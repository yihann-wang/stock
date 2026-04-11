"""可转债数据获取模块 - 东方财富 push2 (主) + 腾讯API (备)"""

import logging
import time
from datetime import datetime

import requests

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


# ============================================================
# 公共入口
# ============================================================

def get_cb_list() -> list[dict]:
    """
    获取全市场可转债实时数据。

    主数据源: 东方财富 push2 (精确转股价值 + 溢价率)
    备用数据源: 东方财富数据中心 (转股价) + 腾讯行情 (实时价格) → 自算溢价率

    返回:
      [{bond_code, bond_name, bond_price, stock_code, stock_name,
        convert_price, convert_value, premium_rate(%), volume(万元)}, ...]
    """
    # 主数据源
    for attempt in range(2):
        try:
            result = _fetch_eastmoney_push()
            if result:
                logger.info(f"可转债数据获取成功(push2): {len(result)} 只")
                return result
        except Exception as e:
            logger.warning(f"push2 获取失败 (尝试 {attempt + 1}/2): {e}")
            if attempt < 1:
                time.sleep(2)

    # 备用数据源
    try:
        result = _fetch_via_tencent()
        if result:
            logger.info(f"可转债数据获取成功(腾讯备用): {len(result)} 只")
            return result
    except Exception as e:
        logger.warning(f"腾讯备用获取失败: {e}")

    logger.error("可转债数据获取最终失败")
    return []


# ============================================================
# 主数据源: 东方财富 push2
# ============================================================

def _fetch_eastmoney_push() -> list[dict]:
    """
    东方财富 push2 行情 API (实时)。

    字段映射 (已验证):
      f2=转债现价  f6=成交额(元)  f12=转债代码  f14=转债名称
      f227=转股价值  f229=转股价  f232=正股代码  f234=正股名称
      f238=转股溢价率(%)

    按溢价率升序，取前 100 只（覆盖所有负溢价机会）。
    """
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1, "pz": 100, "po": 0, "np": 1,
        "fltt": 2, "invt": 2,
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "fid": "f238",
        "fs": "b:MK0354",
        "fields": "f2,f6,f12,f14,f227,f229,f232,f234,f238",
    }

    resp = _new_session().get(url, params=params, timeout=15)
    data = resp.json()

    items = (data.get("data") or {}).get("diff")
    if not items:
        return []

    results = []
    for item in items:
        bond_price = _safe_float(item.get("f2"))
        convert_value = _safe_float(item.get("f227"))
        if bond_price <= 0 or convert_value <= 0:
            continue

        results.append({
            "bond_code": str(item.get("f12", "")),
            "bond_name": str(item.get("f14", "")),
            "bond_price": bond_price,
            "stock_code": str(item.get("f232", "")),
            "stock_name": str(item.get("f234", "")),
            "convert_price": _safe_float(item.get("f229")),
            "convert_value": round(convert_value, 3),
            "premium_rate": _safe_float(item.get("f238")),
            "volume": round(_safe_float(item.get("f6")) / 10000, 2),
        })

    return results


# ============================================================
# 备用数据源: 数据中心(转股价) + 腾讯(实时价格)
# ============================================================

def _fetch_via_tencent() -> list[dict]:
    """
    备用方案: 数据中心获取转债列表+转股价 → 腾讯获取实时价格 → 自算溢价率。

    注: 转股价使用发行时初始值，若公司有下修转股价则可能偏高，
    导致计算的溢价率偏高(实际折价可能更深)。作为备用扫描仍然有效。
    """
    session = _new_session()

    # 1) 获取活跃转债列表 + 转股价
    active_bonds = _get_active_bond_refs(session)
    if not active_bonds:
        return []
    logger.info(f"数据中心获取活跃转债: {len(active_bonds)} 只")

    # 2) 批量获取转债实时价格 (腾讯)
    bond_symbols = {
        b["bond_code"]: _secucode_to_tencent(b["secucode"])
        for b in active_bonds
    }
    bond_quotes = _batch_tencent_quotes(session, list(bond_symbols.values()))
    logger.info(f"腾讯获取转债价格: {len(bond_quotes)} 只")

    # 3) 批量获取正股实时价格 (腾讯)
    stock_codes = list({b["stock_code"] for b in active_bonds})
    stock_symbols = {c: _stock_code_to_tencent(c) for c in stock_codes}
    stock_quotes = _batch_tencent_quotes(session, list(stock_symbols.values()))
    logger.info(f"腾讯获取正股价格: {len(stock_quotes)} 只")

    # 4) 合并计算
    results = []
    for b in active_bonds:
        bc = b["bond_code"]
        sc = b["stock_code"]
        cp = b["convert_price"]

        bp_info = bond_quotes.get(bc)
        sp_info = stock_quotes.get(sc)
        if not bp_info or not sp_info or cp <= 0:
            continue

        bond_price = bp_info["price"]
        stock_price = sp_info["price"]
        convert_value = stock_price * (100 / cp)
        premium_rate = (bond_price - convert_value) / convert_value * 100

        results.append({
            "bond_code": bc,
            "bond_name": b["bond_name"],
            "bond_price": bond_price,
            "stock_code": sc,
            "stock_name": b["stock_name"],
            "convert_price": cp,
            "convert_value": round(convert_value, 3),
            "premium_rate": round(premium_rate, 2),
            "volume": bp_info.get("volume", 0),
        })

    return results


