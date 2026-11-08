from datetime import date

import pandas as pd
import pytest

from revenuescope.screening import ScreeningPolicy, screen, synthetic_promotion_dates


def daily(amounts, orders=None, complete=None):
    return pd.DataFrame({"date": pd.date_range("2024-01-01", periods=len(amounts)), "net_revenue_cents": amounts,
                         "orders": orders if orders is not None else [10] * len(amounts),
                         "is_complete": complete if complete is not None else [True] * len(amounts)})


def test_median_reference_and_threshold_boundary():
    frame = daily([100_000] * 56 + [150_000, 149_999])
    score = screen(frame, ScreeningPolicy(min_impact_cents=50_000, min_relative_impact=.25))
    assert score.iloc[55].status == "insufficient_history"
    assert score.iloc[56].expected_revenue_cents == 100_000
    assert score.iloc[56].score == 5 and score.iloc[56].is_anomaly
    assert not score.iloc[57].is_anomaly
    assert score.iloc[56].reference_dates == tuple(frame.date.iloc[:56:7].dt.strftime("%Y-%m-%d"))


def test_trend_adjusts_for_growth_and_never_uses_the_future():
    observed = [100_000 + (i // 7) * 20_000 for i in range(70)]
    frame = daily(observed)
    policy = ScreeningPolicy(method="trend")
    base = screen(frame, policy)
    assert base.iloc[56].expected_revenue_cents == 260_000
    assert base.iloc[56].residual_cents == 0
    frame.loc[57:, "net_revenue_cents"] = 900_000
    pd.testing.assert_frame_equal(base.iloc[:57], screen(frame, policy).iloc[:57])


def test_planned_days_still_scored_and_not_used_in_ordinary_baseline():
    frame = daily([100_000] * 56 + [300_000] + [100_000] * 7)
    planned = frame.iloc[56].date.date()
    scored = screen(frame, ScreeningPolicy(planned_promotion_policy=True), planned_dates=[planned])
    assert scored.iloc[56].status == "scored" and scored.iloc[56].suppression_reason == "planned_promotion"
    assert not scored.iloc[56].is_anomaly
    assert planned.isoformat() not in scored.iloc[63].reference_dates
    assert synthetic_promotion_dates(date(2024, 11, 28), date(2024, 12, 3)) == {
        date(2024, 11, 29), date(2024, 11, 30), date(2024, 12, 1), date(2024, 12, 2)}


def test_sparse_and_incomplete_are_not_scored():
    frame = daily([50_000] * 58, orders=[1] * 58, complete=[True] * 57 + [False])
    scored = screen(frame, ScreeningPolicy())
    assert scored.iloc[56].status == "sparse_history"
    assert scored.iloc[57].status == "incomplete_day"
    assert not scored.is_anomaly.any()
    with pytest.raises(ValueError, match="contiguous"):
        screen(frame.drop(1), ScreeningPolicy())
