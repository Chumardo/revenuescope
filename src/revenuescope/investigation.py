"""Build reconciling, date-safe evidence for daily revenue alerts."""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN
from numbers import Integral

import duckdb
import pandas as pd

from .analytics import _bounds, _segment_expression, _sql
from .anomaly import _dates, _integers

_DIMENSIONS = ("region", "product", "channel")
_CONTRIBUTION_METHOD = "Allocate the whole-scope expectation by prior reference-day net revenue mix."


def retrospective_segment_daily(
    conn: duckdb.DuckDBPyConnection,
    start: date | str,
    end: date | str,
    *,
    dimension: str | None = None,
    segment: str | None = None,
) -> pd.DataFrame:
    """Partition restated line revenue across each investigation dimension."""
    if (dimension is None) != (segment is None):
        raise ValueError("Provide both dimension and segment, or neither")
    params = _bounds(conn, start, end)
    predicate = ""
    if dimension is not None:
        predicate = f"AND ({_segment_expression(dimension)}) = ?"
        params = [*params, segment]
    dimensions = ", ".join(f"('{d}', {_segment_expression(d)})" for d in _DIMENSIONS)
    query = f"""
        WITH {_sql('line_facts')}
        SELECT f.order_date AS date, s.dimension, s.segment,
               SUM(f.net_revenue_cents)::BIGINT AS net_revenue_cents
        FROM line_facts AS f
        CROSS JOIN LATERAL (VALUES {dimensions}) AS s(dimension, segment)
        WHERE f.order_date >= ?::DATE AND f.order_date < ?::DATE {predicate}
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
    """
    return conn.execute(query, params).fetchdf()


def _bucket_index(segment_daily: pd.DataFrame) -> dict[tuple[date, str], dict[str, int]]:
    raw_dates = segment_daily["date"].drop_duplicates()
    date_lookup = dict(zip(raw_dates, _dates(raw_dates).dt.date, strict=True))
    groups = defaultdict(dict)
    cols = ["date", "dimension", "segment", "net_revenue_cents"]
    for raw_date, dimension, segment, cents in segment_daily[cols].itertuples(index=False, name=None):
        if dimension not in _DIMENSIONS:
            raise ValueError(f"Unknown contribution dimension {dimension!r}")
        if not isinstance(segment, str) or not segment.strip():
            raise ValueError("Contribution segment names must be nonempty strings")
        if not isinstance(cents, Integral) or isinstance(cents, bool):
            raise ValueError("Contribution amounts must be integer cents")
        group = groups[date_lookup[raw_date], dimension]
        if segment in group:
            raise ValueError(f"Duplicate contribution bucket for {raw_date}, {dimension}, {segment}")
        group[segment] = int(cents)
    return groups


def _allocate_cents(total: int, weights: Mapping[str, int]) -> dict[str, int]:
    """Apportion signed whole cents using positive weights and largest remainders."""
    denominator = sum(weights.values())
    portions = {segment: divmod(abs(total) * weight, denominator) for segment, weight in weights.items()}
    rounded = {segment: quotient for segment, (quotient, _) in portions.items()}
    cents_left = abs(total) - sum(rounded.values())
    by_remainder = sorted(portions, key=lambda segment: (-portions[segment][1], segment))
    for segment in by_remainder[:cents_left]:
        rounded[segment] += 1
    return {segment: -cents if total < 0 else cents for segment, cents in rounded.items()}


def _segment_breakdown(
    actual: Mapping[str, int],
    historical: Mapping[str, int],
    expected: int,
    actual_total: int,
    top_n: int,
) -> dict:
    weights = {segment: cents for segment, cents in historical.items() if cents > 0}
    allocated = _allocate_cents(expected, weights) if weights else {}
    unallocated = 0 if weights else expected
    rows = [
        {
            "segment": segment,
            "actual_cents": actual.get(segment, 0),
            "allocated_expected_cents": allocated.get(segment, 0),
            "deviation_cents": actual.get(segment, 0) - allocated.get(segment, 0),
        }
        for segment in actual.keys() | historical.keys()
    ]
    rows.sort(key=lambda row: (-abs(row["deviation_cents"]), row["segment"]))
    if (sum(row["actual_cents"] for row in rows) != actual_total
        or sum(row["allocated_expected_cents"] for row in rows) + unallocated != expected
        or sum(row["deviation_cents"] for row in rows) - unallocated != actual_total - expected):
        raise ArithmeticError("Contribution arithmetic did not reconcile")
    return {
        "rows": rows,
        "top_contributors": [row for row in rows if row["deviation_cents"]][:top_n],
        "unallocated_expected_cents": unallocated,
        "unallocated_deviation_cents": -unallocated,
        "weight_method": "positive aggregate reference-day net cents" if weights else "no positive historical mix",
        "negative_reference_buckets": sum(cents < 0 for cents in historical.values()),
    }


