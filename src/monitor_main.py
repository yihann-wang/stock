"""可转债中线候选监控入口。"""

import logging
import os
import traceback

from .cb_data import get_cb_list
from .cb_strategy import scan_cb_low_price_maturity, scan_cb_maturity_play
from .config import load_config
from .notifier import notify_cb_low_price_maturity, notify_cb_maturity_play, notify_error
from .price import is_trading_day

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
    logger.info("=== 可转债中线候选扫描开始 ===")
    force_run = os.getenv("FORCE_RUN", "").lower() == "true"

    if not force_run:
        try:
            if not is_trading_day():
                logger.info("今日非交易日，跳过扫描")
                return
        except Exception as e:
            logger.error(f"交易日判断失败: {e}")
            notify_error(stage="交易日判断", error=str(e), detail=traceback.format_exc())
            return
    else:
        logger.info("手动触发，忽略交易日限制")

    def _scan_midterm_candidates():
        cfg = load_config().get("cb_midterm_stock", {})
        if not cfg.get("enabled", True):
            logger.info("正股中线候选扫描已禁用")
            return

        cb_list = get_cb_list()
        if not cb_list:
            logger.warning("可转债数据获取失败")
            return

        low_price_results = scan_cb_low_price_maturity(cb_list)
        if low_price_results:
            logger.info(f"发现 {len(low_price_results)} 只低价临期转债")
            notify_cb_low_price_maturity(low_price_results)
        else:
            logger.info("无低价临期转债候选")

        results = scan_cb_maturity_play(cb_list)
        if not results:
            logger.info("无中线候选")
            return

        logger.info(f"发现 {len(results)} 只中线候选，全部推送")
        notify_cb_maturity_play(results)
    _safe_run("可转债中线候选", _scan_midterm_candidates)

    logger.info("=== 可转债中线候选扫描结束 ===")


if __name__ == "__main__":
    run()
