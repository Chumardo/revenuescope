from dataclasses import asdict
import json

import pandas as pd
import pytest

from revenuescope.benchmark import CANDIDATES, DEV_SEEDS, HOLDOUT_SEEDS, PROTOCOL, alert_metrics, load_lock


def test_event_and_date_metrics_independent_arithmetic():
    frame = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=9),
                          "status": ["insufficient_history"] + ["scored"] * 8,
                          "direction": [None, "up", "up", "down", None, "up", None, None, "down"]})
    frame["is_anomaly"] = frame.direction.notna()
    manifest = {"anomalies": [
        {"id": "one", "start": "2024-01-02", "end": "2024-01-04", "direction": "up", "net_revenue_delta_cents": 900},
        {"id": "two", "start": "2024-01-05", "end": "2024-01-07", "direction": "up", "net_revenue_delta_cents": 50},
        {"id": "three", "start": "2024-01-08", "end": "2024-01-09", "direction": "down", "net_revenue_delta_cents": -50},
        {"id": "warmup", "start": "2024-01-01", "end": "2024-01-02", "direction": "up", "net_revenue_delta_cents": 1000},
    ]}
    result = alert_metrics(frame, manifest)
    assert result["date_precision"] == 3 / 5
    assert result["date_recall"] == 3 / 5
    assert result["event_precision"] == 2 / 4
    assert result["event_recall"] == 2 / 3
    assert result["severity_weighted_recall"] == .95
    assert result["coverage"] == 8 / 9
    assert result["unmatched_per_calendar_month"] == 2
    assert result["mean_delay_days"] == .5
    assert result["unmatched_dates"] == ["2024-01-04", "2024-01-09"]


def test_consecutive_same_direction_episode_cannot_credit_two_events():
    frame = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=2), "status": ["scored"] * 2,
                          "direction": ["up"] * 2, "is_anomaly": [True] * 2})
    manifest = {"anomalies": [
        {"id": str(day), "start": f"2024-01-0{day}", "end": f"2024-01-0{day + 1}",
         "direction": "up", "net_revenue_delta_cents": 1} for day in (1, 2)]}
    result = alert_metrics(frame, manifest)
    assert result["date_recall"] == 1
    assert result["event_recall"] == .5
    assert result["event_precision"] == 1


def test_locked_protocol_refuses_seed_or_policy_changes(tmp_path):
    assert not set(DEV_SEEDS) & set(HOLDOUT_SEEDS)
    path = tmp_path / "dev.json"
    selected = next(iter(CANDIDATES))
    record = {"protocol": PROTOCOL, "seeds": DEV_SEEDS, "selected": selected,
              "locked_policy": asdict(CANDIDATES[selected])}
    path.write_text(json.dumps(record))
    assert load_lock(path)[2] == CANDIDATES[selected]
    record["locked_policy"]["score_threshold"] += 1
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="registered protocol"):
        load_lock(path)
