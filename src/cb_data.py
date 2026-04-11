"""可转债数据获取模块 - 数据中心(转债列表+转股价) + 腾讯API(实时价格) → 自算溢价率"""

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


def get_cb_list() -> list[dict]:
    """
    获取全市场可转债实时数据。

    流程:
      1. 东方财富数据中心 → 活跃转债列表 + 转股价 + 正股代码
      2. 腾讯行情 API → 转债实时价格 + 正股实时价格
      3. 自行计算转股价值和转股溢价率

    返回:
      [{bond_code, bond_name, bond_price, stock_code, stock_name,
        convert_price, convert_value, premium_rate(%), volume(万元)}, ...]
    """
    try:
        session = _new_session()

        # 1) 活跃转债列表 + 转股价
        active_bonds = _get_active_bond_refs(session)
        if not active_bonds:
            logger.error("数据中心获取转债列表失败")
            return []
        logger.info(f"数据中心获取活跃转债: {len(active_bonds)} 只")

        # 2) 批量获取转债实时价格
        bond_symbols = {
            b["bond_code"]: _secucode_to_tencent(b["secucode"])
            for b in active_bonds
        }
        bond_quotes = _batch_tencent_quotes(session, list(bond_symbols.values()))
        logger.info(f"腾讯获取转债价格: {len(bond_quotes)} 只")

        # 3) 批量获取正股实时价格
        stock_codes = list({b["stock_code"] for b in active_bonds})
        stock_symbols = {c: _stock_code_to_tencent(c) for c in stock_codes}
        stock_quotes = _batch_tencent_quotes(session, list(stock_symbols.values()))
        logger.info(f"腾讯获取正股价格: {len(stock_quotes)} 只")

        # 4) 自行计算转股价值和溢价率
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

        logger.info(f"可转债数据获取成功: {len(results)} 只")
        return results

    except Exception as e:
        logger.error(f"可转债数据获取失败: {e}")
        return []


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
        if item.get("DELIST_DATE"):
            continue
        if not item.get("LISTING_DATE"):
            continue
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
            volume = _safe_float(fields[37])
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
