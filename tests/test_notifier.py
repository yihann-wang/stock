import unittest
from datetime import date
from unittest.mock import patch

from src.models import (
    BondQuote,
    LowPriceCandidate,
    LowPriceScanReport,
    MidtermCandidate,
    MidtermScanReport,
    RelativeStrengthMetrics,
    SectorInfo,
    WeeklyPerformance,
    WeeklyScanReport,
)
from src.notifier import (
    DingTalkNotifier,
    NotificationError,
    _signed_url,
    render_weekly_report,
)
from src.settings import AppSettings, DingTalkSettings


class NotifierTest(unittest.TestCase):
    def test_combined_report_contains_both_logic_details_and_correlation(self):
        week = WeeklyPerformance(date(2026, 5, 1), date(2026, 5, 8), 3, 1, 2)
        metrics = RelativeStrengthMetrics(15, 4, 11, 0.42, (week, week, week, week))
        report = WeeklyScanReport(
            as_of=date(2026, 6, 2),
            low_price=LowPriceScanReport(
                total_bonds=1,
                candidates=[
                    LowPriceCandidate(
                        BondQuote(
                            "123001",
                            "甲转债",
                            99.9,
                            "300001",
                            "甲股份",
                            date(2027, 1, 1),
                        ),
                        213,
                    )
                ],
            ),
            midterm=MidtermScanReport(
                total_stocks=1,
                evaluated_stocks=1,
                candidates=[
                    MidtermCandidate(
                        "300001",
                        "甲股份",
                        "123001",
                        "甲转债",
                        SectorInfo("801080", "电子"),
                        metrics,
                    )
                ],
            ),
            elapsed_seconds=1.2,
        )

        title, markdown = render_weekly_report(report, AppSettings())

        self.assertIn("每周双策略筛选", title)
        self.assertIn("严格低于 100 元", markdown)
        self.assertIn("相关系数 0.420", markdown)
        self.assertIn("每周都不弱于行业", markdown)

    def test_signature_uses_ampersand_for_existing_query(self):
        url = _signed_url("https://example.test/send?access_token=x", "secret", 123)

        self.assertIn("access_token=x&timestamp=123&sign=", url)

    @patch.dict(
        "os.environ",
        {
            "DINGTALK_WEBHOOK": "https://example.test/send?token=x",
            "DINGTALK_SECRET": "s",
        },
    )
    def test_dingtalk_rejection_raises_and_fails_the_job(self):
        class RejectedClient:
            def post_json(self, url, payload):
                return {"errcode": 310000, "errmsg": "rejected"}

        with self.assertRaises(NotificationError):
            DingTalkNotifier(RejectedClient(), DingTalkSettings()).send("title", "body")


if __name__ == "__main__":
    unittest.main()
