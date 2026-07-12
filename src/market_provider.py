"""Stock history and Shenwan level-one industry data providers."""

import hashlib
import json
import logging
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

from .http_client import DirectHttpClient, HttpRequestError
from .models import PricePoint, SectorInfo


logger = logging.getLogger(__name__)

TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_PROXY_KLINE_URL = (
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
)
# This public quote host closes some Python TLS connections while its HTTP
# endpoint returns the same non-sensitive market classification payload.
EASTMONEY_BATCH_QUOTE_URL = "http://push2.eastmoney.com/api/qt/ulist.np/get"
LEGULEGU_ROOT = "https://legulegu.com"
SWS_TREND_URL = "https://www.swsresearch.com/institute-sw/api/index_publish/trend/"

SW_LEVEL_ONE_SECTORS: tuple[SectorInfo, ...] = (
    SectorInfo("801010", "农林牧渔"),
    SectorInfo("801030", "基础化工"),
    SectorInfo("801040", "钢铁"),
    SectorInfo("801050", "有色金属"),
    SectorInfo("801080", "电子"),
    SectorInfo("801880", "汽车"),
    SectorInfo("801110", "家用电器"),
    SectorInfo("801120", "食品饮料"),
    SectorInfo("801130", "纺织服饰"),
    SectorInfo("801140", "轻工制造"),
    SectorInfo("801150", "医药生物"),
    SectorInfo("801160", "公用事业"),
    SectorInfo("801170", "交通运输"),
    SectorInfo("801180", "房地产"),
    SectorInfo("801200", "商贸零售"),
    SectorInfo("801210", "社会服务"),
    SectorInfo("801780", "银行"),
    SectorInfo("801790", "非银金融"),
    SectorInfo("801230", "综合"),
    SectorInfo("801710", "建筑材料"),
    SectorInfo("801720", "建筑装饰"),
    SectorInfo("801730", "电力设备"),
    SectorInfo("801890", "机械设备"),
    SectorInfo("801740", "国防军工"),
    SectorInfo("801750", "计算机"),
    SectorInfo("801760", "传媒"),
    SectorInfo("801770", "通信"),
    SectorInfo("801950", "煤炭"),
    SectorInfo("801960", "石油石化"),
    SectorInfo("801970", "环保"),
    SectorInfo("801980", "美容护理"),
)

SECTOR_BY_CODE = {sector.code: sector for sector in SW_LEVEL_ONE_SECTORS}

# Eastmoney's f100 field follows the SW 2021 lower-level industry names. These
# explicit entries cover groups that had no already-mapped bond stock from which
# to infer their level-one parent during the initial cache build.
INDUSTRY_PARENT_OVERRIDES = {
    "IT服务Ⅱ": SECTOR_BY_CODE["801750"],
    "半导体": SECTOR_BY_CODE["801080"],
    "专业服务": SECTOR_BY_CODE["801210"],
    "专用设备": SECTOR_BY_CODE["801890"],
    "休闲食品": SECTOR_BY_CODE["801120"],
    "军工电子Ⅱ": SECTOR_BY_CODE["801740"],
    "包装印刷": SECTOR_BY_CODE["801140"],
    "化妆品": SECTOR_BY_CODE["801980"],
    "多元金融": SECTOR_BY_CODE["801790"],
    "家居用品": SECTOR_BY_CODE["801140"],
    "工程机械": SECTOR_BY_CODE["801890"],
    "教育": SECTOR_BY_CODE["801210"],
    "数字媒体": SECTOR_BY_CODE["801760"],
    "服装家纺": SECTOR_BY_CODE["801130"],
    "游戏Ⅱ": SECTOR_BY_CODE["801760"],
    "炼化及贸易": SECTOR_BY_CODE["801960"],
    "焦炭Ⅱ": SECTOR_BY_CODE["801950"],
    "煤炭开采": SECTOR_BY_CODE["801950"],
    "环保设备Ⅱ": SECTOR_BY_CODE["801970"],
    "环境治理": SECTOR_BY_CODE["801970"],
    "纺织制造": SECTOR_BY_CODE["801130"],
    "自动化设备": SECTOR_BY_CODE["801890"],
    "航空装备Ⅱ": SECTOR_BY_CODE["801740"],
    "计算机设备": SECTOR_BY_CODE["801750"],
    "证券Ⅱ": SECTOR_BY_CODE["801790"],
    "轨交设备Ⅱ": SECTOR_BY_CODE["801890"],
    "软件开发": SECTOR_BY_CODE["801750"],
    "通信服务": SECTOR_BY_CODE["801770"],
    "通信设备": SECTOR_BY_CODE["801770"],
    "通用设备": SECTOR_BY_CODE["801890"],
    "造纸": SECTOR_BY_CODE["801140"],
    "银行Ⅱ": SECTOR_BY_CODE["801780"],
    "电网设备": SECTOR_BY_CODE["801730"],
    "非白酒": SECTOR_BY_CODE["801120"],
    "食品加工": SECTOR_BY_CODE["801120"],
    "饮料乳品": SECTOR_BY_CODE["801120"],
}


