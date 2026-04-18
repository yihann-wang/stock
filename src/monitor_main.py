"""价差监控入口 - 只推送能赚钱的套利机会"""

import logging
import traceback

from datetime import datetime, timedelta, timezone

from .ah_monitor import scan_ah_premium
from .cb_ipo import scan_cb_ipo
from .cb_strategy import scan_cb_arbitrage, scan_cb_maturity_play, scan_cb_putback
from .config import (
    get_active_mergers,
    get_active_offers,
    load_config,
    load_known_maturity_plays,
    save_known_maturity_plays,
)
from .merger_strategy import evaluate_merger_signals
from .notifier import (
    notify_ah_premium,
    notify_cb_arbitrage,
    notify_cb_ipo,
    notify_cb_maturity_play,
    notify_cb_putback,
    notify_error,
    notify_merger_spread_signal,
    notify_spread_signal,
)
from .price import is_trading_day
from .strategy import evaluate_signals


def _beijing_weekday() -> int:
    """北京时间星期几 (0=周一, 6=周日)"""
    bj = datetime.now(timezone.utc) + timedelta(hours=8)
    return bj.weekday()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _safe_run(stage_name: str, func):
    """运行单个策略，捕获异常并发推钉钉"""
    try:
        func()
    except Exception as e:
        logger.error(f"[{stage_name}] 异常: {e}")
        logger.error(traceback.format_exc())
        notify_error(stage=stage_name, error=str(e), detail=traceback.format_exc())


def run():
    logger.info("=== 价差监控流程开始 ===")

    # 非交易日（节假日）直接退出，避免推送过期数据
    try:
        if not is_trading_day():
            logger.info("今日非交易日，跳过所有策略")
            return
    except Exception as e:
        logger.error(f"交易日判断失败: {e}")
        notify_error(stage="交易日判断", error=str(e), detail=traceback.format_exc())
        return

    # ===== 要约收购套利监控 =====
    def _tender_offer():
        logger.info("--- 要约收购套利 ---")
        active_offers = get_active_offers()
        if not active_offers:
            logger.info("无活跃要约")
            return
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
    _safe_run("要约收购套利", _tender_offer)

    # ===== 吸收合并套利监控 =====
    def _merger():
        logger.info("--- 吸收合并套利 ---")
        active_mergers = get_active_mergers()
        if not active_mergers:
            logger.info("无活跃吸收合并")
            return
        logger.info(f"当前活跃吸收合并: {len(active_mergers)} 个")
        merger_signals = evaluate_merger_signals(active_mergers)
        if merger_signals:
            logger.info(f"产生 {len(merger_signals)} 个吸收合并套利信号")
            for signal in merger_signals:
                logger.info(f"信号: {signal.message}")
                notify_merger_spread_signal(signal.result)
        else:
            logger.info("无吸收合并套利机会")
    _safe_run("吸收合并套利", _merger)

    # ===== 可转债套利（共用一份快照）=====
    cb_list_holder = {"data": None}

    def _cb_arbitrage():
        logger.info("--- 可转债套利 ---")
        from .cb_data import get_cb_list
        cb_list = get_cb_list()
        cb_list_holder["data"] = cb_list
        if not cb_list:
            logger.warning("可转债数据获取失败，跳过")
            return

        cb_results = scan_cb_arbitrage(cb_list)
        if cb_results:
            logger.info(f"发现 {len(cb_results)} 只转股套利机会")
            notify_cb_arbitrage(cb_results)
        else:
            logger.info("无转股套利机会")

        putback_results = scan_cb_putback(cb_list)
        if putback_results:
            logger.info(f"发现 {len(putback_results)} 只回售套利机会")
            notify_cb_putback(putback_results)
        else:
            logger.info("无回售套利机会")
    _safe_run("可转债套利", _cb_arbitrage)

    # ===== 可转债到期博弈套利（每周二，永久去重）=====
    def _cb_maturity_play():
        cfg = load_config().get("cb_maturity_play", {})
        if not cfg.get("enabled", True):
            return
        weekly_day = cfg.get("weekly_day", 1)
        wd = _beijing_weekday()
        if wd != weekly_day:
            names = ["周一","周二","周三","周四","周五","周六","周日"]
            logger.info(f"--- 到期博弈套利 (今日{names[wd]}, 仅{names[weekly_day]}推送，跳过) ---")
            return

        logger.info("--- 可转债到期博弈套利 ---")
        cb_list = cb_list_holder["data"]
        if not cb_list:
            logger.warning("可转债数据获取失败，跳过到期博弈扫描")
            return

        all_results = scan_cb_maturity_play(cb_list)
        if not all_results:
            logger.info("无到期博弈机会")
            return

        # 持久化去重: 推送过的不再推
        known = load_known_maturity_plays()
        new_results = [r for r in all_results if r.bond_code not in known]
        if not new_results:
            logger.info(f"扫到 {len(all_results)} 只，但均已推送过")
            return

        logger.info(f"首次发现 {len(new_results)} 只到期博弈机会")
        notify_cb_maturity_play(new_results)

        # 标记为已推送
        for r in new_results:
            known.add(r.bond_code)
        save_known_maturity_plays(known)
        logger.info(f"已记录 {len(known)} 只历史推送")
    _safe_run("可转债到期博弈套利", _cb_maturity_play)

    # ===== 可转债打新 =====
    def _cb_ipo():
        logger.info("--- 可转债打新 ---")
        ipo_results = scan_cb_ipo()
        if ipo_results:
            logger.info(f"发现 {len(ipo_results)} 只可打新可转债")
            notify_cb_ipo(ipo_results)
        else:
            logger.info("无可打新可转债")
    _safe_run("可转债打新", _cb_ipo)

    # ===== AH股溢价 =====
    def _ah_premium():
        logger.info("--- AH股溢价 ---")
        ah_results = scan_ah_premium()
        if ah_results:
            logger.info(f"发现 {len(ah_results)} 只AH股极端溢价")
            notify_ah_premium(ah_results)
    _safe_run("AH股溢价", _ah_premium)

    logger.info("=== 价差监控流程结束 ===")


if __name__ == "__main__":
    run()
