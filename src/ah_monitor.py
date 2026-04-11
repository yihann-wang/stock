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
    premium_rate: float       # AH溢价率(%), >0 表示A股贵


def scan_ah_premium() -> list[AHPremiumResult]:
    """
    扫描AH股溢价率，筛选极端偏离的标的。

    数据源: akshare (stock_a_ah_tx)
    溢价率 = (A股价格 / H股价格 / 汇率 - 1) × 100%
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
        df = ak.stock_a_ah_tx()
    except Exception as e:
        logger.warning(f"AH股数据获取失败: {e}")
        return []

    if df is None or df.empty:
        logger.warning("AH股数据为空")
        return []

    logger.info(f"获取到 {len(df)} 只AH股数据")

    # 尝试匹配列名（akshare 不同版本列名可能不同）
    col_map = {}
    for col in df.columns:
        cl = col.lower()
        if "a股代码" in col or "代码" in col and "h" not in cl:
            col_map["code"] = col
        elif "名称" in col or "简称" in col:
            col_map["name"] = col
        elif "a股价" in col or ("最新价" in col and "h" not in cl):
            col_map["a_price"] = col
        elif "h股价" in col:
            col_map["h_price"] = col
        elif "溢价" in col or "比价" in col:
            col_map["premium"] = col

    if "premium" not in col_map:
        logger.warning(f"AH股数据列名无法匹配, 可用列: {list(df.columns)}")
        return []

    results = []
    for _, row in df.iterrows():
        try:
            premium = float(row.get(col_map.get("premium", ""), 0))
        except (ValueError, TypeError):
            continue

        if premium > max_premium or premium < min_premium:
            a_price = 0
            h_price = 0
            try:
                a_price = float(row.get(col_map.get("a_price", ""), 0))
                h_price = float(row.get(col_map.get("h_price", ""), 0))
            except (ValueError, TypeError):
                pass

            results.append(AHPremiumResult(
                stock_code=str(row.get(col_map.get("code", ""), "")),
                stock_name=str(row.get(col_map.get("name", ""), "")),
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
