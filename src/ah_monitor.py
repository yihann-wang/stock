"""AH股溢价监控模块 - 筛选AH溢价率极端偏离的标的"""

import logging
from dataclasses import dataclass

from .config import load_config

logger = logging.getLogger(__name__)


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

    max_premium = cfg.get("max_premium", 200)
    min_premium = cfg.get("min_premium", -10)
    max_results = cfg.get("max_results", 10)

    try:
        import akshare as ak
    except ImportError:
        logger.warning("akshare 未安装，跳过AH股溢价监控")
        return []

    try:
        df = ak.stock_zh_ah_spot_em()
    except Exception as e:
        logger.warning(f"AH股数据获取失败: {e}")
        return []

    if df is None or df.empty:
        logger.warning("AH股数据为空")
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

    results.sort(key=lambda x: x.premium_rate)
    results = results[:max_results]

    if results:
        logger.info(f"发现 {len(results)} 只AH股极端溢价")
    else:
        logger.info("无AH股极端溢价")

    return results
