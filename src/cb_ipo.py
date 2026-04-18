"""可转债打新模块 - 扫描今日可申购+即将申购的可转债"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import load_config

logger = logging.getLogger(__name__)


@dataclass
class CBIPOResult:
    """可转债打新机会"""
    bond_code: str              # 债券代码
    bond_name: str              # 债券简称
    apply_code: str             # 申购代码
    apply_date: str             # 申购日期 YYYY-MM-DD
    stock_code: str             # 正股代码
    stock_name: str             # 正股简称
    stock_price: float          # 当前正股价
    convert_price: float        # 转股价
    convert_value: float        # 转股价值
    issue_size: float           # 发行规模(亿元)
    rating: str                 # 信用评级
    listing_date: str           # 预计上市日
    days_from_today: int        # 距今天的天数(0=今天, 1=明天, -1=昨天)


def scan_cb_ipo() -> list[CBIPOResult]:
    """
    扫描可转债打新机会。

    策略: 推送"今日 / 未来N天内"可以申购的可转债。
    所有打新都是免费午餐，100%中签率(按配售规则)，上市后平均涨幅 10-20%。
    """
    cfg = load_config().get("cb_ipo", {})
    if not cfg.get("enabled", True):
        return []

    days_ahead = cfg.get("days_ahead", 5)   # 提前N天提醒
    days_back = cfg.get("days_back", 0)     # 向后N天(0=仅今天及未来)

    try:
        import akshare as ak
    except ImportError:
        logger.warning("akshare 未安装，跳过可转债打新扫描")
        return []

    import time as _t
    df = None
    last_err = None
    for attempt in range(3):
        try:
            df = ak.bond_zh_cov()
            if df is not None and not df.empty:
                break
        except Exception as e:
            last_err = e
            logger.warning(f"可转债打新数据获取失败 (尝试 {attempt+1}/3): {e}")
            if attempt < 2:
                _t.sleep(2)
    if df is None or df.empty:
        try:
            from .notifier import notify_error
            notify_error(
                stage="可转债打新数据获取",
                error="akshare bond_zh_cov 3次重试均失败",
                detail=str(last_err) if last_err else "返回空数据",
            )
        except Exception:
            pass
        return []

    # 用列名查找（稳健性优于硬编码下标）
    expected = {
        "bond_code": "债券代码",
        "bond_name": "债券简称",
        "apply_date": "申购日期",
        "apply_code": "申购代码",
        "stock_code": "正股代码",
        "stock_name": "正股简称",
        "stock_price": "正股价",
        "convert_price": "转股价",
        "convert_value": "转股价值",
        "size": "发行规模",
        "rating": "信用评级",
        "listing": "上市时间",
    }
    col_map = {}
    missing = []
    for key, col_name in expected.items():
        if col_name in df.columns:
            col_map[key] = col_name
        else:
            missing.append(col_name)

    if missing:
        logger.warning(f"bond_zh_cov 列名缺失: {missing}，akshare 可能已变更数据结构")
        return []

    today = datetime.now().date()
    min_date = today - timedelta(days=days_back)
    max_date = today + timedelta(days=days_ahead)

    results = []
    for _, row in df.iterrows():
        apply_date_str = str(row[col_map["apply_date"]]).strip()
        if not apply_date_str or apply_date_str == "nan":
            continue

        try:
            apply_date = datetime.strptime(apply_date_str[:10], "%Y-%m-%d").date()
        except ValueError:
            continue

        if apply_date < min_date or apply_date > max_date:
            continue

        days_diff = (apply_date - today).days

        try:
            stock_price = float(row[col_map["stock_price"]])
        except (ValueError, TypeError):
            stock_price = 0

        try:
            convert_price = float(row[col_map["convert_price"]])
        except (ValueError, TypeError):
            convert_price = 0

        try:
            convert_value = float(row[col_map["convert_value"]])
        except (ValueError, TypeError):
            convert_value = 0

        try:
            size = float(row[col_map["size"]])
        except (ValueError, TypeError):
            size = 0

        results.append(CBIPOResult(
            bond_code=str(row[col_map["bond_code"]]),
            bond_name=str(row[col_map["bond_name"]]),
            apply_code=str(row[col_map["apply_code"]]),
            apply_date=apply_date_str[:10],
            stock_code=str(row[col_map["stock_code"]]),
            stock_name=str(row[col_map["stock_name"]]),
            stock_price=stock_price,
            convert_price=convert_price,
            convert_value=round(convert_value, 2),
            issue_size=size,
            rating=str(row[col_map["rating"]]),
            listing_date=str(row[col_map["listing"]]),
            days_from_today=days_diff,
        ))

    results.sort(key=lambda x: x.days_from_today)

    if results:
        logger.info(f"发现 {len(results)} 只可打新可转债")
    return results