def _severity(delta: int, expected: int) -> str:
    """Rank business magnitude with absolute and relative gates, independent of score."""
    for level, cents, percent in (("high", 100_000, 25), ("medium", 50_000, 15)):
        if abs(delta) >= cents and abs(delta) * 100 >= abs(expected) * percent:
            return level
    return "low"


def _scalar(value):
    if value is None or pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def investigate_alerts(
    scored: pd.DataFrame,
    segment_daily: pd.DataFrame,
    *,
    metric: str = "net_revenue",
    scope: str = "overall",
    top_n: int = 3,
    data_quality_warnings: Iterable[str] = (),
) -> list[dict]:
    """Return JSON-ready evidence for each alert in a single consistent reporting scope.

    `reference_dates` must contain the complete earlier dates used by the detector.
    Missing segment activity is zero. Each partition must reconcile with the scored
    series for the alert and every reference date. The expectation is rounded to
    the nearest cent (ties to even) before allocation. The allocation explains
    contributions under a historical mix assumption, without assigning causes.
    """
    if isinstance(top_n, bool) or not isinstance(top_n, Integral) or top_n < 1:
        raise ValueError("top_n must be a positive integer")
    flagged = scored.loc[scored["is_anomaly"]]
    if flagged.empty:
        return []
    dates = _dates(scored["date"])
    totals = dict(zip(dates.dt.date, _integers(scored["net_revenue_cents"], "net_revenue_cents"), strict=True))
    complete = set(dates[scored["is_complete"]].dt.date) if "is_complete" in scored else set(totals)
    buckets = _bucket_index(segment_daily)
    base_warnings = tuple(data_quality_warnings)
    result = []
    for row in flagged.sort_values("date").to_dict(orient="records"):
        day = pd.Timestamp(row["date"]).date()
        raw_refs = row["reference_dates"]
        if not isinstance(raw_refs, Sequence) or isinstance(raw_refs, str):
            raise ValueError("Each alert must include its reference dates as a sequence")
        references = _dates(pd.Series(raw_refs, dtype="object")).dt.date.tolist()
        if day not in complete or any(ref >= day or ref not in complete for ref in references):
            raise ValueError("Alert reference dates must be complete earlier days in the scored series")
        model_expected = Decimal(str(row["expected_revenue_cents"]))
        if not model_expected.is_finite():
            raise ValueError("An alert must have a finite model expectation")
        expected = int(model_expected.to_integral_value(rounding=ROUND_HALF_EVEN))
        actual = totals[day]
        delta = actual - expected
        warnings = list(base_warnings)
        if expected == 0:
            warnings.append("Percentage deviation is unavailable because expected revenue rounds to zero.")
        contributions = {}
        for dimension in _DIMENSIONS:
            historical = Counter()
            observed = buckets.get((day, dimension), {})
            for evidence_day in [*references, day]:
                partition = buckets.get((evidence_day, dimension), {})
                if sum(partition.values()) != totals[evidence_day]:
                    raise ArithmeticError(f"{dimension} buckets do not match the scored total for {evidence_day}")
                if evidence_day != day:
                    historical.update(partition)
            breakdown = _segment_breakdown(observed, historical, expected, actual, int(top_n))
            contributions[dimension] = breakdown
            if breakdown["weight_method"] == "no positive historical mix":
                warnings.append(f"{dimension}: expected revenue could not be assigned to individual segments.")
            if breakdown["negative_reference_buckets"]:
                warnings.append(f"{dimension}: negative historical net amounts did not receive allocation weight.")
        result.append({
            "date": day.isoformat(),
            "metric": metric,
            "scope": scope,
            "actual_revenue_cents": actual,
            "expected_revenue_cents": expected,
            "model_expected_revenue_cents": str(model_expected),
            "deviation_cents": delta,
            "absolute_deviation_cents": abs(delta),
            "percentage_deviation": delta * 100 / abs(expected) if expected else None,
            "direction": row["direction"],
            "severity": _severity(delta, expected),
            "detection_method": row["method"],
            "diagnostics": {key: _scalar(row.get(key)) for key in (
                "score", "scale_cents", "alert_threshold_cents", "history_days", "history_active_share", "history_median_orders"
            )},
            "reference_dates": [ref.isoformat() for ref in references],
            "contribution_method": _CONTRIBUTION_METHOD,
            "contributions": contributions,
            "data_quality_warnings": list(dict.fromkeys(warnings)),
        })
    return result
