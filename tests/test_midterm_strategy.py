import unittest
from datetime import date

from src.midterm_strategy import (
    calculate_relative_strength,
    completed_data_cutoff,
    evaluate_relative_strength,
    pearson_correlation,
)
from src.models import EvaluationStatus, PricePoint
from src.settings import MidtermSettings


FRIDAYS = [
    date(2026, 5, 1),
    date(2026, 5, 8),
    date(2026, 5, 15),
    date(2026, 5, 22),
    date(2026, 5, 29),
]
AS_OF = date(2026, 6, 2)


def history(values, dates=FRIDAYS):
    return [PricePoint(day, value) for day, value in zip(dates, values)]


class MidtermStrategyTest(unittest.TestCase):
    def setUp(self):
        self.settings = MidtermSettings()

    def test_candidate_meets_all_three_filters(self):
        evaluation = evaluate_relative_strength(
            history([100, 104, 108, 113, 118]),
            history([100, 101, 102, 103, 104]),
            AS_OF,
            self.settings,
        )

        self.assertEqual(evaluation.status, EvaluationStatus.CANDIDATE)
        self.assertEqual(len(evaluation.metrics.weeks), 4)
        self.assertGreater(evaluation.metrics.excess_return_pct, 8)

    def test_rejects_sector_that_already_gained_more_than_five_percent(self):
        evaluation = evaluate_relative_strength(
            history([100, 110, 120, 130, 140]),
            history([100, 102, 104, 106, 108]),
            AS_OF,
            self.settings,
        )

        self.assertEqual(evaluation.status, EvaluationStatus.REJECTED)
        self.assertEqual(evaluation.reason, "sector_gain_too_high")

    def test_rejects_when_one_week_is_not_stronger(self):
        evaluation = evaluate_relative_strength(
            history([100, 99, 110, 120, 130]),
            history([100, 100, 101, 102, 103]),
            AS_OF,
            self.settings,
        )

        self.assertEqual(evaluation.status, EvaluationStatus.REJECTED)
        self.assertEqual(evaluation.reason, "not_stronger_every_week")

    def test_current_incomplete_week_is_excluded(self):
        dates = FRIDAYS + [date(2026, 6, 1)]
        metrics = calculate_relative_strength(
            history([100, 104, 108, 113, 118, 1000], dates),
            history([100, 101, 102, 103, 104, 1000], dates),
            AS_OF,
            weeks=4,
        )

        self.assertEqual(metrics.weeks[-1].end_date, date(2026, 5, 29))
        self.assertLess(metrics.stock_return_pct, 20)

    def test_weekend_includes_the_just_completed_week(self):
        self.assertEqual(completed_data_cutoff(date(2026, 5, 31)), date(2026, 5, 31))
        self.assertEqual(completed_data_cutoff(AS_OF), date(2026, 5, 31))

    def test_correlation_is_calculated_from_aligned_returns(self):
        self.assertAlmostEqual(pearson_correlation([1, 2, 3], [2, 4, 6]), 1.0)
        self.assertIsNone(pearson_correlation([1, 1, 1], [2, 3, 4]))

    def test_insufficient_history_is_unavailable(self):
        evaluation = evaluate_relative_strength(
            history([100, 110], FRIDAYS[:2]),
            history([100, 101], FRIDAYS[:2]),
            AS_OF,
            self.settings,
        )

        self.assertEqual(evaluation.status, EvaluationStatus.UNAVAILABLE)
        self.assertEqual(evaluation.reason, "insufficient_history")


if __name__ == "__main__":
    unittest.main()
