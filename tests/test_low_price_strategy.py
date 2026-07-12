import unittest
from datetime import date, timedelta

from src.low_price_strategy import scan_low_price_bonds
from src.models import BondQuote
from src.settings import LowPriceSettings


AS_OF = date(2026, 7, 13)


def bond(price=99.99, days=365, expire_date=None):
    return BondQuote(
        bond_code="123456",
        bond_name="测试转债",
        bond_price=price,
        stock_code="300001",
        stock_name="测试股份",
        expire_date=expire_date
        if expire_date is not None
        else AS_OF + timedelta(days=days),
    )


class LowPriceStrategyTest(unittest.TestCase):
    def setUp(self):
        self.settings = LowPriceSettings()

    def test_accepts_strictly_below_100_and_547_days(self):
        report = scan_low_price_bonds(
            [bond(price=99.99, days=547)], AS_OF, self.settings
        )

        self.assertEqual(len(report.candidates), 1)
        self.assertEqual(report.candidates[0].days_to_expire, 547)

    def test_rejects_price_equal_to_100(self):
        report = scan_low_price_bonds([bond(price=100)], AS_OF, self.settings)

        self.assertEqual(report.candidates, [])
        self.assertEqual(report.rejected_reasons, {"price_not_below_limit": 1})

    def test_rejects_548_days(self):
        report = scan_low_price_bonds([bond(days=548)], AS_OF, self.settings)

        self.assertEqual(report.candidates, [])
        self.assertEqual(report.rejected_reasons, {"maturity_too_far": 1})

    def test_missing_values_are_unavailable_not_rejected(self):
        no_price = bond(price=None)
        no_expiry = bond(expire_date=None)
        no_expiry = BondQuote(**{**no_expiry.__dict__, "expire_date": None})

        report = scan_low_price_bonds([no_price, no_expiry], AS_OF, self.settings)

        self.assertEqual(
            report.unavailable_reasons,
            {"missing_bond_price": 1, "missing_expire_date": 1},
        )


if __name__ == "__main__":
    unittest.main()
