"""Explainable, past-only daily revenue screening and manifest evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import combinations
from math import isfinite
from numbers import Integral, Real
from statistics import median

import pandas as pd
from pandas.api.types import is_bool_dtype

_NORMAL_MAD_SCALE = 1.482602218505602
_SCORE_STATUSES = {"scored", "incomplete_day", "insufficient_history", "sparse_history"}
_DIRECTIONS = {
    "up": "up", "positive": "up", "increase": "up", "spike": "up",
    "down": "down", "negative": "down", "decrease": "down", "dip": "down",
}


@dataclass(frozen=True, slots=True)
class AnomalyConfig:
    """Rules for weekday history, support, and practical alert thresholds."""

    lookback_weeks: int = 12
    min_history: int = 8
    min_active_share: float = 0.65
    min_median_orders: int = 8
    score_threshold: float = 4.0
    min_scale_cents: int = 10_000
    min_relative_scale: float = 0.05

    def __post_init__(self) -> None:
        for name in ("lookback_weeks", "min_history", "min_median_orders", "min_scale_cents"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Integral):
                raise ValueError(f"{name} must be an integer")
        if not 2 <= self.min_history <= self.lookback_weeks:
            raise ValueError("Require 2 <= min_history <= lookback_weeks")
        if self.min_median_orders < 0 or self.min_scale_cents <= 0:
            raise ValueError("Require nonnegative min_median_orders and positive min_scale_cents")
        for name in ("min_active_share", "min_relative_scale", "score_threshold"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value):
                raise ValueError(f"{name} must be finite and numeric")
        if not 0 <= self.min_active_share <= 1 or self.min_relative_scale < 0 or self.score_threshold <= 0:
            raise ValueError("Invalid activity share, relative scale, or score threshold")


def _dates(values: pd.Series) -> pd.Series:
    if any(isinstance(value, Real) for value in values):
        raise ValueError("Dates must be calendar dates, not numeric timestamps")
    try:
        parsed = pd.to_datetime(values, errors="raise")
        if parsed.isna().any() or parsed.dt.tz is not None or not parsed.eq(parsed.dt.normalize()).all():
            raise ValueError("Expected non-null UTC calendar dates at midnight without a timezone offset")
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Expected non-null UTC calendar dates at midnight without a timezone offset") from exc
    if parsed.duplicated().any():
        raise ValueError("Expected exactly one row per date")
    return parsed


def _integers(values: pd.Series, name: str, *, nonnegative: bool = False) -> list[int]:
    result = []
    for value in values.tolist():
        if isinstance(value, bool) or not isinstance(value, Integral) or (nonnegative and value < 0):
            raise ValueError(f"{name} must contain {'nonnegative ' if nonnegative else ''}integers")
        result.append(int(value))
    return result


def _booleans(values: pd.Series, name: str) -> list[bool]:
    if len(values) and (not is_bool_dtype(values) or values.isna().any()):
        raise ValueError(f"{name} must contain non-null booleans")
    return [bool(value) for value in values]


def _trend_baseline(history: list[int], revenue: list[int], index: int) -> tuple[float, float, float, float]:
    """Return trend, expectation, normal-scaled historical MAD, and residual."""
    trend = median(
        (revenue[later] - revenue[earlier]) / ((later - earlier) // 7)
        for earlier, later in combinations(history, 2)
    )
    # Center before computing residuals so a large common level does not erase small changes.
    anchor = revenue[history[-1]]
    projections = [revenue[i] - anchor + trend * ((index - i) // 7) for i in history]
    center = median(projections)
    mad = median(abs(value - center) for value in projections)
    residual = (revenue[index] - anchor) - center
    return trend, anchor + center, _NORMAL_MAD_SCALE * mad, residual


def detect_anomalies(
    daily: pd.DataFrame,
    *,
    date_column: str = "date",
    revenue_column: str = "net_revenue_cents",
    order_column: str | None = "orders",
    config: AnomalyConfig | None = None,
) -> pd.DataFrame:
    """Score a contiguous UTC day series against prior complete, matching weekdays.

    Input may be unsorted. Include zero-activity days explicitly, and set the optional
    `is_complete` flag to False for partial days. If order counts are absent, activity
    support uses days with nonzero net revenue. Model outputs use floating-point cents
    for statistical diagnostics while observed revenue remains integer cents.
    """
    rules = config or AnomalyConfig()
    frame = daily.assign(_date=_dates(daily[date_column])).sort_values("_date").reset_index(drop=True)
    if len(frame) > 1 and not frame["_date"].diff().iloc[1:].eq(pd.Timedelta(days=1)).all():
        raise ValueError("Daily series must include all calendar days, including days with zero activity")
    revenue = _integers(frame[revenue_column], revenue_column)
    orders = (
        _integers(frame[order_column], order_column, nonnegative=True)
        if order_column is not None and order_column in frame
        else None
    )
    complete = _booleans(frame["is_complete"], "is_complete") if "is_complete" in frame else [True] * len(frame)
    activity = orders if orders is not None else revenue
    output = []

    for index in range(len(frame)):
        history = [
            i for i in range(index - 7 * min(rules.lookback_weeks, index // 7), index, 7)
            if complete[i]
        ]
        active_share = sum(activity[i] != 0 for i in history) / len(history) if history else None
        median_orders = median(orders[i] for i in history) if history and orders is not None else None
        row = {
            "status": "scored",
            "history_days": len(history),
            "history_active_share": active_share,
            "history_median_orders": median_orders,
            "trend_cents_per_week": None,
            "expected_revenue_cents": None,
            "residual_cents": None,
            "scale_cents": None,
            "score": None,
            "direction": None,
            "is_anomaly": False,
        }
        if not complete[index]:
            row["status"] = "incomplete_day"
        elif len(history) < rules.min_history:
            row["status"] = "insufficient_history"
        elif active_share < rules.min_active_share or (
            median_orders is not None and median_orders < rules.min_median_orders
        ):
            row["status"] = "sparse_history"
        else:
            trend, expected, mad_scale, residual = _trend_baseline(history, revenue, index)
            scale = max(mad_scale, rules.min_scale_cents, abs(expected) * rules.min_relative_scale)
            score = residual / scale
            flagged = abs(score) >= rules.score_threshold
            row.update(
                trend_cents_per_week=trend, expected_revenue_cents=expected, residual_cents=residual,
                scale_cents=scale, score=score, is_anomaly=flagged,
                direction=("up" if score > 0 else "down") if flagged else None,
            )
        output.append(row)

    base = pd.DataFrame(
        {"date": frame["_date"], "net_revenue_cents": pd.Series(revenue, dtype="int64"),
         "is_complete": pd.Series(complete, dtype="bool")}
    )
    if orders is not None:
        base["orders"] = pd.Series(orders, dtype="int64")
    diagnostics = {
        "status": "string", "history_days": "int64", "history_active_share": "Float64",
        "history_median_orders": "Float64", "trend_cents_per_week": "Float64",
        "expected_revenue_cents": "Float64", "residual_cents": "Float64", "scale_cents": "Float64",
        "score": "Float64", "direction": "string", "is_anomaly": "bool",
    }
    return base.join(pd.DataFrame(output, columns=list(diagnostics)).astype(diagnostics))


@dataclass(frozen=True, slots=True)
class _Event:
    id: str
    start: date
    end: date
    direction: str


def _events(manifest: Sequence[Mapping] | Mapping, scope: str) -> list[_Event]:
    records = manifest["anomalies"] if isinstance(manifest, Mapping) else manifest
    events = []
    identifiers = set()
    for item in records:
        if item.get("scope", "overall") != scope:
            continue
        key = item["id"]
        start, end = date.fromisoformat(item["start"]), date.fromisoformat(item["end"])
        direction = _DIRECTIONS.get(item["direction"])
        if not isinstance(key, str) or not key or key in identifiers:
            raise ValueError("Manifest event IDs must be nonempty and unique within a scope")
        if start >= end or direction is None:
            raise ValueError(f"Invalid dates or direction for manifest event {key!r}")
        events.append(_Event(key, start, end, direction))
        identifiers.add(key)
    return sorted(events, key=lambda event: (event.end, event.start, event.id))


def evaluate_anomalies(
    scored: pd.DataFrame,
    manifest: Sequence[Mapping] | Mapping,
    *,
    scope: str = "overall",
) -> dict:
    """Compare screening alerts with a held-out injection manifest for one scope.

    A flagged day is a true positive only if its direction matches an event covering
    the day. Unscored dates are reported separately from scored misses. Event matches
    are one-to-one, using earliest-ending intervals first and the earliest available
    alert of the same direction. End dates are exclusive.
    """
    frame = scored.assign(date=_dates(scored["date"]))
    flags = _booleans(frame["is_anomaly"], "is_anomaly")
    observed: set[date] = set()
    scored_days: set[date] = set()
    alerts: dict[date, str] = {}
    for day, status, direction, flagged in zip(
        frame["date"].dt.date, frame["status"], frame["direction"], flags, strict=True
    ):
        if status not in _SCORE_STATUSES:
            raise ValueError(f"Unknown scoring status {status!r}")
        if flagged and (status != "scored" or direction not in {"up", "down"}):
            raise ValueError("Alerts require scored days and valid directions")
        observed.add(day)
        if status == "scored":
            scored_days.add(day)
        if flagged:
            alerts[day] = direction

    events = _events(manifest, scope)
    covered = {event.id: {day for day in observed if event.start <= day < event.end} for event in events}
    truth_days = set().union(*covered.values())
    truth_scored = truth_days & scored_days
    hits = {
        day for event in events for day in covered[event.id]
        if alerts.get(day) == event.direction
    }
    unmatched_alerts = set(hits)
    event_results = []
    for event in events:
        in_window = covered[event.id]
        candidates = sorted(day for day in in_window if alerts.get(day) == event.direction)
        eligible = in_window & scored_days
        match = next((day for day in candidates if day in unmatched_alerts), None)
        if match is not None:
            outcome = "detected"
            unmatched_alerts.remove(match)
        elif not in_window:
            outcome = "outside_coverage"
        elif not eligible:
            outcome = "unscored"
        else:
            outcome = "missed"
        event_results.append({
            "id": event.id, "start": event.start.isoformat(), "end": event.end.isoformat(),
            "direction": event.direction, "outcome": outcome,
            "window_days": (event.end - event.start).days,
            "observed_days": len(in_window), "scored_days": len(eligible),
            "matched_alert_date": match.isoformat() if match else None,
            "same_direction_alert_dates": [day.isoformat() for day in candidates],
            "opposite_direction_alert_dates": sorted(
                day.isoformat() for day in in_window if day in alerts and alerts[day] != event.direction
            ),
        })

    outcomes = [event["outcome"] for event in event_results]
    detected, missed = outcomes.count("detected"), outcomes.count("missed")
    false_positives = set(alerts) - hits
    misses = truth_scored - hits
    unscored = truth_days - scored_days
    return {
        "scope": scope,
        "observed_days": len(observed), "scored_days": len(scored_days), "alert_days": len(alerts),
        "injected_days_in_coverage": len(truth_days), "scored_injected_days": len(truth_scored),
        "true_positive_days": len(hits), "false_positive_days": len(false_positives),
        "false_negative_days": len(misses), "unscored_injected_days": len(unscored),
        "wrong_direction_alert_days": len(false_positives & truth_scored),
        "precision": len(hits) / len(alerts) if alerts else None,
        "recall": len(hits) / len(truth_scored) if truth_scored else None,
        "detected_events": detected, "missed_events": missed,
        "unscored_events": outcomes.count("unscored"), "outside_coverage_events": outcomes.count("outside_coverage"),
        "event_recall": detected / (detected + missed) if detected + missed else None,
        "false_positive_dates": sorted(day.isoformat() for day in false_positives),
        "missed_dates": sorted(day.isoformat() for day in misses),
        "unscored_injected_dates": sorted(day.isoformat() for day in unscored),
        "events": event_results,
    }
