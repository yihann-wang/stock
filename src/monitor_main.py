"""价差监控入口 - 只推送能赚钱的套利机会"""

import logging

from .ah_monitor import scan_ah_premium
from .cb_ipo import scan_cb_ipo
from .cb_strategy import scan_cb_arbitrage, scan_cb_putback
from .config import get_active_mergers, get_active_offers, load_config
from .merger_strategy import evaluate_merger_signals
from .notifier import (
    notify_ah_premium,
    notify_cb_arbitrage,
    notify_cb_ipo,
    notify_cb_putback,
    notify_merger_spread_signal,
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

    # 非交易日（节假日）直接退出，避免推送过期数据
    if not is_trading_day():
        logger.info("今日非交易日，跳过所有策略")
        return

    # ===== 要约收购套利监控 =====
    logger.info("--- 要约收购套利 ---")
    active_offers = get_active_offers()
    if active_offers:
        logger.info(f"当前活跃要约: {len(active_offers)} 个")
        signals = evaluate_signals(active_offers)
        spread_signals = [s for s in signals if s.signal_type == "spread"]
        if spread_signals:
            logger.info(f"产生 {len(spread_signals)} 个套利信号")
            for signal in spread_signals:
                logger.info(f"信号: {signal.message}")
                notify_spread_signal(signal.result)
        else:
            logger.info("无要约收购套利机会")
    else:
        logger.info("无活跃要约")

    # ===== 吸收合并套利监控 =====
    logger.info("--- 吸收合并套利 ---")
    active_mergers = get_active_mergers()
    if active_mergers:
        logger.info(f"当前活跃吸收合并: {len(active_mergers)} 个")
        merger_signals = evaluate_merger_signals(active_mergers)
        if merger_signals:
            logger.info(f"产生 {len(merger_signals)} 个吸收合并套利信号")
            for signal in merger_signals:
                logger.info(f"信号: {signal.message}")
                notify_merger_spread_signal(signal.result)
        else:
            logger.info("无吸收合并套利机会")
    else:
        logger.info("无活跃吸收合并")

    # ===== 可转债套利（共用一份快照）=====
    logger.info("--- 可转债套利 ---")
    from .cb_data import get_cb_list
    cb_list = get_cb_list()
    if cb_list:
        # 转股套利
        cb_results = scan_cb_arbitrage(cb_list)
        if cb_results:
            logger.info(f"发现 {len(cb_results)} 只转股套利机会")
            notify_cb_arbitrage(cb_results)
        else:
            logger.info("无转股套利机会")

        # 回售套利
        putback_results = scan_cb_putback(cb_list)
        if putback_results:
            logger.info(f"发现 {len(putback_results)} 只回售套利机会")
            notify_cb_putback(putback_results)
        else:
            logger.info("无回售套利机会")
    else:
        logger.warning("可转债数据获取失败")

    # ===== 可转债打新 =====
    logger.info("--- 可转债打新 ---")
    ipo_results = scan_cb_ipo()
    if ipo_results:
        logger.info(f"发现 {len(ipo_results)} 只可打新可转债")
        notify_cb_ipo(ipo_results)
    else:
        logger.info("无可打新可转债")

    # ===== AH股溢价（每周一次，默认周二）=====
    logger.info("--- AH股溢价 ---")
    ah_results = scan_ah_premium()
    if ah_results:
        logger.info(f"发现 {len(ah_results)} 只AH股极端溢价")
        notify_ah_premium(ah_results)

    logger.info("=== 价差监控流程结束 ===")


if __name__ == "__main__":
    run()
