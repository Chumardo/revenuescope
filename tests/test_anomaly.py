"""Independent calendar, alert, and benchmark expectations for daily screening."""

from dataclasses import replace
import json

import pandas as pd
import pytest

from revenuescope.anomaly import AnomalyConfig, detect_anomalies, evaluate_anomalies


def daily_fixture(size=100):
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=size),
        "net_revenue_cents": [100_000 + (i % 7) * 12_500 + (i // 7) * 700 for i in range(size)],
        "orders": 60,
        "is_complete": True,
    })


def test_same_weekday_trend_distinguishes_spikes_and_dips_from_weekly_patterns():
    daily = daily_fixture()
    daily.loc[84, "net_revenue_cents"] += 80_000
    daily.loc[86, "net_revenue_cents"] -= 60_000
    scored = detect_anomalies(daily)

    assert (scored.loc[:55, "status"] == "insufficient_history").all()
    assert scored.loc[56, "status"] == "scored"
    assert scored.loc[56, "expected_revenue_cents"] == 105_600
    assert scored.loc[84, "expected_revenue_cents"] == 108_400
    assert scored.loc[84, "trend_cents_per_week"] == 700
    assert scored.loc[84, "residual_cents"] == 80_000
    assert scored.loc[84, "score"] == 8
    assert scored.loc[86, "expected_revenue_cents"] == 133_400
    assert scored.loc[86, "residual_cents"] == -60_000
    assert scored.loc[86, "score"] == -6
    assert scored.loc[scored["is_anomaly"], "date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2024-03-25", "2024-03-27",
    ]
    assert scored.loc[scored["is_anomaly"], "direction"].tolist() == ["up", "down"]


def test_a_later_value_never_changes_earlier_scores_and_input_order_is_irrelevant():
    daily = daily_fixture()
    original = detect_anomalies(daily)
    daily.loc[99, "net_revenue_cents"] = 200_000_000
    changed = detect_anomalies(daily.iloc[::-1])
    pd.testing.assert_frame_equal(changed.iloc[:-1], original.iloc[:-1])
    assert changed.loc[99, "is_anomaly"]


def test_incomplete_day_is_neither_scored_nor_used_in_following_baseline():
    daily = daily_fixture(29)
    daily.loc[7, ["net_revenue_cents", "orders"]] = [20_000_000, 0]
    daily.loc[7, "is_complete"] = False
    rules = AnomalyConfig(lookback_weeks=4, min_history=3)
    scored = detect_anomalies(daily, config=rules)

    assert scored.loc[7, "status"] == "incomplete_day"
    assert pd.isna(scored.loc[7, "score"])
    assert scored.loc[21, "status"] == "insufficient_history"
    assert scored.loc[21, "history_days"] == 2
    assert scored.loc[28, "status"] == "scored"
    assert scored.loc[28, "history_days"] == 3
    assert scored.loc[28, "expected_revenue_cents"] == 102_800
    assert scored.loc[28, "score"] == 0


def test_sparse_support_is_based_on_history_so_zero_order_outages_can_still_alert():
    daily = daily_fixture(57)
    daily.loc[56, ["net_revenue_cents", "orders"]] = [0, 0]
    outage = detect_anomalies(daily)
    assert outage.loc[56, "status"] == "scored"
    assert outage.loc[56, "direction"] == "down"

    daily["orders"] = 2
    sparse = detect_anomalies(daily)
    assert sparse.loc[56, "status"] == "sparse_history"
    assert sparse.loc[56, "history_median_orders"] == 2
    assert not sparse.loc[56, "is_anomaly"]

    daily["net_revenue_cents"] = 0
    sparse_without_orders = detect_anomalies(daily.drop(columns="orders"))
    assert sparse_without_orders.loc[56, "status"] == "sparse_history"
    assert sparse_without_orders.loc[56, "history_active_share"] == 0


def test_relative_and_absolute_noise_floors_and_inclusive_threshold():
    daily = daily_fixture(59)
    daily["net_revenue_cents"] = 100_000
    daily.loc[56, "net_revenue_cents"] += 20_000
    daily.loc[57, "net_revenue_cents"] += 19_999
    rules = AnomalyConfig(min_scale_cents=1_000)
    scored = detect_anomalies(daily, config=rules)
    assert scored.loc[56, "scale_cents"] == 5_000
    assert scored.loc[56, "score"] == 4
    assert scored.loc[56, "is_anomaly"]
    assert not scored.loc[57, "is_anomaly"]

    conservative = detect_anomalies(daily, config=replace(rules, min_scale_cents=10_000))
    assert conservative.loc[56, "scale_cents"] == 10_000
    assert not conservative.loc[56, "is_anomaly"]


