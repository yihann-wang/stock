"""Pure four-week stock-versus-sector relative-strength strategy."""

import math
from datetime import date, timedelta

from .models import (
    EvaluationStatus,
    MidtermEvaluation,
    PricePoint,
    RelativeStrengthMetrics,
    WeeklyPerformance,
)
from .settings import MidtermSettings


def completed_data_cutoff(as_of: date) -> date:
    """Return the last date belonging to a completed trading week."""
    if as_of.weekday() <= 4:
        week_start = as_of - timedelta(days=as_of.weekday())
        return week_start - timedelta(days=1)
    return as_of


def pearson_correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_variance = sum((x - left_mean) ** 2 for x in left)
    right_variance = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_variance * right_variance)
    if denominator == 0:
        return None
    return numerator / denominator


def _return_pct(start: float, end: float) -> float:
    return (end / start - 1) * 100


def calculate_relative_strength(
    stock_history: list[PricePoint],
    sector_history: list[PricePoint],
    as_of: date,
    weeks: int,
) -> RelativeStrengthMetrics | None:
    cutoff = completed_data_cutoff(as_of)
    stock_by_date = {
        point.trade_date: point.close
        for point in stock_history
        if point.trade_date <= cutoff
    }
    sector_by_date = {
        point.trade_date: point.close
        for point in sector_history
        if point.trade_date <= cutoff
    }
    common_dates = sorted(stock_by_date.keys() & sector_by_date.keys())
    if not common_dates:
        return None

    weekly_closes: dict[tuple[int, int], tuple[date, float, float]] = {}
    for trade_date in common_dates:
        iso = trade_date.isocalendar()
        weekly_closes[(iso.year, iso.week)] = (
            trade_date,
            stock_by_date[trade_date],
            sector_by_date[trade_date],
        )

    observations = list(weekly_closes.values())
    if len(observations) < weeks + 1:
        return None
    observations = observations[-(weeks + 1) :]

    weekly_performance = []
    for previous, current in zip(observations, observations[1:]):
        stock_return = _return_pct(previous[1], current[1])
        sector_return = _return_pct(previous[2], current[2])
        weekly_performance.append(
            WeeklyPerformance(
                start_date=previous[0],
                end_date=current[0],
                stock_return_pct=stock_return,
                sector_return_pct=sector_return,
                excess_return_pct=stock_return - sector_return,
            )
        )

    first, last = observations[0], observations[-1]
    stock_return = _return_pct(first[1], last[1])
    sector_return = _return_pct(first[2], last[2])

    correlation_dates = [
        trade_date for trade_date in common_dates if first[0] <= trade_date <= last[0]
    ]
    stock_daily_returns = []
    sector_daily_returns = []
    for previous_date, current_date in zip(correlation_dates, correlation_dates[1:]):
        stock_daily_returns.append(
            stock_by_date[current_date] / stock_by_date[previous_date] - 1
        )
        sector_daily_returns.append(
            sector_by_date[current_date] / sector_by_date[previous_date] - 1
        )

    return RelativeStrengthMetrics(
        stock_return_pct=stock_return,
        sector_return_pct=sector_return,
        excess_return_pct=stock_return - sector_return,
        correlation=pearson_correlation(stock_daily_returns, sector_daily_returns),
        weeks=tuple(weekly_performance),
    )


def evaluate_relative_strength(
    stock_history: list[PricePoint],
    sector_history: list[PricePoint],
    as_of: date,
    settings: MidtermSettings,
) -> MidtermEvaluation:
    metrics = calculate_relative_strength(
        stock_history, sector_history, as_of, settings.weeks
    )
    if metrics is None:
        return MidtermEvaluation(EvaluationStatus.UNAVAILABLE, "insufficient_history")

    latest_date = metrics.weeks[-1].end_date
    if (completed_data_cutoff(as_of) - latest_date).days > 14:
        return MidtermEvaluation(EvaluationStatus.UNAVAILABLE, "stale_history", metrics)
    if metrics.sector_return_pct > settings.max_sector_gain_pct:
        return MidtermEvaluation(
            EvaluationStatus.REJECTED, "sector_gain_too_high", metrics
        )
    if metrics.excess_return_pct < settings.min_excess_return_pct:
        return MidtermEvaluation(
            EvaluationStatus.REJECTED, "insufficient_excess", metrics
        )
    if settings.require_every_week_outperform and any(
        week.excess_return_pct < settings.min_weekly_excess_pct
        for week in metrics.weeks
    ):
        return MidtermEvaluation(
            EvaluationStatus.REJECTED, "not_stronger_every_week", metrics
        )
    return MidtermEvaluation(EvaluationStatus.CANDIDATE, "candidate", metrics)
