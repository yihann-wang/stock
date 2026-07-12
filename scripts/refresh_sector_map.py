"""Refresh the committed SW level-one mapping for current bond underlyings."""

import logging
from datetime import date

from src.bond_provider import EastmoneyBondProvider
from src.http_client import DirectHttpClient
from src.market_provider import SectorMapRepository, ShenwanSectorProvider
from src.settings import load_settings


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> int:
    settings = load_settings()
    client = DirectHttpClient(retries=3, backoff_seconds=1.0)
    bonds = EastmoneyBondProvider(client, settings.name_overrides).list_active_bonds(
        date.today()
    )
    stock_codes = {bond.stock_code for bond in bonds if bond.stock_code != "000000"}
    repository = SectorMapRepository(settings.market.sector_map_path)
    provider = ShenwanSectorProvider(client, repository)
    unresolved = provider.refresh_unknown(stock_codes)
    repository.save()
    print(
        f"Saved {len(stock_codes) - len(unresolved)}/{len(stock_codes)} mappings to "
        f"{repository.path}"
    )
    if unresolved:
        print("Unresolved: " + ", ".join(sorted(unresolved)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
