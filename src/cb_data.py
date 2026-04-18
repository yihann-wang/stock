"""可转债数据获取模块 - 东方财富数据中心 + 实时行情(quoteColumns)一次请求获取全部数据"""

import logging
import time
from datetime import datetime

import requests

from .config import load_config

logger = logging.getLogger(__name__)


def _new_session() -> requests.Session:
    """创建无代理 Session"""
    s = requests.Session()
    s.trust_env = False
    s.proxies = {"http": None, "https": None}
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    return s


def _safe_float(val, default=0.0) -> float:
    """安全转换浮点数，处理 None / '-' / 空字符串"""
    if val is None or val == "-" or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def get_cb_list() -> list[dict]:
    """
    获取全市场可转债实时数据。

    使用东方财富数据中心 API + quoteColumns 实时行情叠加，
    一次请求同时获取转债列表、当前转股价、转股价值、转股溢价率。

    返回:
      [{bond_code, bond_name, bond_price, stock_code, stock_name,
        convert_price, convert_value, premium_rate(%), volume(万元)}, ...]
    """
    last_err = None
    for attempt in range(3):
        try:
            session = _new_session()
            result = _fetch_datacenter_realtime(session)
            if result:
                logger.info(f"可转债数据获取成功: {len(result)} 只")
                return result
        except Exception as e:
            last_err = e
            logger.warning(f"可转债数据获取失败 (尝试 {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(3)

    logger.error(f"可转债数据获取最终失败: {last_err}")
    # 钉钉推送错误通知
    try:
        from .notifier import notify_error
        notify_error(
            stage="可转债数据获取",
            error="东方财富数据中心API 3次重试均失败",
            detail=str(last_err),
        )
    except Exception as e:
        logger.warning(f"错误通知发送失败: {e}")
    return []


def _get_manual_blacklist() -> set[str]:
    """手动配置的黑名单（兜底用，正常情况留空）"""
    try:
        cfg = load_config().get("cb_blacklist", {})
        return set(str(c) for c in cfg.get("bond_codes", []))
    except Exception:
        return set()


def _get_announced_redeem_codes() -> set[str]:
    """
    自动从集思录获取「已公告强赎」的可转债代码。

    这些转债公司已公告将提前赎回，最终未及时转股/卖出的会按面值赎回，
    高价转债买入会亏，必须从所有套利策略中排除。
    """
    try:
        import akshare as ak
        df = ak.bond_cb_redeem_jsl()
        if df is None or df.empty:
            return set()
        # "强赎状态" 包含 "已公告" 字样的
        mask = df["强赎状态"].astype(str).str.contains("已公告", na=False)
        codes = set(df[mask]["代码"].astype(str).tolist())
        if codes:
            logger.info(f"集思录已公告强赎转债: {len(codes)} 只 → 自动过滤")
        return codes
    except Exception as e:
        logger.warning(f"获取已公告强赎列表失败: {e}")
        return set()


def _get_blacklist() -> set[str]:
    """合并的可转债黑名单：手动 + 自动（已公告强赎）"""
    return _get_manual_blacklist() | _get_announced_redeem_codes()


def _get_name_overrides() -> dict:
    """从 config.yml 加载名称纠错映射 {债码: [转债名, 正股名]}"""
    try:
        return load_config().get("cb_name_overrides", {}) or {}
    except Exception:
        return {}


def _fetch_datacenter_realtime(session: requests.Session) -> list[dict]:
    """
    东方财富数据中心 + 实时行情叠加。

    quoteColumns 实时字段 (type=10 为债券行情):
      f2  = 转债现价
      f6  = 成交额(元)
      f235 = 当前转股价
      f236 = 转股价值
      f237 = 转股溢价率(%)
    """
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    today = datetime.now().strftime("%Y-%m-%d")
    blacklist = _get_blacklist()
    name_overrides = _get_name_overrides()
    all_results = []

    for page in range(1, 5):
        params = {
            "reportName": "RPT_BOND_CB_LIST",
            "columns": (
                "SECURITY_CODE,SECURITY_NAME_ABBR,"
                "CONVERT_STOCK_CODE,SECURITY_SHORT_NAME,"
                "LISTING_DATE,DELIST_DATE,TRANSFER_START_DATE,"
                "EXPIRE_DATE"
            ),
            "quoteColumns": (
                "f2~10~SECURITY_CODE~BOND_PRICE,"
                "f6~10~SECURITY_CODE~VOLUME,"
                "f235~10~SECURITY_CODE~CONVERT_PRICE,"
                "f236~10~SECURITY_CODE~CONVERT_VALUE,"
                "f237~10~SECURITY_CODE~PREMIUM_RATE"
            ),
            "pageSize": 500,
            "pageNumber": page,
            "sortColumns": "PREMIUM_RATE",
            "sortTypes": 1,
            "source": "WEB",
            "client": "WEB",
        }

        # 单页超时45秒（4页累计可能慢）
        resp = session.get(url, params=params, timeout=45)
        data = resp.json()

        if not data.get("success"):
            break

        items = (data.get("result") or {}).get("data")
        if not items:
            break

        for item in items:
            # 过滤: 已退市 / 未上市 / 转股未开始 / 手动黑名单
            bond_code = str(item.get("SECURITY_CODE", ""))
            if bond_code in blacklist:
                continue
            if item.get("DELIST_DATE"):
                continue
            if not item.get("LISTING_DATE"):
                continue
            ts = (item.get("TRANSFER_START_DATE") or "")[:10]
            if ts and ts > today:
                continue
            # 注: 不能用 EXECUTE_END_DATE 判断退市，该字段是历史强赎触发记录,
            # 很多公司触发后最终未行使赎回权，转债仍在正常交易

            bond_price = _safe_float(item.get("BOND_PRICE"))
            convert_value = _safe_float(item.get("CONVERT_VALUE"))
            convert_price = _safe_float(item.get("CONVERT_PRICE"))
            premium_rate = _safe_float(item.get("PREMIUM_RATE"))
            volume = _safe_float(item.get("VOLUME"))

            if bond_price <= 0 or convert_value <= 0:
                continue

            # 名称纠错（东方财富数据偶有错误，按 config.cb_name_overrides 覆盖）
            override = name_overrides.get(bond_code) or []
            bond_name = override[0] if len(override) > 0 else item.get("SECURITY_NAME_ABBR", "")
            stock_name = override[1] if len(override) > 1 else item.get("SECURITY_SHORT_NAME", "")

            all_results.append({
                "bond_code": item.get("SECURITY_CODE", ""),
                "bond_name": bond_name,
                "bond_price": bond_price,
                "stock_code": item.get("CONVERT_STOCK_CODE", ""),
                "stock_name": stock_name,
                "convert_price": convert_price,
                "convert_value": round(convert_value, 3),
                "premium_rate": round(premium_rate, 2),
                "volume": round(volume / 10000, 2) if volume > 0 else 0,
                "expire_date": (item.get("EXPIRE_DATE") or "")[:10],
            })

        total_pages = (data.get("result") or {}).get("pages", 1)
        if page >= total_pages:
            break

    return all_results
