import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from src import cb_strategy


STRENGTH = {
    "sector_name": "测试行业",
    "stock_4w_return": 12.0,
    "sector_4w_return": 2.0,
    "excess_4w_return": 10.0,
    "return_correlation": 0.42,
    "weekly_outperform_count": 4,
    "observed_weeks": 4,
}


class CBMidtermStrategyTest(unittest.TestCase):
    def setUp(self):
        self.config = {
            "cb_midterm_stock": {
                "enabled": True,
                "max_results": 20,
                "relative_strength": {},
            },
            "cb_low_price_maturity": {
                "enabled": True,
                "max_years_to_expire": 1.5,
                "max_bond_price": 100,
                "max_results": 20,
            },
        }
        self.candidate = {
            "bond_code": "123456",
            "bond_name": "测试转债",
            "bond_price": 99.99,
            "stock_code": "300001",
            "stock_name": "测试股份",
            "expire_date": (datetime.now().date() + timedelta(days=365)).strftime("%Y-%m-%d"),
        }

    @patch.object(cb_strategy, "_calc_relative_strength", return_value=STRENGTH)
    @patch.object(cb_strategy, "_get_stock_sector", return_value=("测试行业", "801000"))
    @patch.object(cb_strategy, "_prepare_sw_sector_map")
    @patch.object(cb_strategy, "load_config")
    def test_midterm_candidate_is_independent_of_bond_price_and_expiry(
        self, load_config, _prepare_sector, _get_sector, _strength
    ):
        load_config.return_value = self.config
        candidate = {**self.candidate, "bond_price": 130, "expire_date": "2035-01-01"}

        results = cb_strategy.scan_cb_maturity_play([candidate])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].bond_code, "123456")
        self.assertEqual(results[0].return_correlation, 0.42)

    @patch.object(cb_strategy, "load_config")
    def test_low_price_strategy_rejects_bond_at_or_above_100(self, load_config):
        load_config.return_value = self.config
        candidate = {**self.candidate, "bond_price": 100}

        self.assertEqual(cb_strategy.scan_cb_low_price_maturity([candidate]), [])

    @patch.object(cb_strategy, "load_config")
    def test_low_price_strategy_rejects_bond_beyond_one_and_a_half_years(self, load_config):
        load_config.return_value = self.config
        candidate = {
            **self.candidate,
            "expire_date": (datetime.now().date() + timedelta(days=548)).strftime("%Y-%m-%d"),
        }

        self.assertEqual(cb_strategy.scan_cb_low_price_maturity([candidate]), [])

    @patch.object(cb_strategy, "_calc_relative_strength", return_value=None)
    @patch.object(cb_strategy, "_get_stock_sector", return_value=("测试行业", "801000"))
    @patch.object(cb_strategy, "_prepare_sw_sector_map")
    @patch.object(cb_strategy, "load_config")
    def test_requires_four_week_relative_strength(
        self, load_config, _prepare_sector, _get_sector, _strength
    ):
        load_config.return_value = self.config

        self.assertEqual(cb_strategy.scan_cb_maturity_play([self.candidate]), [])

    def test_pearson_correlation_uses_return_series(self):
        self.assertAlmostEqual(cb_strategy._pearson_corr([1, 2, 3], [2, 4, 6]), 1.0)


if __name__ == "__main__":
    unittest.main()
