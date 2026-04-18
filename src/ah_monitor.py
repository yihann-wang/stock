"""AH股溢价监控模块 - 筛选AH溢价率极端偏离的标的（每周推送一次）"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import load_config

logger = logging.getLogger(__name__)


def _beijing_weekday() -> int:
    """返回当前北京时间星期几（0=周一 ... 6=周日）"""
    utc_now = datetime.now(timezone.utc)
    beijing_now = utc_now + timedelta(hours=8)
    return beijing_now.weekday()


@dataclass
class AHPremiumResult:
    """AH溢价扫描结果"""
    stock_code: str           # A股代码
    stock_name: str           # 名称
    a_price: float            # A股价格(元)
    h_price: float            # H股价格(港元)
    premium_rate: float       # AH溢价率(%), >0 表示A股贵, <0 表示A股便宜


def scan_ah_premium() -> list[AHPremiumResult]:
    """
    扫描AH股溢价率，筛选极端偏离的标的。

    数据源: akshare stock_zh_ah_spot_em()
    """
    cfg = load_config().get("ah_premium", {})
    if not cfg.get("enabled", True):
        logger.info("AH股溢价监控已禁用")
        return []

    # 仅在指定星期推送（默认周二）
    weekly_day = cfg.get("weekly_day", 1)
    today_wd = _beijing_weekday()
    if today_wd != weekly_day:
        wd_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        logger.info(
            f"今日{wd_names[today_wd]}，AH股溢价只在{wd_names[weekly_day]}推送，跳过"
        )
        return []

    max_premium = cfg.get("max_premium", 200)
    min_premium = cfg.get("min_premium", -10)
    max_results = cfg.get("max_results", 10)

    try:
        import akshare as ak
    except ImportError:
        logger.warning("akshare 未安装，跳过AH股溢价监控")
        return []

    import time as _t
    df = None
    last_err = None
    for attempt in range(3):
        try:
            df = ak.stock_zh_ah_spot_em()
            if df is not None and not df.empty:
                break
        except Exception as e:
            last_err = e
            logger.warning(f"AH股数据获取失败 (尝试 {attempt+1}/3): {e}")
            if attempt < 2:
                _t.sleep(2)
    if df is None or df.empty:
        try:
            from .notifier import notify_error
            notify_error(
                stage="AH股数据获取",
                error="akshare AH股 3次重试均失败",
                detail=str(last_err) if last_err else "返回空数据",
            )
        except Exception:
            pass
        return []

    logger.info(f"获取到 {len(df)} 只AH股数据")

    # 匹配列名（处理编码问题）
    cols = list(df.columns)
    col_name = cols[1]       # 名称
    col_h_code = cols[2]     # H股代码
    col_h_price = cols[3]    # 最新价-HKD
    col_a_code = cols[5]     # A股代码
    col_a_price = cols[6]    # 最新价-RMB
    col_premium = cols[9]    # 溢价(%)

    results = []
    for _, row in df.iterrows():
        try:
            premium = float(row[col_premium])
        except (ValueError, TypeError):
            continue

        if premium > max_premium or premium < min_premium:
            a_price = float(row[col_a_price]) if row[col_a_price] else 0
            h_price = float(row[col_h_price]) if row[col_h_price] else 0

            results.append(AHPremiumResult(
                stock_code=str(row[col_a_code]),
                stock_name=str(row[col_name]),
                a_price=a_price,
                h_price=h_price,
                premium_rate=round(premium, 2),
            ))

    # 按偏离程度排序：负溢价(A股折价)按绝对值大→小，高正溢价按 premium-100 大→小
    # 两端极端偏离都保留
    def _deviation(r: AHPremiumResult) -> float:
        if r.premium_rate < 0:
            return abs(r.premium_rate) + 10000  # 负溢价罕见，优先级更高
        return r.premium_rate  # 高正溢价越大越偏离

    results.sort(key=_deviation, reverse=True)
    results = results[:max_results]

    if results:
        logger.info(f"发现 {len(results)} 只AH股极端溢价")
    else:
        logger.info("无AH股极端溢价")

    return results
