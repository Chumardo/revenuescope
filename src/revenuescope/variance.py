"""Exact arithmetic bridges for observed period-over-period revenue change."""

from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from operator import index

import duckdb

from .analytics import summarize
from .db import metadata


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError("Period end must be after start.")

    @property
    def days(self) -> int:
        return (self.end - self.start).days


@dataclass(frozen=True)
class RevenueBridge:
    baseline_revenue_cents: int
    current_revenue_cents: int
    baseline_orders: int
    current_orders: int
    baseline_aov_cents: Fraction | None
    current_aov_cents: Fraction | None
    volume_effect_cents: Fraction
    aov_effect_cents: Fraction
    revenue_change_cents: int


@dataclass(frozen=True)
class SegmentContribution:
    segment: str
    baseline_revenue_cents: int
    current_revenue_cents: int
    revenue_change_cents: int


@dataclass(frozen=True)
class VarianceReport:
    baseline: Period
    current: Period
    bridge: RevenueBridge
    contributions: dict[str, tuple[SegmentContribution, ...]]


def revenue_bridge(
    baseline_orders: int, baseline_revenue_cents: int,
    current_orders: int, current_revenue_cents: int,
) -> RevenueBridge:
    """Average the two possible orderings of the volume and AOV effects."""
    n0, r0, n1, r1 = map(index, (baseline_orders, baseline_revenue_cents,
                                current_orders, current_revenue_cents))
    if min(n0, n1) < 0 or (n0 == 0 and r0 != 0) or (n1 == 0 and r1 != 0):
        raise ValueError("Order counts must be nonnegative, and zero orders require zero revenue.")
    a0 = Fraction(r0, n0) if n0 else None
    a1 = Fraction(r1, n1) if n1 else None
    if a0 is None or a1 is None:
        # Missing AOV is unobservable. Use the observed AOV on both sides of
        # the bridge, attributing the entire start/stop effect to volume.
        volume, aov = Fraction(r1 - r0), Fraction(0)
    else:
        volume = (n1 - n0) * (a0 + a1) / 2
        aov = (a1 - a0) * (n0 + n1) / 2
    if volume + aov != r1 - r0:
        raise ArithmeticError("Revenue bridge does not reconcile.")
    return RevenueBridge(r0, r1, n0, n1, a0, a1, volume, aov, r1 - r0)


def compare_periods(
    conn: duckdb.DuckDBPyConnection,
    baseline: Period,
    current: Period,
    *,
    dimensions: tuple[str, ...] = ("category", "region", "channel"),
) -> VarianceReport:
    """Compare nonoverlapping, fully observed order-date windows."""
    meta = metadata(conn)
    if baseline.end > current.start:
        raise ValueError("Baseline must end on or before the current period starts.")
    if baseline.start < meta.coverage_start or current.end > min(meta.coverage_end, meta.data_as_of.date()):
        raise ValueError("The comparison requires two fully observed periods inside dataset coverage.")
    overall0 = summarize(conn, baseline.start, baseline.end)
    overall1 = summarize(conn, current.start, current.end)
    bridge = revenue_bridge(
        overall0["orders"].iat[0], overall0["net_revenue_cents"].iat[0],
        overall1["orders"].iat[0], overall1["net_revenue_cents"].iat[0],
    )

    contributions: dict[str, tuple[SegmentContribution, ...]] = {}
    for dimension in dimensions:
        previous = summarize(conn, baseline.start, baseline.end, dimension=dimension)
        latest = summarize(conn, current.start, current.end, dimension=dimension)
        p = dict(zip(previous["segment"], map(int, previous["net_revenue_cents"]), strict=True))
        c = dict(zip(latest["segment"], map(int, latest["net_revenue_cents"]), strict=True))
        rows = [
            SegmentContribution(key, p.get(key, 0), c.get(key, 0), c.get(key, 0) - p.get(key, 0))
            for key in p.keys() | c.keys()
        ]
        if (
            sum(row.baseline_revenue_cents for row in rows) != bridge.baseline_revenue_cents
            or sum(row.current_revenue_cents for row in rows) != bridge.current_revenue_cents
            or sum(row.revenue_change_cents for row in rows) != bridge.revenue_change_cents
        ):
            raise ArithmeticError(f"{dimension} contribution does not reconcile with overall revenue.")
        contributions[dimension] = tuple(sorted(rows, key=lambda r: (-abs(r.revenue_change_cents), r.segment)))
    return VarianceReport(baseline, current, bridge, contributions)
