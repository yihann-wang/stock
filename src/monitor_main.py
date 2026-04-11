"""价差监控入口 - 要约收购监控 + 可转债套利扫描"""

import logging
import os
import sys

from .cb_strategy import scan_cb_arbitrage
from .config import get_active_offers
from .notifier import (
    notify_cb_arbitrage,
    notify_deadline_warning,
    notify_negative_spread,
    notify_spread_signal,
)
from .price import is_trading_day
from .strategy import evaluate_signals

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run():
    logger.info("=== 价差监控流程开始 ===")

    trading_day = is_trading_day()

    # ===== 要约收购监控（仅交易日）=====
    if trading_day:
        logger.info("--- 要约收购监控 ---")
        active_offers = get_active_offers()
        if active_offers:
            logger.info(f"当前活跃要约: {len(active_offers)} 个")
            for offer in active_offers:
                logger.info(
                    f"  - {offer.get('stock_name', '')}({offer.get('stock_code', '')}) "
                    f"要约价 {offer.get('offer_price', 'N/A')} 截止 {offer.get('offer_end', 'N/A')}"
                )

            signals = evaluate_signals(active_offers)
            if signals:
                logger.info(f"产生 {len(signals)} 个要约收购信号")
                for signal in signals:
                    logger.info(f"信号: [{signal.signal_type}] {signal.message}")
                    if signal.signal_type == "spread":
                        notify_spread_signal(signal.result)
                    elif signal.signal_type == "deadline":
                        notify_deadline_warning(signal.result)
                    elif signal.signal_type == "negative":
                        notify_negative_spread(signal.result)
            else:
                logger.info("无要约收购达标信号")
        else:
            logger.info("无活跃要约")
    else:
        logger.info("今日非交易日，跳过要约收购监控")

    # ===== 可转债套利扫描（不受交易日限制，数据来自收盘快照）=====
    logger.info("--- 可转债套利扫描 ---")
    cb_results = scan_cb_arbitrage()
    if cb_results:
        logger.info(f"发现 {len(cb_results)} 只可转债套利机会，发送通知")
        notify_cb_arbitrage(cb_results)
    else:
        logger.info("无可转债套利机会")

    logger.info("=== 价差监控流程结束 ===")


if __name__ == "__main__":
    run()