def _market_symbol(stock_code: str) -> str:
    if stock_code.startswith(("5", "6", "9")):
        return f"sh{stock_code}"
    if stock_code.startswith(("0", "2", "3")):
        return f"sz{stock_code}"
    if stock_code.startswith(("4", "8", "92")):
        return f"bj{stock_code}"
    raise ValueError(f"unsupported stock code: {stock_code}")


def _parse_price_points(
    rows: list, start_date: date, end_date: date
) -> list[PricePoint]:
    points: dict[date, PricePoint] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 3:
            continue
        try:
            trade_date = datetime.strptime(str(row[0]), "%Y-%m-%d").date()
            close = float(row[2])
        except (TypeError, ValueError):
            continue
        if start_date <= trade_date <= end_date and close > 0:
            points[trade_date] = PricePoint(trade_date, close)
    return [points[key] for key in sorted(points)]


class TencentStockHistoryProvider:
    def __init__(self, client: DirectHttpClient):
        self.client = client

    def get_history(
        self, stock_code: str, start_date: date, end_date: date
    ) -> list[PricePoint]:
        symbol = _market_symbol(stock_code)
        errors = []
        try:
            response = self.client.get(
                TENCENT_PROXY_KLINE_URL,
                params={
                    "_var": "kline_dayqfq",
                    "param": (
                        f"{symbol},day,{start_date:%Y-%m-%d},"
                        f"{end_date:%Y-%m-%d},160,qfq"
                    ),
                    "r": "0.8205512681390605",
                },
                timeout=30,
            )
            marker = response.text.find("={")
            payload_text = response.text[marker + 1 :] if marker >= 0 else response.text
            payload = json.loads(payload_text)
            points = self._points(payload, symbol, start_date, end_date)
            if points:
                return points
            errors.append("proxy returned empty data")
        except (HttpRequestError, ValueError) as exc:
            errors.append(f"proxy: {exc}")

        try:
            payload = self.client.get_json(
                TENCENT_KLINE_URL,
                params={
                    "param": (
                        f"{symbol},day,{start_date:%Y-%m-%d},"
                        f"{end_date:%Y-%m-%d},160,qfq"
                    )
                },
                timeout=30,
            )
            points = self._points(payload, symbol, start_date, end_date)
            if points:
                return points
            errors.append("legacy endpoint returned empty data")
        except HttpRequestError as exc:
            errors.append(f"legacy: {exc}")

        raise HttpRequestError(
            f"Tencent returned no history for {stock_code}: {' | '.join(errors)}"
        )

    @staticmethod
    def _points(
        payload: dict, symbol: str, start_date: date, end_date: date
    ) -> list[PricePoint]:
        symbol_data = (payload.get("data") or {}).get(symbol) or {}
        rows = symbol_data.get("qfqday") or symbol_data.get("day") or []
        return _parse_price_points(rows, start_date, end_date)


