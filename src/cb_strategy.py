"""可转债策略模块 - 转股套利 + 强赎预警 + 回售套利"""

import logging
import math
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from .cb_data import get_cb_list
from .config import load_config

logger = logging.getLogger(__name__)

_sector_info_cache: dict[str, tuple[str, str] | None] = {}
_stock_history_cache: dict[tuple[str, str, str], list[tuple[str, float]]] = {}
_sector_history_cache: dict[tuple[str, str, str], list[tuple[str, float]]] = {}


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


@dataclass
class CBPutbackResult:
    """可转债回售套利结果"""
    bond_code: str
    bond_name: str
    bond_price: float
    stock_code: str
    stock_name: str
    convert_price: float
    resale_trig_price: float      # 回售触发价 = 转股价 × 70%
    stock_price: float
    stock_vs_trig: float          # 正股/触发价 百分比
    years_to_expire: float        # 剩余年限
    expire_date: str
    putback_price: float          # 预估回售价(面值+利息)
    profit_pct: float             # 预估收益率(%)
    volume: float


@dataclass
class CBRedemptionAlert:
    """可转债强赎预警"""
    bond_code: str
    bond_name: str
    bond_price: float
    stock_code: str
    stock_name: str
    convert_price: float       # 转股价
    convert_value: float       # 转股价值
    ratio: float               # 正股价/转股价 的百分比(如 135 表示135%)


@dataclass
class CBLowPriceMaturityResult:
    """低价临期可转债候选。"""
    bond_code: str
    bond_name: str
    bond_price: float
    stock_code: str
    stock_name: str
    days_to_expire: int
    expire_date: str


@dataclass
class CBMidtermStockResult:
    """可转债正股中线候选。"""
    stock_code: str
    stock_name: str
    bond_code: str
    bond_name: str
    sector_name: str           # 正股所属行业板块
    stock_4w_return: float     # 正股近4周累计涨幅(%)
    sector_4w_return: float    # 板块近4周累计涨幅(%)
    excess_4w_return: float    # 正股跑赢板块(%)
    return_correlation: float | None  # 正股/板块日收益率相关系数
    weekly_outperform_count: int       # 近N周跑赢板块的周数
    observed_weeks: int


@contextmanager
def _without_proxy_env():
    """东方财富接口直连，避免系统代理导致 AkShare 请求失败。"""
    proxy_keys = (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    )
    no_proxy_keys = ("NO_PROXY", "no_proxy")
    saved = {
        key: os.environ[key]
        for key in proxy_keys + no_proxy_keys
        if key in os.environ
    }
    for key in proxy_keys:
        os.environ.pop(key, None)
    for key in no_proxy_keys:
        os.environ[key] = "*"
    try:
        yield
    finally:
        for key in no_proxy_keys:
            os.environ.pop(key, None)
        os.environ.update(saved)


def scan_cb_arbitrage(cb_list: list[dict] | None = None) -> list[CBArbitrageResult]:
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

    if cb_list is None:
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


