import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.market_provider import SectorMapRepository
from src.models import BondQuote, PricePoint, SectorInfo
from src.scanner import scan_midterm_stocks
from src.settings import AppSettings, MarketSettings, MidtermSettings


DATES = [
    date(2026, 5, 1),
    date(2026, 5, 8),
    date(2026, 5, 15),
    date(2026, 5, 22),
    date(2026, 5, 29),
]


def points(values):
    return [PricePoint(day, value) for day, value in zip(DATES, values)]


class FakeStockProvider:
    def get_history(self, stock_code, start_date, end_date):
        if stock_code == "300002":
            raise RuntimeError("upstream failed")
        return points([100, 104, 108, 113, 118])


class FakeSectorProvider:
    def get_history(self, sector, start_date, end_date):
        return points([100, 101, 102, 103, 104])


class ScannerTest(unittest.TestCase):
    def test_coverage_excludes_unavailable_but_not_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sectors.json"
            repository = SectorMapRepository(path)
            repository.update(
                {
                    "300001": SectorInfo("801080", "电子"),
                    "300002": SectorInfo("801080", "电子"),
                }
            )
            settings = AppSettings(
                midterm=MidtermSettings(max_workers=2),
                market=MarketSettings(path, refresh_unknown_sectors=False),
            )
            bonds = [
                BondQuote("123001", "甲转债", 130, "300001", "甲股份", None),
                BondQuote("123002", "乙转债", 90, "300002", "乙股份", None),
            ]

            report = scan_midterm_stocks(
                bonds,
                date(2026, 6, 2),
                settings,
                FakeStockProvider(),
                FakeSectorProvider(),
                repository,
            )

        self.assertEqual(report.total_stocks, 2)
        self.assertEqual(report.evaluated_stocks, 1)
        self.assertEqual(report.coverage_ratio, 0.5)
        self.assertEqual(len(report.candidates), 1)
        self.assertEqual(report.unavailable_reasons, {"missing_stock_history": 1})


if __name__ == "__main__":
    unittest.main()