def _get_active_bond_refs(session: requests.Session) -> list[dict]:
    """从东方财富数据中心获取活跃可转债列表（含转股价、正股代码）"""
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    all_items = []

    for page in range(1, 5):
        params = {
            "reportName": "RPT_BOND_CB_LIST",
            "columns": (
                "SECURITY_CODE,SECUCODE,SECURITY_NAME_ABBR,"
                "CONVERT_STOCK_CODE,SECURITY_SHORT_NAME,"
                "INITIAL_TRANSFER_PRICE,LISTING_DATE,"
                "DELIST_DATE,TRANSFER_START_DATE"
            ),
            "pageSize": 500,
            "pageNumber": page,
            "sortColumns": "LISTING_DATE",
            "sortTypes": -1,
            "source": "WEB",
            "client": "WEB",
        }
        resp = session.get(url, params=params, timeout=15)
        data = resp.json()
        items = (data.get("result") or {}).get("data")
        if not items:
            break
        all_items.extend(items)
        total_pages = (data.get("result") or {}).get("pages", 1)
        if page >= total_pages:
            break

    today = datetime.now().strftime("%Y-%m-%d")
    active = []
    for item in all_items:
        # 跳过已退市
        if item.get("DELIST_DATE"):
            continue
        # 跳过未上市
        if not item.get("LISTING_DATE"):
            continue
        # 跳过转股未开始
        ts = (item.get("TRANSFER_START_DATE") or "")[:10]
        if ts and ts > today:
            continue
        cp = item.get("INITIAL_TRANSFER_PRICE")
        if not cp or cp <= 0:
            continue

        active.append({
            "bond_code": item["SECURITY_CODE"],
            "secucode": item["SECUCODE"],
            "bond_name": item["SECURITY_NAME_ABBR"],
            "stock_code": item["CONVERT_STOCK_CODE"],
            "stock_name": item["SECURITY_SHORT_NAME"],
            "convert_price": cp,
        })

    return active


def _batch_tencent_quotes(
    session: requests.Session, symbols: list[str], batch_size: int = 80,
) -> dict[str, dict]:
    """
    批量从腾讯行情获取实时价格。

    返回: {证券代码: {"price": float, "volume": float(万元)}}
    """
    quotes: dict[str, dict] = {}

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]
        url = f"http://qt.gtimg.cn/q={','.join(batch)}"
        try:
            resp = session.get(url, timeout=10)
        except Exception:
            continue

        for line in resp.text.strip().split(";"):
            if '="' not in line:
                continue
            data_str = line.split('="')[1].rstrip('"')
            fields = data_str.split("~")
            if len(fields) < 38:
                continue
            code = fields[2]
            price = _safe_float(fields[3])
            volume = _safe_float(fields[37])  # 成交额(万元)
            if price > 0:
                quotes[code] = {"price": price, "volume": volume}

    return quotes


def _secucode_to_tencent(secucode: str) -> str:
    """SECUCODE (如 128119.SZ) → 腾讯格式 (sz128119)"""
    code, market = secucode.split(".")
    return ("sh" if market == "SH" else "sz") + code


def _stock_code_to_tencent(stock_code: str) -> str:
    """股票代码 → 腾讯格式"""
    if stock_code.startswith(("6", "9")):
        return f"sh{stock_code}"
    return f"sz{stock_code}"
