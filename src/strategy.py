"""套利策略模块 - 价差/年化收益计算 + 部分要约调整 + 信号判定"""

import logging
from dataclasses import dataclass
from datetime import datetime

from .config import load_config
from .price import get_realtime_price

logger = logging.getLogger(__name__)


@dataclass
class ArbitrageResult:
    """套利计算结果"""
    stock_code: str
    stock_name: str
    current_price: float
    offer_price: float
    spread: float            # 价差(元)
    spread_pct: float        # 价差收益率(%)
    annualized_pct: float    # 年化收益率(%)
    days_left: int           # 剩余天数
    offer_end: str           # 截止日期
    offer_type: str          # full / partial
    condition: str           # none / min_accept
    partial_pct: float       # 收购比例上限(%)
    daily_volume: float      # 日成交额(万元)
    # 部分要约调整后收益
    adjusted_spread_pct: float | None = None
    adjusted_annualized_pct: float | None = None


@dataclass
class Signal:
    """信号"""
    signal_type: str         # spread / deadline / negative
    result: ArbitrageResult
    message: str


def calculate_arbitrage(offer: dict) -> ArbitrageResult | None:
    """计算单个要约的套利指标"""
    stock_code = offer.get("stock_code", "")
    offer_end = offer.get("offer_end", "")

    if not stock_code or not offer_end:
        return None

    # 计算剩余天数
    try:
        end_date = datetime.strptime(offer_end, "%Y-%m-%d").date()
        days_left = (end_date - datetime.now().date()).days
    except ValueError:
        logger.error(f"截止日期格式无效: {offer_end}")
        return None

    if days_left <= 0:
        return None

    # 获取实时股价
    price_info = get_realtime_price(stock_code)
    if not price_info:
        return None

    current_price = price_info["current_price"]
    offer_price = offer.get("offer_price", 0)

    if current_price <= 0 or offer_price <= 0:
        return None

    # 基础收益
    spread = offer_price - current_price
    spread_pct = spread / current_price * 100
    annualized_pct = spread_pct * (365 / days_left)

    result = ArbitrageResult(
        stock_code=stock_code,
        stock_name=offer.get("stock_name", ""),
        current_price=current_price,
        offer_price=offer_price,
        spread=round(spread, 4),
        spread_pct=round(spread_pct, 2),
        annualized_pct=round(annualized_pct, 2),
        days_left=days_left,
        offer_end=offer_end,
        offer_type=offer.get("type", "full"),
        condition=offer.get("condition", "none"),
        partial_pct=offer.get("partial_pct", 100),
        daily_volume=price_info.get("daily_volume", 0),
    )

    # 部分要约风险调整
    if result.offer_type == "partial" and result.partial_pct < 100:
        result.adjusted_spread_pct = round(
            spread_pct * (result.partial_pct / 100), 2
        )
        result.adjusted_annualized_pct = round(
            result.adjusted_spread_pct * (365 / days_left), 2
        )

    return result


def evaluate_signals(offers: list[dict]) -> list[Signal]:
    """对所有活跃要约评估信号"""
    cfg = load_config().get("thresholds", {})
    min_spread_pct = cfg.get("min_spread_pct", 3.0)
    min_annualized_pct = cfg.get("min_annualized_pct", 30.0)
    warn_days_left = cfg.get("warn_days_left", 5)
    min_daily_volume = cfg.get("min_daily_volume", 500)

    signals = []

    for offer in offers:
        result = calculate_arbitrage(offer)
        if not result:
            continue

        # 负价差警告（不受成交额限制）
        if result.spread < 0:
            signals.append(Signal(
                signal_type="negative",
                result=result,
                message=(
                    f"{result.stock_name}({result.stock_code}) 出现负价差 "
                    f"{result.spread_pct:.2f}%，当前价 {result.current_price} "
                    f"> 要约价 {result.offer_price}"
                ),
            ))
            continue

        # 截止日提醒（不受成交额限制）
        if result.days_left <= warn_days_left:
            signals.append(Signal(
                signal_type="deadline",
                result=result,
                message=(
                    f"{result.stock_name}({result.stock_code}) 要约即将截止，"
                    f"剩余 {result.days_left} 天 ({result.offer_end})"
                ),
            ))

        # 套利信号（需达到阈值 + 成交额要求）
        if (result.spread_pct >= min_spread_pct
                and result.annualized_pct >= min_annualized_pct
                and result.daily_volume >= min_daily_volume):
            signals.append(Signal(
                signal_type="spread",
                result=result,
                message=(
                    f"{result.stock_name}({result.stock_code}) 套利机会: "
                    f"价差 {result.spread_pct:.2f}% 年化 {result.annualized_pct:.2f}%"
                ),
            ))
        elif (result.spread_pct >= min_spread_pct
              and result.annualized_pct >= min_annualized_pct
              and result.daily_volume < min_daily_volume):
            logger.info(
                f"{result.stock_name} 满足收益阈值但成交额不足: "
                f"{result.daily_volume:.0f}万 < {min_daily_volume}万"
            )

    return signals
