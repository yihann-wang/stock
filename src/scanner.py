"""Application orchestration for the two retained screening strategies."""

import logging
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from .bond_provider import EastmoneyBondProvider
from .low_price_strategy import scan_low_price_bonds
from .market_provider import (
    SectorMapRepository,
    ShenwanSectorProvider,
    TencentStockHistoryProvider,
)
from .midterm_strategy import evaluate_relative_strength
from .models import (
    BondQuote,
    EvaluationStatus,
    MidtermCandidate,
    MidtermEvaluation,
    MidtermScanReport,
    WeeklyScanReport,
)
from .settings import AppSettings


logger = logging.getLogger(__name__)


def _unique_underlyings(bonds: list[BondQuote]) -> dict[str, BondQuote]:
    output: dict[str, BondQuote] = {}
    for bond in sorted(bonds, key=lambda item: item.bond_code):
        if bond.stock_code and bond.stock_code != "000000":
            output.setdefault(bond.stock_code, bond)
    return output


def scan_midterm_stocks(
    bonds: list[BondQuote],
    as_of: date,
    settings: AppSettings,
    stock_provider: TencentStockHistoryProvider,
    sector_provider: ShenwanSectorProvider,
    sector_repository: SectorMapRepository,
) -> MidtermScanReport:
    underlyings = _unique_underlyings(bonds)
    report = MidtermScanReport(total_stocks=len(underlyings))
    if not settings.midterm.enabled:
        report.rejected_reasons = {"strategy_disabled": len(underlyings)}
        return report

    stock_codes = set(underlyings)
    missing = sector_repository.missing(stock_codes)
    if missing and settings.market.refresh_unknown_sectors:
        try:
            unresolved = sector_provider.refresh_unknown(stock_codes)
            logger.info(
                "SW sector refresh mapped %d/%d unknown stocks",
                len(missing) - len(unresolved),
                len(missing),
            )
        except Exception as exc:
            logger.warning("SW sector membership refresh failed: %s", exc)

    start_date = as_of - timedelta(days=settings.midterm.history_calendar_days)
    sectors = {
        sector
        for stock_code in stock_codes
        if (sector := sector_repository.get(stock_code)) is not None
    }
    sector_histories = {}
    sector_errors = {}
    for sector in sorted(sectors, key=lambda item: item.code):
        try:
            sector_histories[sector.code] = sector_provider.get_history(
                sector, start_date, as_of
            )
        except Exception as exc:
            logger.warning("Sector history unavailable for %s: %s", sector.name, exc)
            sector_errors[sector.code] = exc

    def evaluate(stock_code: str) -> tuple[str, MidtermEvaluation]:
        sector = sector_repository.get(stock_code)
        if sector is None:
            return stock_code, MidtermEvaluation(
                EvaluationStatus.UNAVAILABLE, "missing_sector_mapping"
            )
        if sector.code in sector_errors or sector.code not in sector_histories:
            return stock_code, MidtermEvaluation(
                EvaluationStatus.UNAVAILABLE, "missing_sector_history"
            )
        try:
            stock_history = stock_provider.get_history(stock_code, start_date, as_of)
        except Exception:
            logger.debug("Stock history unavailable for %s", stock_code, exc_info=True)
            return stock_code, MidtermEvaluation(
                EvaluationStatus.UNAVAILABLE, "missing_stock_history"
            )
        return stock_code, evaluate_relative_strength(
            stock_history,
            sector_histories[sector.code],
            as_of,
            settings.midterm,
        )

    evaluations: list[tuple[str, MidtermEvaluation]] = []
    with ThreadPoolExecutor(max_workers=settings.midterm.max_workers) as executor:
        futures = [executor.submit(evaluate, code) for code in sorted(stock_codes)]
        for position, future in enumerate(as_completed(futures), start=1):
            evaluations.append(future.result())
            if position % 25 == 0 or position == len(futures):
                logger.info("Stock history progress: %d/%d", position, len(futures))

    rejected: Counter[str] = Counter()
    unavailable: Counter[str] = Counter()
    for stock_code, evaluation in evaluations:
        if evaluation.status == EvaluationStatus.UNAVAILABLE:
            unavailable[evaluation.reason] += 1
            continue

        report.evaluated_stocks += 1
        if evaluation.status == EvaluationStatus.REJECTED:
            rejected[evaluation.reason] += 1
            continue

        bond = underlyings[stock_code]
        sector = sector_repository.get(stock_code)
        if sector is None or evaluation.metrics is None:
            unavailable["internal_candidate_error"] += 1
            report.evaluated_stocks -= 1
            continue
        report.candidates.append(
            MidtermCandidate(
                stock_code=stock_code,
                stock_name=bond.stock_name,
                bond_code=bond.bond_code,
                bond_name=bond.bond_name,
                sector=sector,
                metrics=evaluation.metrics,
            )
        )

    report.candidates.sort(
        key=lambda candidate: candidate.metrics.excess_return_pct, reverse=True
    )
    report.rejected_reasons = dict(rejected)
    report.unavailable_reasons = dict(unavailable)
    return report


def run_weekly_scan(
    settings: AppSettings,
    bond_provider: EastmoneyBondProvider,
    stock_provider: TencentStockHistoryProvider,
    sector_provider: ShenwanSectorProvider,
    sector_repository: SectorMapRepository,
    as_of: date,
) -> WeeklyScanReport:
    started = time.monotonic()
    bonds = bond_provider.list_active_bonds(as_of)
    low_price = scan_low_price_bonds(bonds, as_of, settings.low_price)
    midterm = scan_midterm_stocks(
        bonds,
        as_of,
        settings,
        stock_provider,
        sector_provider,
        sector_repository,
    )
    return WeeklyScanReport(
        as_of=as_of,
        low_price=low_price,
        midterm=midterm,
        elapsed_seconds=time.monotonic() - started,
    )
