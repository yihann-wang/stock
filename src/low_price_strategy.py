"""Pure low-price, near-maturity convertible-bond strategy."""

from collections import Counter
from datetime import date

from .models import BondQuote, LowPriceCandidate, LowPriceScanReport
from .settings import LowPriceSettings


def scan_low_price_bonds(
    bonds: list[BondQuote], as_of: date, settings: LowPriceSettings
) -> LowPriceScanReport:
    report = LowPriceScanReport(total_bonds=len(bonds))
    rejected: Counter[str] = Counter()
    unavailable: Counter[str] = Counter()

    if not settings.enabled:
        report.rejected_reasons = {"strategy_disabled": len(bonds)}
        return report

    max_days = int(settings.max_years_to_expire * 365)
    for bond in bonds:
        if bond.bond_price is None:
            unavailable["missing_bond_price"] += 1
            continue
        if bond.expire_date is None:
            unavailable["missing_expire_date"] += 1
            continue

        days_to_expire = (bond.expire_date - as_of).days
        if days_to_expire <= 0:
            rejected["expired"] += 1
        elif bond.bond_price >= settings.max_bond_price:
            rejected["price_not_below_limit"] += 1
        elif days_to_expire > max_days:
            rejected["maturity_too_far"] += 1
        else:
            report.candidates.append(LowPriceCandidate(bond, days_to_expire))

    report.candidates.sort(
        key=lambda candidate: (
            candidate.days_to_expire,
            candidate.bond.bond_price or float("inf"),
        )
    )
    report.rejected_reasons = dict(rejected)
    report.unavailable_reasons = dict(unavailable)
    return report
