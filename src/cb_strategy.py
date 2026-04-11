"""可转债套利策略模块 - 转股溢价率扫描 + 负溢价套利信号"""

import logging
from dataclasses import dataclass

from .cb_data import get_cb_list
from .config import load_config

logger = logging.getLogger(__name__)


@dataclass
class CBArbitrageResult:
    """可转债套利计算结果"""
    bond_code: str
    bond_name: str
    bond_price: float          # 转债现价(元)
    stock_code: str
    stock_name: str
    convert_price: float       # 转股价(元)
    convert_value: float       # 转股价值(元)
    premium_rate: float        # 转股溢价率(%), 负值=折价=套利机会
    volume: float              # 成交额(万元)
    profit_per_ten: float      # 每10张预期收益(元)


def scan_cb_arbitrage() -> list[CBArbitrageResult]:
    """
    扫描全市场可转债，筛选负溢价(折价)套利机会。

    套利逻辑:
      转股价值 > 转债价格 → 买入转债 → 当日转股 → 次日卖出正股 → 赚取差价

    返回按溢价率升序排列的结果(最深折价在前)。
    """
    cfg = load_config().get("cb_arbitrage", {})
    if not cfg.get("enabled", True):
        logger.info("可转债套利扫描已禁用")
        return []

    max_premium_rate = cfg.get("max_premium_rate", -0.5)
    min_volume = cfg.get("min_volume", 1000)
    min_bond_price = cfg.get("min_bond_price", 90)
    max_bond_price = cfg.get("max_bond_price", 200)
    max_results = cfg.get("max_results", 10)

    cb_list = get_cb_list()
    if not cb_list:
        logger.warning("未获取到可转债数据")
        return []

    logger.info(f"获取到 {len(cb_list)} 只可转债，筛选负溢价机会...")

    results = []
    for cb in cb_list:
        premium_rate = cb.get("premium_rate", 0)
        bond_price = cb.get("bond_price", 0)
        convert_value = cb.get("convert_value", 0)
        volume = cb.get("volume", 0)

        if premium_rate > max_premium_rate:
            continue
        if volume < min_volume:
            continue
        if bond_price < min_bond_price or bond_price > max_bond_price:
            continue
        if convert_value <= 0:
            continue

        # 每10张(面值1000元)的预期收益
        profit_per_ten = round((convert_value - bond_price) * 10, 2)

        results.append(CBArbitrageResult(
            bond_code=cb.get("bond_code", ""),
            bond_name=cb.get("bond_name", ""),
            bond_price=bond_price,
            stock_code=cb.get("stock_code", ""),
            stock_name=cb.get("stock_name", ""),
            convert_price=cb.get("convert_price", 0),
            convert_value=round(convert_value, 2),
            premium_rate=round(premium_rate, 2),
            volume=volume,
            profit_per_ten=profit_per_ten,
        ))

    results.sort(key=lambda x: x.premium_rate)
    results = results[:max_results]

    if results:
        logger.info(f"发现 {len(results)} 只负溢价可转债:")
        for r in results:
            logger.info(
                f"  {r.bond_name}({r.bond_code}) 溢价率={r.premium_rate:.2f}% "
                f"转债价={r.bond_price:.2f} 转股价值={r.convert_value:.2f} "
                f"成交额={r.volume:.0f}万"
            )
    else:
        logger.info("当前无满足条件的负溢价套利机会")

    return results
