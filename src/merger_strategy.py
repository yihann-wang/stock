"""吸收合并套利策略 - 换股比例套利计算"""

import logging
from dataclasses import dataclass
from datetime import datetime

from .config import load_config
from .price import get_realtime_price

logger = logging.getLogger(__name__)


@dataclass
class MergerArbitrageResult:
    """吸收合并套利结果"""
    target_code: str              # 被合并方代码
    target_name: str
    target_price: float           # 被合并方现价
    acquirer_code: str            # 存续公司代码
    acquirer_name: str
    acquirer_price: float         # 存续公司现价
    exchange_ratio: float         # 换股比例
    theoretical_value: float      # 理论价值 = 存续价 × 换股比例
    spread: float                 # 价差(元)
    spread_pct: float             # 价差收益率(%)
    annualized_pct: float         # 年化收益率(%)
    days_left: int                # 距实施日天数
    expected_date: str
    target_volume: float          # 被合并方日成交额(万)


@dataclass
class MergerSignal:
    signal_type: str              # merger_spread
    result: MergerArbitrageResult
    message: str


def calculate_merger_arbitrage(merger: dict) -> MergerArbitrageResult | None:
    """计算单个吸收合并的套利指标"""
    target_code = merger.get("target_code", "")
    acquirer_code = merger.get("acquirer_code", "")
    ratio = merger.get("exchange_ratio", 0)
    expected_date = merger.get("expected_date", "")

    if not target_code or not acquirer_code or ratio <= 0:
        return None

    # 计算剩余天数
    try:
        exp_date = datetime.strptime(expected_date, "%Y-%m-%d").date()
        days_left = (exp_date - datetime.now().date()).days
    except ValueError:
        return None

    if days_left <= 0:
        return None

    # 获取两只股票实时价格
    target_info = get_realtime_price(target_code)
    acquirer_info = get_realtime_price(acquirer_code)
    if not target_info or not acquirer_info:
        return None

    target_price = target_info["current_price"]
    acquirer_price = acquirer_info["current_price"]

    if target_price <= 0 or acquirer_price <= 0:
        return None

    # 理论价值 = 存续方价格 × 换股比例
    theoretical_value = acquirer_price * ratio
    spread = theoretical_value - target_price
    spread_pct = spread / target_price * 100
    annualized_pct = spread_pct * (365 / days_left) if days_left > 0 else 0

    return MergerArbitrageResult(
        target_code=target_code,
        target_name=merger.get("target_name", ""),
        target_price=target_price,
        acquirer_code=acquirer_code,
        acquirer_name=merger.get("acquirer_name", ""),
        acquirer_price=acquirer_price,
        exchange_ratio=ratio,
        theoretical_value=round(theoretical_value, 3),
        spread=round(spread, 3),
        spread_pct=round(spread_pct, 2),
        annualized_pct=round(annualized_pct, 2),
        days_left=days_left,
        expected_date=expected_date,
        target_volume=target_info.get("daily_volume", 0),
    )


def evaluate_merger_signals(mergers: list[dict]) -> list[MergerSignal]:
    """对活跃合并评估套利信号"""
    cfg = load_config().get("thresholds", {})
    min_spread_pct = cfg.get("min_spread_pct", 3.0)
    min_annualized_pct = cfg.get("min_annualized_pct", 30.0)
    min_daily_volume = cfg.get("min_daily_volume", 500)

    signals = []
    for m in mergers:
        r = calculate_merger_arbitrage(m)
        if not r:
            continue

        if (r.spread_pct >= min_spread_pct
                and r.annualized_pct >= min_annualized_pct
                and r.target_volume >= min_daily_volume):
            signals.append(MergerSignal(
                signal_type="merger_spread",
                result=r,
                message=(
                    f"{r.target_name}({r.target_code}) 换股套利: "
                    f"价差 {r.spread_pct:.2f}% 年化 {r.annualized_pct:.2f}%"
                ),
            ))

    return signals
