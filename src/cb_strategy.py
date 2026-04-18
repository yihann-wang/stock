"""可转债策略模块 - 转股套利 + 强赎预警 + 回售套利"""

import logging
from dataclasses import dataclass
from datetime import datetime

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
class CBMaturityPlayResult:
    """可转债到期博弈套利结果"""
    bond_code: str
    bond_name: str
    bond_price: float          # 转债现价(元), 应<=阈值
    stock_code: str
    stock_name: str
    stock_price: float         # 正股现价
    convert_price: float       # 当前转股价
    convert_value: float       # 转股价值
    premium_rate: float        # 转股溢价率(%), 应>=阈值
    days_to_expire: int        # 距到期天数
    expire_date: str
    volume: float


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


def scan_cb_maturity_play(cb_list: list[dict] | None = None) -> list[CBMaturityPlayResult]:
    """
    扫描可转债到期博弈套利机会。

    逻辑:
      到期1年内 + 转债价低(<=105) + 高溢价(>=100%) → 公司面临到期偿付压力，
      有强动力下修转股价或拉抬正股，刺激投资者转股以避免现金赎回。
      此时买入转债，等待:
        ① 公司公告下修转股价 → 转债大涨
        ② 公司主动拉抬股价 → 转股价值上升
        ③ 即使啥都没发生，转债价低安全垫高，最差按面值+利息到期偿付

    筛选:
      - 剩余年限 <= 1 年
      - 转债现价 <= max_bond_price (默认105)
      - 转股溢价率 >= min_premium_rate (默认100)
      - 成交额 >= min_volume
    """
    cfg = load_config().get("cb_maturity_play", {})
    if not cfg.get("enabled", True):
        return []

    max_years = cfg.get("max_years_to_expire", 1.0)
    max_bond_price = cfg.get("max_bond_price", 105)
    min_premium_rate = cfg.get("min_premium_rate", 100)
    min_volume = cfg.get("min_volume", 200)
    max_results = cfg.get("max_results", 20)

    if cb_list is None:
        cb_list = get_cb_list()
    if not cb_list:
        return []

    today = datetime.now().date()
    results = []
    for cb in cb_list:
        bp = cb.get("bond_price", 0)
        cp = cb.get("convert_price", 0)
        cv = cb.get("convert_value", 0)
        premium = cb.get("premium_rate", 0)
        volume = cb.get("volume", 0)
        expire_str = cb.get("expire_date", "")

        if bp <= 0 or bp > max_bond_price:
            continue
        if cp <= 0 or cv <= 0:
            continue
        if premium < min_premium_rate:
            continue
        if volume < min_volume:
            continue
        if not expire_str:
            continue

        try:
            exp_date = datetime.strptime(expire_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        days_to_expire = (exp_date - today).days
        if days_to_expire <= 0 or days_to_expire > max_years * 365:
            continue

        stock_price = cv * cp / 100  # 反推正股价

        results.append(CBMaturityPlayResult(
            bond_code=cb.get("bond_code", ""),
            bond_name=cb.get("bond_name", ""),
            bond_price=bp,
            stock_code=cb.get("stock_code", ""),
            stock_name=cb.get("stock_name", ""),
            stock_price=round(stock_price, 2),
            convert_price=cp,
            convert_value=round(cv, 2),
            premium_rate=round(premium, 2),
            days_to_expire=days_to_expire,
            expire_date=expire_str,
            volume=volume,
        ))

    # 按"剩余天数升序"排序（越接近到期，公司越急）
    results.sort(key=lambda x: x.days_to_expire)
    results = results[:max_results]

    if results:
        logger.info(f"发现 {len(results)} 只到期博弈套利机会")
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
