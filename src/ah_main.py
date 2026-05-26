"""AH 股溢价监控独立入口 - 每周二 9:35 北京时间触发"""

import logging
import traceback

from .ah_monitor import scan_ah_premium
from .notifier import notify_ah_premium, notify_error
from .price import is_trading_day

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run():
    logger.info("=== AH股溢价监控开始 ===")

    try:
        if not is_trading_day():
            logger.info("今日非交易日，跳过")
            return
    except Exception as e:
        logger.error(f"交易日判断失败: {e}")
        notify_error(stage="交易日判断", error=str(e), detail=traceback.format_exc())
        return

    try:
        results = scan_ah_premium()
        if results:
            logger.info(f"发现 {len(results)} 只AH股极端溢价")
            notify_ah_premium(results)
        else:
            logger.info("无AH股极端溢价")
    except Exception as e:
        logger.error(f"AH股扫描异常: {e}")
        notify_error(stage="AH股溢价", error=str(e), detail=traceback.format_exc())

    logger.info("=== AH股溢价监控结束 ===")


if __name__ == "__main__":
    run()
