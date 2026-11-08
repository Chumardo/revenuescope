"""Small, interpretable alternatives to the unchanged Phase 1 screening policy."""

from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite
from statistics import median

import pandas as pd

from .anomaly import _NORMAL_MAD_SCALE, _booleans, _dates, _integers, _trend_baseline


@dataclass(frozen=True, slots=True)
class ScreeningPolicy:
    method: str = "median"
    lookback_weeks: int = 12
    min_history: int = 8
    min_active_share: float = 0.65
    min_median_orders: int = 8
    score_threshold: float = 4.0
    min_scale_cents: int = 10_000
    min_relative_scale: float = 0.05
    min_impact_cents: int = 0
    min_relative_impact: float = 0.0
    planned_promotion_policy: bool = False

    def __post_init__(self):
        if self.method not in {"median", "trend"} or not 2 <= self.min_history <= self.lookback_weeks:
            raise ValueError("Invalid baseline method or history settings.")
        for value in (self.lookback_weeks, self.min_history, self.min_median_orders,
                      self.min_impact_cents, self.min_scale_cents):
            if type(value) is not int or value < 0:
                raise ValueError("History counts and cent thresholds must be nonnegative integers.")
        values = (self.min_active_share, self.score_threshold, self.min_relative_scale, self.min_relative_impact)
        if any(not isfinite(value) or value < 0 for value in values) or self.min_active_share > 1:
            raise ValueError("Invalid share or threshold.")
        if self.score_threshold == 0 or self.min_scale_cents == 0:
            raise ValueError("The score threshold and scale floor must be positive.")


PORTFOLIO_POLICY_NAME = "weekday_trend_calendar_gates"
PORTFOLIO_POLICY = ScreeningPolicy(method="trend", planned_promotion_policy=True,
                                   min_impact_cents=125_000, min_relative_impact=0.25)


def synthetic_promotion_dates(start: date, end: date) -> frozenset[date]:
    """Known synthetic annual retail campaign (Fri–Mon after US Thanksgiving)."""
    dates = set()
    for year in range(start.year, end.year + 1):
        november = date(year, 11, 1)
        thanksgiving = november + timedelta(days=(3 - november.weekday()) % 7 + 21)
        dates.update(thanksgiving + timedelta(days=offset) for offset in range(1, 5))
    return frozenset(day for day in dates if start <= day < end)


def screen(daily: pd.DataFrame, policy: ScreeningPolicy, *, planned_dates=()) -> pd.DataFrame:
    """Score contiguous dates using only prior complete, eligible weekdays.

    A planned promotion can be scored diagnostically but is never paged by a
    promotion-aware policy. Planned dates remain in all evaluation denominators.
    """
    frame = daily.assign(date=_dates(daily["date"])).sort_values("date").reset_index(drop=True)
    if len(frame) > 1 and not frame.date.diff().iloc[1:].eq(pd.Timedelta(days=1)).all():
        raise ValueError("Provide a contiguous daily calendar including zero-activity days.")
    amounts = _integers(frame["net_revenue_cents"], "net_revenue_cents")
    orders = _integers(frame["orders"], "orders", nonnegative=True)
    complete = _booleans(frame["is_complete"], "is_complete")
    dates = frame.date.dt.date.tolist()
    planned_dates = frozenset(planned_dates)
    planned = [day in planned_dates for day in dates]
    output = []
    for i, day in enumerate(dates):
        history = [j for j in range(max(0, i // 7 - policy.lookback_weeks) * 7 + i % 7, i, 7)
                   if complete[j] and not (policy.planned_promotion_policy and planned[j])]
        share = sum(orders[j] != 0 for j in history) / len(history) if history else None
        median_orders = median(orders[j] for j in history) if history else None
        row = {
            "status": "scored", "history_days": len(history), "history_active_share": share,
            "history_median_orders": median_orders, "reference_dates": tuple(dates[j].isoformat() for j in history),
            "method": f"weekday_{policy.method}", "trend_cents_per_week": None,
            "planned_promotion": planned[i], "suppression_reason": None, "alert_threshold_cents": None,
            "expected_revenue_cents": None, "residual_cents": None, "score": None, "scale_cents": None,
            "direction": None, "is_anomaly": False,
        }
        if not complete[i]:
            row["status"] = "incomplete_day"
        elif len(history) < policy.min_history:
            row["status"] = "insufficient_history"
        elif share < policy.min_active_share or median_orders < policy.min_median_orders:
            row["status"] = "sparse_history"
        else:
            if policy.method == "trend":
                trend, center, dispersion, _ = _trend_baseline(history, amounts, i)
            else:
                trend = 0.0
                center = median(amounts[j] for j in history)
                dispersion = _NORMAL_MAD_SCALE * median(abs(amounts[j] - center) for j in history)
            expected = round(center)
            residual = amounts[i] - expected
            scale = max(dispersion, policy.min_scale_cents, abs(expected) * policy.min_relative_scale)
            threshold = max(scale * policy.score_threshold, policy.min_impact_cents,
                            abs(expected) * policy.min_relative_impact)
            exceeds = residual != 0 and abs(residual) >= threshold
            suppressed = exceeds and planned[i] and policy.planned_promotion_policy
            flag = exceeds and not suppressed
            row.update(trend_cents_per_week=trend, expected_revenue_cents=expected, residual_cents=residual,
                       scale_cents=scale, score=residual / scale, alert_threshold_cents=threshold,
                       suppression_reason="planned_promotion" if suppressed else None,
                       is_anomaly=flag, direction=("up" if residual > 0 else "down") if flag else None)
        output.append(row)
    diagnostics = pd.DataFrame(output, columns=list(row) if output else [
        "status", "history_days", "history_active_share", "history_median_orders", "reference_dates",
        "method", "trend_cents_per_week", "planned_promotion", "suppression_reason", "alert_threshold_cents",
        "expected_revenue_cents", "residual_cents", "score", "scale_cents", "direction", "is_anomaly",
    ])
    for column in ("expected_revenue_cents", "residual_cents"):
        diagnostics[column] = pd.array([row[column] for row in output], dtype="Int64")
    diagnostics = diagnostics.astype({
        "status": "string", "direction": "string", "method": "string", "suppression_reason": "string",
        "history_days": "int64", "history_active_share": "Float64", "history_median_orders": "Float64",
        "trend_cents_per_week": "Float64", "scale_cents": "Float64", "score": "Float64",
        "alert_threshold_cents": "Float64", "planned_promotion": bool, "is_anomaly": bool,
    })
    return frame.join(diagnostics)
