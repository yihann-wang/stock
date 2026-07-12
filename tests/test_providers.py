import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from src.bond_provider import EastmoneyBondProvider
from src.market_provider import (
    SectorMapRepository,
    ShenwanSectorProvider,
    TencentStockHistoryProvider,
)
from src.models import SectorInfo


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.payload

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return SimpleNamespace(text="kline=" + json.dumps(self.payload))


class ProviderTest(unittest.TestCase):
    def test_bond_provider_keeps_missing_price_for_stock_universe(self):
        payload = {
            "success": True,
            "result": {
                "pages": 1,
                "data": [
                    {
                        "SECURITY_CODE": "123001",
                        "SECURITY_NAME_ABBR": "甲转债",
                        "CONVERT_STOCK_CODE": "300001",
                        "SECURITY_SHORT_NAME": "甲股份",
                        "LISTING_DATE": "2024-01-01",
                        "DELIST_DATE": None,
                        "EXPIRE_DATE": "2027-01-01",
                        "BOND_PRICE": None,
                    },
                    {
                        "SECURITY_CODE": "123002",
                        "CONVERT_STOCK_CODE": "300002",
                        "LISTING_DATE": "2027-01-01",
                    },
                ],
            },
        }

        bonds = EastmoneyBondProvider(FakeClient(payload)).list_active_bonds(
            date(2026, 7, 13)
        )

        self.assertEqual(len(bonds), 1)
        self.assertIsNone(bonds[0].bond_price)
        self.assertEqual(bonds[0].stock_code, "300001")

    def test_tencent_provider_reads_qfq_close(self):
        payload = {
            "data": {
                "sz300001": {
                    "qfqday": [
                        ["2026-07-09", "10", "10.5", "11", "9", "100"],
                        ["2026-07-10", "10.5", "10.8", "11", "10", "120"],
                    ]
                }
            }
        }

        points = TencentStockHistoryProvider(FakeClient(payload)).get_history(
            "300001", date(2026, 7, 1), date(2026, 7, 13)
        )

        self.assertEqual([point.close for point in points], [10.5, 10.8])

    def test_sector_history_uses_index_close(self):
        client = FakeClient(
            {
                "data": [
                    {"bargaindate": "2026-07-09", "closeindex": 1000},
                    {"bargaindate": "2026-07-10", "closeindex": 1010},
                ]
            }
        )
        repository = SectorMapRepository.__new__(SectorMapRepository)
        provider = ShenwanSectorProvider(client, repository)

        points = provider.get_history(
            SectorInfo("801080", "电子"),
            date(2026, 7, 1),
            date(2026, 7, 13),
        )

        self.assertEqual([point.close for point in points], [1000, 1010])

    def test_unknown_sector_uses_explicit_parent_overrides(self):
        client = FakeClient(
            {
                "data": {
                    "diff": [
                        {"f12": "600001", "f100": "半导体"},
                        {"f12": "300001", "f100": "半导体"},
                        {"f12": "601128", "f100": "银行Ⅱ"},
                    ]
                }
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = SectorMapRepository(Path(directory) / "sectors.json")
            provider = ShenwanSectorProvider(client, repository)

            unresolved = provider.refresh_unknown({"300001", "601128"})

            self.assertEqual(unresolved, set())
            self.assertEqual(repository.get("300001"), SectorInfo("801080", "电子"))
            self.assertEqual(repository.get("601128"), SectorInfo("801780", "银行"))


if __name__ == "__main__":
    unittest.main()
