"""Domain models shared by providers, strategies, and notifications."""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


@dataclass(frozen=True)
class PricePoint:
    trade_date: date
    close: float


@dataclass(frozen=True)
class BondQuote:
    bond_code: str
    bond_name: str
    bond_price: float | None
    stock_code: str
    stock_name: str
    expire_date: date | None


@dataclass(frozen=True)
class SectorInfo:
    code: str
    name: str


@dataclass(frozen=True)
class LowPriceCandidate:
    bond: BondQuote
    days_to_expire: int


@dataclass(frozen=True)
class WeeklyPerformance:
    start_date: date
    end_date: date
    stock_return_pct: float
    sector_return_pct: float
    excess_return_pct: float


@dataclass(frozen=True)
class RelativeStrengthMetrics:
    stock_return_pct: float
    sector_return_pct: float
    excess_return_pct: float
    correlation: float | None
    weeks: tuple[WeeklyPerformance, ...]


class EvaluationStatus(str, Enum):
    CANDIDATE = "candidate"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class MidtermEvaluation:
    status: EvaluationStatus
    reason: str
    metrics: RelativeStrengthMetrics | None = None


@dataclass(frozen=True)
class MidtermCandidate:
    stock_code: str
    stock_name: str
    bond_code: str
    bond_name: str
    sector: SectorInfo
    metrics: RelativeStrengthMetrics


@dataclass
class LowPriceScanReport:
    total_bonds: int
    candidates: list[LowPriceCandidate] = field(default_factory=list)
    rejected_reasons: dict[str, int] = field(default_factory=dict)
    unavailable_reasons: dict[str, int] = field(default_factory=dict)


@dataclass
class MidtermScanReport:
    total_stocks: int
    evaluated_stocks: int = 0
    candidates: list[MidtermCandidate] = field(default_factory=list)
    rejected_reasons: dict[str, int] = field(default_factory=dict)
    unavailable_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def coverage_ratio(self) -> float:
        if self.total_stocks == 0:
            return 0.0
        return self.evaluated_stocks / self.total_stocks


@dataclass(frozen=True)
class WeeklyScanReport:
    as_of: date
    low_price: LowPriceScanReport
    midterm: MidtermScanReport
    elapsed_seconds: float
