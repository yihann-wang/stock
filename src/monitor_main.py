"""Command-line entry point for the weekly two-strategy scan."""

import argparse
import logging
from datetime import date, datetime
from pathlib import Path

from .bond_provider import EastmoneyBondProvider
from .http_client import DirectHttpClient
from .market_provider import (
    SectorMapRepository,
    ShenwanSectorProvider,
    TencentStockHistoryProvider,
)
from .notifier import DingTalkNotifier, render_weekly_report
from .scanner import run_weekly_scan
from .settings import ROOT_DIR, load_settings


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class CoverageError(RuntimeError):
    pass


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the weekly stock screen")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT_DIR / "config.yml",
        help="settings YAML path",
    )
    parser.add_argument("--as-of", type=_parse_date, default=date.today())
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the report without sending DingTalk",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    settings = load_settings(args.config)
    data_client = DirectHttpClient(retries=3, backoff_seconds=1.0)
    market_client = DirectHttpClient(retries=3, backoff_seconds=1.0)
    sector_repository = SectorMapRepository(settings.market.sector_map_path)

    report = run_weekly_scan(
        settings=settings,
        bond_provider=EastmoneyBondProvider(data_client, settings.name_overrides),
        stock_provider=TencentStockHistoryProvider(market_client),
        sector_provider=ShenwanSectorProvider(market_client, sector_repository),
        sector_repository=sector_repository,
        as_of=args.as_of,
    )
    title, markdown = render_weekly_report(report, settings)
    print(markdown)

    if args.dry_run:
        logger.info("Dry run complete; DingTalk was not called")
    else:
        DingTalkNotifier(DirectHttpClient(retries=1), settings.dingtalk).send(
            title, markdown
        )

    if (
        settings.midterm.enabled
        and report.midterm.coverage_ratio < settings.midterm.min_coverage_ratio
    ):
        raise CoverageError(
            f"midterm coverage {report.midterm.coverage_ratio:.1%} is below "
            f"{settings.midterm.min_coverage_ratio:.1%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
