"""Typed application settings loaded once at process startup."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class LowPriceSettings:
    enabled: bool = True
    max_bond_price: float = 100.0
    max_years_to_expire: float = 1.5
    max_results: int = 20


@dataclass(frozen=True)
class MidtermSettings:
    enabled: bool = True
    weeks: int = 4
    max_sector_gain_pct: float = 5.0
    min_excess_return_pct: float = 8.0
    min_weekly_excess_pct: float = 0.0
    require_every_week_outperform: bool = True
    max_results: int = 20
    history_calendar_days: int = 90
    max_workers: int = 4
    min_coverage_ratio: float = 0.8


@dataclass(frozen=True)
class MarketSettings:
    sector_map_path: Path = ROOT_DIR / "data" / "cb_stock_sectors.json"
    refresh_unknown_sectors: bool = True


@dataclass(frozen=True)
class DingTalkSettings:
    enabled: bool = True
    webhook_env: str = "DINGTALK_WEBHOOK"
    secret_env: str = "DINGTALK_SECRET"


@dataclass(frozen=True)
class AppSettings:
    low_price: LowPriceSettings = field(default_factory=LowPriceSettings)
    midterm: MidtermSettings = field(default_factory=MidtermSettings)
    market: MarketSettings = field(default_factory=MarketSettings)
    dingtalk: DingTalkSettings = field(default_factory=DingTalkSettings)
    name_overrides: dict[str, tuple[str, str]] = field(default_factory=dict)


def _section(data: dict, key: str) -> dict:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"config section '{key}' must be a mapping")
    return value


def load_settings(path: Path | None = None) -> AppSettings:
    config_path = path or ROOT_DIR / "config.yml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    low = _section(raw, "low_price")
    mid = _section(raw, "midterm")
    market = _section(raw, "market")
    notification = _section(raw, "notification")
    dingtalk = notification.get("dingtalk", {}) or {}

    settings = AppSettings(
        low_price=LowPriceSettings(**low),
        midterm=MidtermSettings(**mid),
        market=MarketSettings(
            sector_map_path=ROOT_DIR
            / market.get("sector_map_path", "data/cb_stock_sectors.json"),
            refresh_unknown_sectors=market.get("refresh_unknown_sectors", True),
        ),
        dingtalk=DingTalkSettings(**dingtalk),
        name_overrides={
            str(code): tuple(names[:2])
            for code, names in (_section(raw, "name_overrides") or {}).items()
            if isinstance(names, list) and len(names) >= 2
        },
    )
    _validate(settings)
    return settings


def _validate(settings: AppSettings) -> None:
    if settings.low_price.max_bond_price <= 0:
        raise ValueError("low_price.max_bond_price must be positive")
    if settings.low_price.max_years_to_expire <= 0:
        raise ValueError("low_price.max_years_to_expire must be positive")
    if settings.midterm.weeks <= 0:
        raise ValueError("midterm.weeks must be positive")
    if settings.midterm.history_calendar_days < settings.midterm.weeks * 7:
        raise ValueError("midterm.history_calendar_days is too short")
    if settings.midterm.max_workers <= 0:
        raise ValueError("midterm.max_workers must be positive")
    if not 0 <= settings.midterm.min_coverage_ratio <= 1:
        raise ValueError("midterm.min_coverage_ratio must be between 0 and 1")


def get_required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise EnvironmentError(f"environment variable {name} is not set")
    return value