def scan_cb_putback(cb_list: list[dict] | None = None) -> list[CBPutbackResult]:
    """
    扫描可转债回售套利「观察名单」。

    回售条件(主流条款):
      - 最后两个计息年度内
      - 正股连续30个交易日收盘价 < 转股价 × 70%
      - 须在公司设定的回售申报窗口内申报

    本扫描器仅做「预警观察」，不代表可立即执行套利:
      - 剩余年限 <= 2 年
      - 当前正股/转股价 < 阈值 (单日代替30日连续条件)
      - 转债现价 <= max_bond_price (默认100, 保证利润空间)

    使用者需自行校验: ①连续30日条件 ②申报窗口期 ③具体条款差异
    """
    cfg = load_config().get("cb_putback", {})
    if not cfg.get("enabled", True):
        return []

    max_years = cfg.get("max_years_to_expire", 2.0)
    max_stock_ratio = cfg.get("max_stock_ratio", 72)
    min_profit_pct = cfg.get("min_profit_pct", 0.5)
    min_volume = cfg.get("min_volume", 500)
    estimated_interest = cfg.get("estimated_interest", 1.5)
    max_bond_price = cfg.get("max_bond_price", 100)  # 硬过滤: 100元以上不做
    max_results = cfg.get("max_results", 10)

    if cb_list is None:
        cb_list = get_cb_list()
    if not cb_list:
        return []

    today = datetime.now().date()
    results = []

    for cb in cb_list:
        cp = cb.get("convert_price", 0)
        cv = cb.get("convert_value", 0)
        bp = cb.get("bond_price", 0)
        volume = cb.get("volume", 0)
        expire_date_str = cb.get("expire_date", "")

        if cp <= 0 or cv <= 0 or bp <= 0 or volume < min_volume:
            continue
        if bp > max_bond_price:  # 硬过滤: 超过票面价不做
            continue
        if not expire_date_str:
            continue

        # 检查剩余年限
        try:
            expire_date = datetime.strptime(expire_date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        years_to_expire = (expire_date - today).days / 365
        if years_to_expire <= 0 or years_to_expire > max_years:
            continue

        # stock_price 反推: cv = stock_price * 100 / cp → stock_price = cv * cp / 100
        stock_price = cv * cp / 100
        resale_trig_price = cp * 0.7
        # stock_vs_cp = 正股 / 转股价 × 100%, 70%=触发线
        stock_vs_cp = stock_price / cp * 100

        # 正股/转股价 必须低于(或接近)70% 触发线
        if stock_vs_cp > max_stock_ratio:
            continue

        # 预估回售价 = 面值 + 利息
        putback_price = 100 + estimated_interest
        profit_pct = (putback_price - bp) / bp * 100

        if profit_pct < min_profit_pct:
            continue

        results.append(CBPutbackResult(
            bond_code=cb.get("bond_code", ""),
            bond_name=cb.get("bond_name", ""),
            bond_price=bp,
            stock_code=cb.get("stock_code", ""),
            stock_name=cb.get("stock_name", ""),
            convert_price=cp,
            resale_trig_price=round(resale_trig_price, 2),
            stock_price=round(stock_price, 2),
            stock_vs_trig=round(stock_vs_cp, 1),   # 正股/转股价 %，70=触发线
            years_to_expire=round(years_to_expire, 2),
            expire_date=expire_date_str,
            putback_price=round(putback_price, 2),
            profit_pct=round(profit_pct, 2),
            volume=volume,
        ))

    results.sort(key=lambda x: -x.profit_pct)
    results = results[:max_results]

    if results:
        logger.info(f"发现 {len(results)} 只可转债回售套利机会")
    return results


def _to_float(val) -> float | None:
    """安全转 float，支持 '-' / '' / 'nan' / 百分号。"""
    if val is None:
        return None
    text = str(val).replace(",", "").replace("%", "").strip()
    if text in ("", "-", "nan", "None"):
        return None
    try:
        return float(text)
    except (ValueError, TypeError):
        return None


def _history_rows_from_df(df, start_date: str, end_date: str) -> list[tuple[str, float]]:
    """从 akshare DataFrame 中提取 (YYYY-MM-DD, close)。"""
    if df is None or getattr(df, "empty", True):
        return []

    cols = list(df.columns)
    date_col = next(
        (c for c in cols if "日期" in str(c) or str(c).lower() == "date"),
        None,
    )
    close_col = next(
        (c for c in cols if "收盘" in str(c) or str(c).lower() == "close"),
        None,
    )
    if close_col is None:
        close_col = next((c for c in cols if "最新" in str(c)), None)
    if close_col is None:
        return []

    rows: list[tuple[str, float]] = []
    for index, row in df.iterrows():
        raw_date = str(row[date_col] if date_col is not None else index)[:10]
        if len(raw_date) >= 8 and "-" not in raw_date:
            date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        else:
            date_str = raw_date
        close = _to_float(row[close_col])
        if not date_str or close is None or close <= 0:
            continue
        if start_date <= date_str <= end_date:
            rows.append((date_str, close))

    rows.sort(key=lambda x: x[0])
    return rows


def _prepare_sw_sector_map(stock_codes: set[str]) -> None:
    """一次性加载申万一级行业成分，建立正股到行业指数的映射。"""
    missing_codes = stock_codes - set(_sector_info_cache)
    if not missing_codes:
        return

    try:
        import akshare as ak
        import pandas as pd
        import requests
        import time
        from akshare.utils.cons import headers
        from io import StringIO

        with _without_proxy_env():
            sector_df = ak.sw_index_first_info()
        for sector_index, (_, sector) in enumerate(sector_df.iterrows(), start=1):
            sector_symbol = str(sector["行业代码"])
            sector_code = sector_symbol.split(".")[0]
            sector_name = str(sector["行业名称"]).strip()
            if sector_index == 1 or sector_index % 5 == 0:
                logger.info(f"申万行业映射进度: {sector_index}/{len(sector_df)}")
            member_codes = None
            try:
                with _without_proxy_env():
                    member_df = ak.index_component_sw(symbol=sector_code)
                if member_df is not None and not member_df.empty:
                    member_codes = member_df["证券代码"].astype(str).str.zfill(6)
            except Exception:
                pass

            if member_codes is None:
                for attempt in range(3):
                    try:
                        with _without_proxy_env():
                            response = requests.get(
                                "https://legulegu.com/stockdata/index-composition",
                                params={"industryCode": sector_symbol},
                                headers=headers,
                                timeout=30,
                            )
                        response.raise_for_status()
                        member_df = pd.read_html(StringIO(response.text))[0]
                        member_codes = (
                            member_df.iloc[:, 1].astype(str).str.split(".").str[0]
                        )
                        break
                    except Exception as e:
                        if attempt == 2:
                            logger.debug(
                                f"跳过申万行业 {sector_name}({sector_code}): {e}"
                            )
                        else:
                            time.sleep((attempt + 1) * 2)

            if member_codes is None:
                continue
            for stock_code in member_codes:
                if stock_code in missing_codes:
                    _sector_info_cache[stock_code] = (sector_name, sector_code)
            if missing_codes <= set(_sector_info_cache):
                break
    except Exception as e:
        logger.warning(f"加载申万行业成分失败: {e}")

def _get_stock_sector(stock_code: str) -> tuple[str, str] | None:
    return _sector_info_cache.get(stock_code)


def _get_stock_history(stock_code: str, start_date: str, end_date: str) -> list[tuple[str, float]]:
    cache_key = (stock_code, start_date, end_date)
    if cache_key in _stock_history_cache:
        return _stock_history_cache[cache_key]

    rows: list[tuple[str, float]] = []
    try:
        import akshare as ak

        market_prefix = "sh" if stock_code.startswith(("5", "6", "9")) else "sz"
        with _without_proxy_env():
            df = ak.stock_zh_a_daily(
                symbol=f"{market_prefix}{stock_code}",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adjust="qfq",
            )
        rows = _history_rows_from_df(df, start_date, end_date)
    except Exception as e:
        logger.debug(f"获取正股历史行情失败: {stock_code} - {e}")

    _stock_history_cache[cache_key] = rows
    return rows


def _get_sector_history(sector_code: str, start_date: str, end_date: str) -> list[tuple[str, float]]:
    cache_key = (sector_code, start_date, end_date)
    if cache_key in _sector_history_cache:
        return _sector_history_cache[cache_key]

    rows: list[tuple[str, float]] = []
    try:
        import akshare as ak

        with _without_proxy_env():
            df = ak.index_hist_sw(symbol=sector_code, period="day")
        rows = _history_rows_from_df(df, start_date, end_date)
    except Exception as e:
        logger.debug(f"获取申万行业指数历史行情失败: {sector_code} - {e}")

    _sector_history_cache[cache_key] = rows
    return rows


def _daily_returns(rows: list[tuple[str, float]]) -> dict[str, float]:
    returns: dict[str, float] = {}
    for i in range(1, len(rows)):
        prev_close = rows[i - 1][1]
        close = rows[i][1]
        if prev_close > 0:
            returns[rows[i][0]] = close / prev_close - 1
    return returns


def _compound_return(values: list[float]) -> float:
    total = 1.0
    for v in values:
        total *= 1 + v
    return total - 1


def _pearson_corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    x_avg = sum(xs) / len(xs)
    y_avg = sum(ys) / len(ys)
    cov = sum((x - x_avg) * (y - y_avg) for x, y in zip(xs, ys))
    x_var = sum((x - x_avg) ** 2 for x in xs)
    y_var = sum((y - y_avg) ** 2 for y in ys)
    denom = math.sqrt(x_var * y_var)
    if denom == 0:
        return None
    return cov / denom


def _calc_relative_strength(stock_code: str, cfg: dict) -> dict | None:
    """计算正股相对行业板块的4周强度和相关系数。"""
    weeks = int(cfg.get("weeks", 4))
    if weeks <= 0:
        return None

    max_sector_gain_pct = cfg.get("max_sector_gain_pct", 5)
    min_excess_return_pct = cfg.get("min_excess_return_pct", 8)
    require_every_week = cfg.get("require_every_week_outperform", True)
    min_weekly_excess_pct = cfg.get("min_weekly_excess_pct", 0)

    end = datetime.now().date()
    start = end - timedelta(days=weeks * 10 + 21)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    sector_info = _get_stock_sector(stock_code)
    if not sector_info:
        return None
    sector_name, sector_code = sector_info

    stock_rows = _get_stock_history(stock_code, start_str, end_str)
    sector_rows = _get_sector_history(sector_code, start_str, end_str)
    if len(stock_rows) < weeks * 3 or len(sector_rows) < weeks * 3:
        return None

    stock_returns = _daily_returns(stock_rows)
    sector_returns = _daily_returns(sector_rows)
    common_dates = sorted(set(stock_returns) & set(sector_returns))
    if len(common_dates) < weeks * 3:
        return None

    current_iso = end.isocalendar()
    current_week = (current_iso.year, current_iso.week)
    grouped: dict[tuple[int, int], list[str]] = {}
    for date_str in common_dates:
        iso = datetime.strptime(date_str, "%Y-%m-%d").date().isocalendar()
        week_key = (iso.year, iso.week)
        if week_key == current_week:
            continue
        grouped.setdefault(week_key, []).append(date_str)

    week_groups = sorted(grouped.values(), key=lambda dates: dates[-1])[-weeks:]
    if len(week_groups) < weeks:
        return None

    selected_dates: list[str] = []
    weekly_outperform_count = 0
    weekly_rows = []
    for dates in week_groups:
        selected_dates.extend(dates)
        stock_week = _compound_return([stock_returns[d] for d in dates])
        sector_week = _compound_return([sector_returns[d] for d in dates])
        weekly_excess_pct = (stock_week - sector_week) * 100
        if weekly_excess_pct >= min_weekly_excess_pct:
            weekly_outperform_count += 1
        weekly_rows.append((stock_week, sector_week, weekly_excess_pct))

    stock_4w = _compound_return([stock_returns[d] for d in selected_dates]) * 100
    sector_4w = _compound_return([sector_returns[d] for d in selected_dates]) * 100
    excess_4w = stock_4w - sector_4w
    corr = _pearson_corr(
        [stock_returns[d] for d in selected_dates],
        [sector_returns[d] for d in selected_dates],
    )

    if sector_4w > max_sector_gain_pct:
        return None
    if excess_4w < min_excess_return_pct:
        return None
    if require_every_week and weekly_outperform_count < weeks:
        return None

    return {
        "sector_name": sector_name,
        "stock_4w_return": round(stock_4w, 2),
        "sector_4w_return": round(sector_4w, 2),
        "excess_4w_return": round(excess_4w, 2),
        "return_correlation": round(corr, 3) if corr is not None else None,
        "weekly_outperform_count": weekly_outperform_count,
        "observed_weeks": len(weekly_rows),
    }


def scan_cb_low_price_maturity(
    cb_list: list[dict] | None = None,
) -> list[CBLowPriceMaturityResult]:
    """筛选转债价格低于100且剩余期限不超过1.5年的标的。"""
    cfg = load_config().get("cb_low_price_maturity", {})
    if not cfg.get("enabled", True):
        return []

    max_years = cfg.get("max_years_to_expire", 1.5)
    max_bond_price = cfg.get("max_bond_price", 100)
    max_results = cfg.get("max_results", 20)
    if cb_list is None:
        cb_list = get_cb_list()
    if not cb_list:
        return []

    today = datetime.now().date()
    results = []
    for cb in cb_list:
        bond_price = cb.get("bond_price", 0)
        expire_str = cb.get("expire_date", "")
        if bond_price <= 0 or bond_price >= max_bond_price or not expire_str:
            continue
        try:
            expire_date = datetime.strptime(expire_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        days_to_expire = (expire_date - today).days
        if days_to_expire <= 0 or days_to_expire > max_years * 365:
            continue
        results.append(CBLowPriceMaturityResult(
            bond_code=cb.get("bond_code", ""),
            bond_name=cb.get("bond_name", ""),
            bond_price=bond_price,
            stock_code=cb.get("stock_code", ""),
            stock_name=cb.get("stock_name", ""),
            days_to_expire=days_to_expire,
            expire_date=expire_str,
        ))

    results.sort(key=lambda item: (item.bond_price, item.days_to_expire))
    return results[:max_results]


def scan_cb_maturity_play(cb_list: list[dict] | None = None) -> list[CBMidtermStockResult]:
    """
    扫描可转债中线候选。

    逻辑:
      全部在市转债对应正股，近4周相对行业板块持续走强。
      correlation 使用正股与行业板块日收益率的 Pearson 相关系数，
      作为候选的走势关联值展示。

    筛选:
      - 板块近4周涨幅不大
      - 正股近4周累计明显跑赢板块
      - 4个观察周每周都跑赢板块
    """
    cfg = load_config().get("cb_midterm_stock", {})
    if not cfg.get("enabled", True):
        return []

    max_results = cfg.get("max_results", 20)
    rs_cfg = cfg.get("relative_strength", {})

    if cb_list is None:
        cb_list = get_cb_list()
    if not cb_list:
        return []

    results = []
    seen_stock_codes = set()
    stock_codes = {cb.get("stock_code", "") for cb in cb_list} - {""}
    logger.info(f"加载 {len(stock_codes)} 只正股的申万一级行业映射...")
    _prepare_sw_sector_map(stock_codes)
    unmapped_codes = {code for code in stock_codes if _get_stock_sector(code) is None}
    if unmapped_codes:
        import time

        logger.info(f"{len(unmapped_codes)} 只正股行业未匹配，等待后进行第二轮重试...")
        time.sleep(5)
        _prepare_sw_sector_map(unmapped_codes)
    mapped_count = sum(_get_stock_sector(code) is not None for code in stock_codes)
    logger.info(f"申万行业映射完成: {mapped_count}/{len(stock_codes)}")
    for index, cb in enumerate(cb_list, start=1):
        stock_code = cb.get("stock_code", "")
        if not stock_code or stock_code in seen_stock_codes:
            continue
        seen_stock_codes.add(stock_code)

        if index == 1 or index % 25 == 0:
            logger.info(f"中线正股扫描进度: {index}/{len(cb_list)}")

        strength = _calc_relative_strength(stock_code, rs_cfg)
        if not strength:
            continue

        results.append(CBMidtermStockResult(
            stock_code=stock_code,
            stock_name=cb.get("stock_name", ""),
            bond_code=cb.get("bond_code", ""),
            bond_name=cb.get("bond_name", ""),
            sector_name=strength["sector_name"],
            stock_4w_return=strength["stock_4w_return"],
            sector_4w_return=strength["sector_4w_return"],
            excess_4w_return=strength["excess_4w_return"],
            return_correlation=strength["return_correlation"],
            weekly_outperform_count=strength["weekly_outperform_count"],
            observed_weeks=strength["observed_weeks"],
        ))

    results.sort(key=lambda x: -x.excess_4w_return)
    results = results[:max_results]

    if results:
        logger.info(f"发现 {len(results)} 只正股中线候选")
    return results


def scan_cb_redemption_alert(cb_list: list[dict] | None = None) -> list[CBRedemptionAlert]:
    """
    扫描接近强赎触发的可转债。

    强赎条件: 正股收盘价连续N天 > 转股价 × 130%
    即 转股价值 > 130 时，正股已超过强赎触发线。
    本函数检查 转股价值 > 阈值(默认125)，提前预警。
    """
    cfg = load_config().get("cb_redemption", {})
    if not cfg.get("enabled", True):
        return []

    min_cv = cfg.get("min_convert_value", 125)
    max_cv = cfg.get("max_convert_value", 150)
    min_volume = cfg.get("min_volume", 500)
    max_results = cfg.get("max_results", 10)

    if cb_list is None:
        cb_list = get_cb_list()
    if not cb_list:
        return []

    results = []
    for cb in cb_list:
        cv = cb.get("convert_value", 0)
        cp = cb.get("convert_price", 0)
        volume = cb.get("volume", 0)

        if cv < min_cv or cv > max_cv or cp <= 0 or volume < min_volume:
            continue

        # ratio = 正股价/转股价 的百分比 = convert_value (因为 cv = stock_price * 100/cp)
        results.append(CBRedemptionAlert(
            bond_code=cb.get("bond_code", ""),
            bond_name=cb.get("bond_name", ""),
            bond_price=cb.get("bond_price", 0),
            stock_code=cb.get("stock_code", ""),
            stock_name=cb.get("stock_name", ""),
            convert_price=cp,
            convert_value=round(cv, 2),
            ratio=round(cv, 2),  # cv 本身就是 stock_price/cp * 100
        ))

    results.sort(key=lambda x: -x.ratio)
    results = results[:max_results]

    if results:
        logger.info(f"发现 {len(results)} 只接近强赎的可转债")
    return results