class SectorMapRepository:
    def __init__(self, path: Path):
        self.path = path
        self._lock = Lock()
        self._mapping = self._load()

    def _load(self) -> dict[str, SectorInfo]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        stocks = raw.get("stocks", raw)
        mapping = {}
        for stock_code, sector in stocks.items():
            if not isinstance(sector, dict):
                continue
            code = str(sector.get("code") or "")
            name = str(sector.get("name") or "")
            if code and name:
                mapping[str(stock_code).zfill(6)] = SectorInfo(code, name)
        return mapping

    def get(self, stock_code: str) -> SectorInfo | None:
        with self._lock:
            return self._mapping.get(stock_code)

    def missing(self, stock_codes: set[str]) -> set[str]:
        with self._lock:
            return stock_codes - self._mapping.keys()

    def update(self, updates: dict[str, SectorInfo]) -> None:
        with self._lock:
            self._mapping.update(updates)

    def save(self) -> None:
        with self._lock:
            payload = {
                "source": "申万行业分类2021（乐咕成分基线 + 东方财富行业增量补全）",
                "updated_at": date.today().isoformat(),
                "stocks": {
                    code: {"code": sector.code, "name": sector.name}
                    for code, sector in sorted(self._mapping.items())
                },
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


class ShenwanSectorProvider:
    """Provide SW level-one membership and daily index closes."""

    def __init__(self, client: DirectHttpClient, repository: SectorMapRepository):
        self.client = client
        self.repository = repository
        self._bootstrapped = False
        self._bootstrap_attempted = False
        self._bootstrap_error = ""

    @staticmethod
    def _page_url(sector: SectorInfo) -> str:
        return (
            f"{LEGULEGU_ROOT}/stockdata/sw-industry-2021?industryCode={sector.code}.SI"
        )

    def _headers(self, sector: SectorInfo) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Referer": self._page_url(sector),
            "X-Requested-With": "XMLHttpRequest",
        }

    def _bootstrap(self, sector: SectorInfo) -> None:
        if self._bootstrapped:
            return
        if self._bootstrap_attempted:
            raise HttpRequestError(
                f"Legulegu session bootstrap previously failed: {self._bootstrap_error}"
            )
        self._bootstrap_attempted = True
        try:
            self.client.get(
                self._page_url(sector),
                headers={"Referer": f"{LEGULEGU_ROOT}/stockdata/sw-industry-overview"},
                timeout=30,
            )
        except HttpRequestError as exc:
            self._bootstrap_error = str(exc)
            raise
        self._bootstrapped = True

    def get_history(
        self, sector: SectorInfo, start_date: date, end_date: date
    ) -> list[PricePoint]:
        errors = []
        try:
            payload = self.client.get_json(
                SWS_TREND_URL,
                params={"swindexcode": sector.code, "period": "DAY"},
                verify=False,
                timeout=60,
            )
            points = []
            for row in payload.get("data") or []:
                try:
                    trade_date = datetime.strptime(
                        row["bargaindate"], "%Y-%m-%d"
                    ).date()
                    close = float(row["closeindex"])
                except (KeyError, TypeError, ValueError):
                    continue
                if start_date <= trade_date <= end_date and close > 0:
                    points.append(PricePoint(trade_date, close))
            if points:
                return sorted(points, key=lambda item: item.trade_date)
            errors.append("SWS: empty data")
        except HttpRequestError as exc:
            errors.append(f"SWS: {exc}")

        try:
            return self._get_legulegu_history(sector, start_date, end_date)
        except HttpRequestError as exc:
            errors.append(f"Legulegu: {exc}")
        raise HttpRequestError(
            f"No index history for {sector.name}; " + " | ".join(errors)
        )

    def _get_legulegu_history(
        self, sector: SectorInfo, start_date: date, end_date: date
    ) -> list[PricePoint]:
        self._bootstrap(sector)
        today_in_china = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        candidate_dates = (today_in_china, date.today())
        last_error: Exception | None = None

        for token_date in dict.fromkeys(candidate_dates):
            token = hashlib.md5(token_date.isoformat().encode()).hexdigest()
            try:
                payload = self.client.get_json(
                    f"{LEGULEGU_ROOT}/api/stockdata/sw-industry-2021",
                    params={"industryCode": f"{sector.code}.SI", "token": token},
                    headers=self._headers(sector),
                    timeout=30,
                )
                points = []
                for row in payload.get("data") or []:
                    try:
                        trade_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
                        close = float(row["indexClose"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if start_date <= trade_date <= end_date and close > 0:
                        points.append(PricePoint(trade_date, close))
                if points:
                    return sorted(points, key=lambda item: item.trade_date)
            except HttpRequestError as exc:
                last_error = exc

        raise HttpRequestError(
            f"Legulegu returned no index history for {sector.name}: {last_error or 'empty data'}"
        )

    @staticmethod
    def _eastmoney_secid(stock_code: str) -> str:
        market = "1" if stock_code.startswith(("5", "6", "9")) else "0"
        return f"{market}.{stock_code}"

    def _fetch_industry_batch(self, batch: list[str]) -> dict[str, str]:
        try:
            payload = self.client.get_json(
                EASTMONEY_BATCH_QUOTE_URL,
                params={
                    "secids": ",".join(self._eastmoney_secid(code) for code in batch),
                    "fields": "f12,f14,f100",
                },
                timeout=30,
            )
        except HttpRequestError as exc:
            if len(batch) == 1:
                logger.warning("Industry lookup failed for %s: %s", batch[0], exc)
                return {}
            midpoint = len(batch) // 2
            return {
                **self._fetch_industry_batch(batch[:midpoint]),
                **self._fetch_industry_batch(batch[midpoint:]),
            }

        output = {}
        for row in (payload.get("data") or {}).get("diff") or []:
            stock_code = str(row.get("f12") or "").zfill(6)
            industry_name = str(row.get("f100") or "").strip()
            if stock_code in batch and industry_name:
                output[stock_code] = industry_name
        return output

    def _stock_industries(self, stock_codes: set[str]) -> dict[str, str]:
        output = {}
        codes = sorted(stock_codes)
        for offset in range(0, len(codes), 50):
            output.update(self._fetch_industry_batch(codes[offset : offset + 50]))
        return output

    def refresh_unknown(self, stock_codes: set[str]) -> set[str]:
        remaining = self.repository.missing(stock_codes)
        if not remaining:
            return set()

        industries = self._stock_industries(remaining)
        updates = {}
        for stock_code in remaining:
            industry_name = industries.get(stock_code)
            if not industry_name:
                continue
            sector = INDUSTRY_PARENT_OVERRIDES.get(industry_name)
            if sector is not None:
                updates[stock_code] = sector

        if updates:
            self.repository.update(updates)
        unresolved = remaining - updates.keys()
        if unresolved:
            logger.warning(
                "No SW level-one mapping for %d stocks: %s",
                len(unresolved),
                ", ".join(sorted(unresolved)),
            )
        return unresolved
