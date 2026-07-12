"""Convertible-bond universe and quote provider."""

import logging
from datetime import date, datetime

from .http_client import DirectHttpClient, HttpRequestError
from .models import BondQuote


logger = logging.getLogger(__name__)

EASTMONEY_BOND_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _parse_date(value: object) -> date | None:
    text = str(value or "")[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_positive_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


class EastmoneyBondProvider:
    """Load every currently listed convertible bond without strategy filters."""

    def __init__(
        self,
        client: DirectHttpClient,
        name_overrides: dict[str, tuple[str, str]] | None = None,
    ):
        self.client = client
        self.name_overrides = name_overrides or {}

    def list_active_bonds(self, as_of: date) -> list[BondQuote]:
        bonds: list[BondQuote] = []
        page = 1

        while True:
            payload = self.client.get_json(
                EASTMONEY_BOND_URL,
                params={
                    "reportName": "RPT_BOND_CB_LIST",
                    "columns": (
                        "SECURITY_CODE,SECURITY_NAME_ABBR,CONVERT_STOCK_CODE,"
                        "SECURITY_SHORT_NAME,LISTING_DATE,DELIST_DATE,EXPIRE_DATE"
                    ),
                    "quoteColumns": "f2~10~SECURITY_CODE~BOND_PRICE",
                    "pageSize": 500,
                    "pageNumber": page,
                    "sortColumns": "SECURITY_CODE",
                    "sortTypes": 1,
                    "source": "WEB",
                    "client": "WEB",
                },
                timeout=45,
            )
            if not payload.get("success"):
                raise HttpRequestError(
                    f"Eastmoney bond API rejected page {page}: {payload.get('message')}"
                )

            result = payload.get("result") or {}
            rows = result.get("data") or []
            if not rows:
                break

            for row in rows:
                bond = self._to_active_bond(row, as_of)
                if bond is not None:
                    bonds.append(bond)

            if page >= int(result.get("pages") or 1):
                break
            page += 1

        if not bonds:
            raise HttpRequestError("Eastmoney returned no active convertible bonds")

        deduplicated = {bond.bond_code: bond for bond in bonds}
        output = sorted(deduplicated.values(), key=lambda item: item.bond_code)
        logger.info("Loaded %d active convertible bonds", len(output))
        return output

    def _to_active_bond(self, row: dict, as_of: date) -> BondQuote | None:
        listing_date = _parse_date(row.get("LISTING_DATE"))
        delist_date = _parse_date(row.get("DELIST_DATE"))
        if listing_date is None or listing_date > as_of:
            return None
        if delist_date is not None and delist_date <= as_of:
            return None

        bond_code = str(row.get("SECURITY_CODE") or "").zfill(6)
        stock_code = str(row.get("CONVERT_STOCK_CODE") or "").zfill(6)
        if (
            len(bond_code) != 6
            or len(stock_code) != 6
            or not bond_code.isdigit()
            or not stock_code.isdigit()
            or bond_code == "000000"
            or stock_code == "000000"
        ):
            return None

        override = self.name_overrides.get(bond_code)
        bond_name = str(row.get("SECURITY_NAME_ABBR") or "").strip()
        stock_name = str(row.get("SECURITY_SHORT_NAME") or "").strip()
        if override:
            bond_name, stock_name = override

        return BondQuote(
            bond_code=bond_code,
            bond_name=bond_name,
            bond_price=_parse_positive_float(row.get("BOND_PRICE")),
            stock_code=stock_code,
            stock_name=stock_name,
            expire_date=_parse_date(row.get("EXPIRE_DATE")),
        )
