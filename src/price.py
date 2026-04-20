"""股价获取模块 - 多源实时股价 + 异常处理与重试"""

import logging
import os
import time
import urllib.request

# 国内 API 直连：在导入 akshare/requests 之前清除所有代理
for _k in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "ALL_PROXY", "all_proxy"]:
    os.environ.pop(_k, None)
urllib.request.getproxies = lambda: {}

import requests          # noqa: E402
import requests.utils    # noqa: E402
requests.utils.getproxies = lambda: {}

# Windows 系统代理（注册表）/ TUN 模式代理 会绕过环境变量层面的清理，
# 直接 patch Session 确保 akshare 内部创建的所有连接都不走代理
_orig_session_init = requests.Session.__init__

def _no_proxy_session_init(self, *args, **kwargs):
    _orig_session_init(self, *args, **kwargs)
    self.trust_env = False
    self.proxies = {"http": None, "https": None}

requests.Session.__init__ = _no_proxy_session_init

import akshare as ak  # noqa: E402

logger = logging.getLogger(__name__)

# 缓存全量行情数据，避免重复拉取
_spot_cache = {"data": None, "time": 0}
_CACHE_TTL = 60  # 缓存 60 秒

_tencent_session = None


def _get_tencent_session() -> requests.Session:
    global _tencent_session
    if _tencent_session is None:
        _tencent_session = requests.Session()
        _tencent_session.trust_env = False
        _tencent_session.proxies = {"http": None, "https": None}
        _tencent_session.headers.update({"User-Agent": "Mozilla/5.0"})
    return _tencent_session


def _stock_code_to_tencent_symbol(stock_code: str) -> str:
    """将纯数字股票代码转为腾讯 API 格式 (sz000001 / sh600000)"""
    if stock_code.startswith(("6", "9")):
        return f"sh{stock_code}"
    return f"sz{stock_code}"


def _fetch_price_tencent(stock_code: str) -> dict | None:
    """
    通过腾讯股票 HTTP API (qt.gtimg.cn) 获取实时行情。
    该接口不依赖 push2.eastmoney.com，在 TUN/fake-ip 代理环境下更稳定。
    """
    symbol = _stock_code_to_tencent_symbol(stock_code)
    url = f"http://qt.gtimg.cn/q={symbol}"
    try:
        resp = _get_tencent_session().get(url, timeout=10)
        text = resp.text
        if '="' not in text:
            return None
        data_str = text.split('="', 1)[1].rstrip('";\n ')
        fields = data_str.split("~")
        if len(fields) < 38:
            return None
        current_price = float(fields[3])
        if current_price <= 0:
            return None
        daily_volume = round(float(fields[37]), 2)  # 字段37: 成交额(万元)
        return {
            "current_price": current_price,
            "daily_volume": daily_volume,
        }
    except Exception as e:
        logger.debug(f"腾讯API获取股价失败: {stock_code} - {e}")
        return None


def get_realtime_price(stock_code: str, retries: int = 3) -> dict | None:
    """
    获取实时股价信息。

    返回:
      {
        "current_price": float,    # 当前价格
        "daily_volume": float,     # 日成交额(万元)
      }
    """
    # 优先使用腾讯 API（不受 TUN/fake-ip 代理影响）
    result = _fetch_price_tencent(stock_code)
    if result is not None:
        return result

    for attempt in range(retries):
        # 方案1: akshare 轻量级单股接口
        try:
            df = ak.stock_bid_ask_em(symbol=stock_code)
            if df is not None and not df.empty:
                latest_row = df[df["item"] == "最新"]
                if not latest_row.empty:
                    current_price = float(latest_row["value"].values[0])
                    daily_volume = 0.0
                    vol_row = df[df["item"] == "成交额"]
                    if not vol_row.empty:
                        try:
                            daily_volume = round(
                                float(vol_row["value"].values[0]) / 10000, 2
                            )
                        except (ValueError, TypeError):
                            pass
                    return {
                        "current_price": current_price,
                        "daily_volume": daily_volume,
                    }
        except Exception:
            pass

        # 方案2: akshare 全量行情（带缓存）
        try:
            now = time.time()
            if _spot_cache["data"] is None or (now - _spot_cache["time"]) > _CACHE_TTL:
                _spot_cache["data"] = ak.stock_zh_a_spot_em()
                _spot_cache["time"] = now

            df = _spot_cache["data"]
            if df is not None:
                row = df[df["代码"] == stock_code]
                if not row.empty:
                    current_price = float(row["最新价"].values[0])
                    volume = float(row["成交额"].values[0])  # 单位: 元
                    return {
                        "current_price": current_price,
                        "daily_volume": round(volume / 10000, 2),
                    }
        except Exception as e:
            logger.warning(
                f"获取股价失败 (尝试 {attempt + 1}/{retries}): {stock_code} - {e}"
            )
            if attempt < retries - 1:
                time.sleep(2)

    logger.error(f"获取股价最终失败: {stock_code}")
    return None


def is_trading_day() -> bool:
    """
    判断今天是否为交易日（北京时间）。

    策略: 重试3次访问新浪交易日历；全失败时降级到本地周末判断。
    重要: 必须用北京时间判断，GitHub Actions 容器是 UTC 时间，
    UTC 周日 22:58 = 北京周一 6:58，按 UTC 判断会误以为是周末。
    """
    from datetime import datetime, timedelta, timezone
    bj = datetime.now(timezone.utc) + timedelta(hours=8)
    today = bj.strftime("%Y-%m-%d")
    last_err = None
    for attempt in range(3):
        try:
            df = ak.tool_trade_date_hist_sina()
            trade_dates = df["trade_date"].astype(str).tolist()
            return today in trade_dates
        except Exception as e:
            last_err = e
            logger.warning(f"获取交易日历失败 (尝试 {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(2)

    # 降级: 北京时间周一至周五默认交易日（无法识别调休）
    weekday = bj.weekday()  # 0=周一, 6=周日
    is_weekday = weekday < 5
    logger.error(
        f"交易日历API最终失败({last_err})，降级判断(北京时间): {'工作日' if is_weekday else '周末'} (weekday={weekday})"
    )
    # 推送告警让用户知道用了降级判断
    try:
        from .notifier import notify_error
        notify_error(
            stage="交易日历API",
            error="新浪交易日历3次重试均失败，已降级用本地周末判断",
            detail=f"{str(last_err)}\n今日: weekday={weekday}, 判定为{'工作日(继续)' if is_weekday else '周末(跳过)'}\n注意: 本地降级无法识别调休，可能漏掉调休交易日",
        )
    except Exception:
        pass
    return is_weekday