def test_scaled_mad_handles_nonzero_noise_and_ignores_a_prior_gross_outlier():
    daily = daily_fixture(57)
    daily["net_revenue_cents"] = 100_000
    daily.loc[[0, 7, 14, 21, 28, 35, 42, 49], "net_revenue_cents"] = [
        99_000, 101_000, 99_000, 101_000, 101_000, 99_000, 101_000, 99_000,
    ]
    daily.loc[56, "net_revenue_cents"] = 110_000
    rules = AnomalyConfig(min_scale_cents=1, min_relative_scale=0)
    scored = detect_anomalies(daily, config=rules)
    # The symmetric history gives a zero median slope and a $10 residual MAD.
    assert scored.loc[56, "trend_cents_per_week"] == 0
    assert scored.loc[56, "expected_revenue_cents"] == 100_000
    assert scored.loc[56, "scale_cents"] == pytest.approx(1_482.60, abs=0.01)
    assert scored.loc[56, "is_anomaly"]
    daily.loc[0, "net_revenue_cents"] = 10_000_000
    assert detect_anomalies(daily, config=rules).loc[56, "expected_revenue_cents"] <= 101_000


@pytest.mark.parametrize("problem", ["gap", "duplicate", "float_money", "null_complete", "date_timestamp"])
def test_invalid_daily_input_fails_before_scoring(problem):
    daily = daily_fixture(12)
    if problem == "gap":
        daily = daily.drop(index=5)
    elif problem == "duplicate":
        daily.loc[5, "date"] = daily.loc[4, "date"]
    elif problem == "float_money":
        daily["net_revenue_cents"] = daily["net_revenue_cents"].astype(float)
    elif problem == "null_complete":
        daily["is_complete"] = daily["is_complete"].astype("boolean")
        daily.loc[5, "is_complete"] = pd.NA
    else:
        daily.loc[5, "date"] += pd.Timedelta(hours=1)
    with pytest.raises(ValueError):
        detect_anomalies(daily)


def event(key, start, end, direction, scope="overall"):
    return {"id": key, "start": f"2024-01-{start:02}", "end": f"2024-01-{end:02}",
            "direction": direction, "mechanism": "test", "scope": scope}


def test_evaluation_reports_directional_false_alerts_misses_and_coverage_separately():
    scored = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=8),
        "status": ["scored"] * 5 + ["insufficient_history"] + ["scored"] * 2,
        "direction": [None, "up", "up", "down", "up", None, None, None],
        "is_anomaly": [False, True, True, True, True, False, False, False],
    })
    manifest = {"anomalies": [
        event("wrong-direction", 2, 3, "down"),
        event("spike", 3, 4, "up"),
        event("dip", 4, 5, "down"),
        event("unscored", 6, 7, "up"),
        event("missed", 7, 8, "down"),
        event("outside", 11, 12, "up"),
        event("other-scope", 5, 6, "up", "channel:web"),
    ]}
    report = evaluate_anomalies(scored, manifest)
    assert json.loads(json.dumps(report)) == report
    assert report["observed_days"] == 8
    assert report["scored_days"] == 7
    assert report["true_positive_days"] == 2
    assert report["false_positive_dates"] == ["2024-01-02", "2024-01-05"]
    assert report["missed_dates"] == ["2024-01-02", "2024-01-07"]
    assert report["unscored_injected_dates"] == ["2024-01-06"]
    assert report["wrong_direction_alert_days"] == 1
    assert report["precision"] == report["recall"] == report["event_recall"] == 0.5
    assert {item["id"]: item["outcome"] for item in report["events"]} == {
        "wrong-direction": "missed", "spike": "detected", "dip": "detected",
        "unscored": "unscored", "missed": "missed", "outside": "outside_coverage",
    }


def test_one_alert_day_cannot_claim_two_overlapping_events():
    scored = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=4),
        "status": "scored",
        "direction": [None, "up", None, None],
        "is_anomaly": [False, True, False, False],
    })
    report = evaluate_anomalies(scored, [
        event("longer", 1, 4, "up"), event("earlier-ending", 2, 3, "up"),
    ])
    assert report["detected_events"] == report["missed_events"] == 1
    assert [item["matched_alert_date"] for item in report["events"]] == ["2024-01-02", None]
    assert report["true_positive_days"] == 1
    assert report["false_negative_days"] == 2


def test_empty_series_and_manifest_return_no_alerts_and_undefined_rates():
    scored = detect_anomalies(daily_fixture(0))
    report = evaluate_anomalies(scored, [])
    assert scored.empty and report["events"] == []
    assert report["alert_days"] == report["false_positive_days"] == 0
    assert report["precision"] is report["recall"] is report["event_recall"] is None
