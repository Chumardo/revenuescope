from datetime import date
import json

import duckdb
import pandas as pd
import pytest

from revenuescope.db import initialize_schema
from revenuescope.investigation import investigate_alerts, retrospective_segment_daily


def _score(actual=250_000, expected=150_000.5):
    return pd.DataFrame([
        {"date": "2024-05-01", "net_revenue_cents": 10, "is_anomaly": False, "is_complete": True},
        {"date": "2024-05-08", "net_revenue_cents": actual, "is_anomaly": True, "is_complete": True,
         "expected_revenue_cents": expected, "reference_dates": ("2024-05-01",),
         "method": "weekday_test", "direction": "up", "score": 7.5, "scale_cents": 10_000},
        {"date": "2024-05-15", "net_revenue_cents": 20, "is_anomaly": False, "is_complete": True},
    ])


def _breakdown(history, today):
    return pd.DataFrame(
        [(date(2024, 5, day), dimension, segment, value)
         for dimension in ("region", "product", "channel")
         for day, values in ((1, history), (8, today), (15, {"future": 20}))
         for segment, value in values.items()],
        columns=["date", "dimension", "segment", "net_revenue_cents"],
    )


def test_alert_uses_reconciled_past_mix_and_json_ready_diagnostics():
    [alert] = investigate_alerts(
        _score(), _breakdown({"a": 2, "b": 8}, {"a": 75_000, "c": 175_000}),
        metric="restated_order_date_revenue", top_n=1,
        data_quality_warnings=("Refunds are restated to the order date.",),
    )
    assert json.loads(json.dumps(alert)) == alert
    assert alert["date"] == "2024-05-08"
    assert alert["expected_revenue_cents"] == 150_000  # Ties to even.
    assert alert["absolute_deviation_cents"] == 100_000
    assert alert["percentage_deviation"] == pytest.approx(200 / 3)
    assert alert["severity"] == "high"
    assert alert["detection_method"] == "weekday_test"
    assert alert["diagnostics"]["score"] == 7.5
    assert alert["reference_dates"] == ["2024-05-01"]
    assert alert["data_quality_warnings"] == ["Refunds are restated to the order date."]
    for dimension in ("region", "product", "channel"):
        output = alert["contributions"][dimension]
        assert [(r["segment"], r["deviation_cents"]) for r in output["rows"]] == [
            ("c", 175_000), ("b", -120_000), ("a", 45_000)
        ]
        assert output["top_contributors"] == output["rows"][:1]
        assert sum(r["allocated_expected_cents"] for r in output["rows"]) == 150_000
        assert sum(r["actual_cents"] for r in output["rows"]) == 250_000


def test_signed_expectation_apportions_largest_remainder_deterministically():
    [alert] = investigate_alerts(_score(-4, -3), _breakdown({"z": 5, "a": 5}, {"z": -4}))
    assert alert["percentage_deviation"] == pytest.approx(-100 / 3)
    rows = alert["contributions"]["product"]["rows"]
    assert [(r["segment"], r["allocated_expected_cents"], r["deviation_cents"]) for r in rows] == [
        ("z", -1, -3), ("a", -2, 2)
    ]


def test_no_positive_history_keeps_unallocated_expectation_explicit():
    scored = _score(5, -3)
    scored.loc[0, "net_revenue_cents"] = -10
    [alert] = investigate_alerts(scored, _breakdown({"a": -10}, {"a": 5}))
    output = alert["contributions"]["region"]
    assert output["unallocated_expected_cents"] == -3
    assert output["unallocated_deviation_cents"] == 3
    assert output["rows"][0]["deviation_cents"] == 5
    assert alert["deviation_cents"] == 8
    assert any("could not be assigned" in warning for warning in alert["data_quality_warnings"])


def test_zero_expectation_has_no_percentage_and_no_alerts_returns_empty():
    [alert] = investigate_alerts(_score(25, 0), _breakdown({"a": 10}, {"a": 25}))
    assert alert["percentage_deviation"] is None
    assert alert["severity"] == "low"
    assert investigate_alerts(_score().assign(is_anomaly=False), pd.DataFrame()) == []


def test_rejects_wrong_scope_or_future_reference():
    segments = _breakdown({"a": 10}, {"a": 250_000})
    with pytest.raises(ArithmeticError, match="buckets do not match"):
        investigate_alerts(_score(), segments[segments["dimension"] != "channel"])
    bad = _score()
    bad.at[1, "reference_dates"] = ("2024-05-15",)
    with pytest.raises(ValueError, match="complete earlier days"):
        investigate_alerts(bad, segments)


def test_retrospective_partition_assigns_refunds_once_and_honors_scope():
    with duckdb.connect() as conn:
        initialize_schema(conn)
        conn.execute("""
            INSERT INTO dataset_meta VALUES ('2024-01-01', '2024-03-01', '2024-03-01', 1);
            INSERT INTO customers VALUES (1, '2023-12-01');
            INSERT INTO products VALUES (1, 'Widget', 'Home'), (2, 'Gizmo', 'Home');
            INSERT INTO orders VALUES
                (1, 1, '2024-01-31', 'North', 'Web', 'partially_refunded'),
                (2, 1, '2024-01-31', 'South', 'App', 'completed'),
                (3, 1, '2024-01-31', 'South', 'App', 'canceled');
            INSERT INTO order_lines VALUES
                (1, 1, 1, 2, 1000, 5, 400), (2, 1, 2, 1, 500, 0, 100),
                (3, 2, 2, 1, 700, 0, 100), (4, 3, 1, 1, 4000, 0, 400);
            INSERT INTO refunds VALUES
                (1, 1, '2024-02-02', 300, 0), (2, 1, '2024-02-03', 200, 0),
                (3, 1, '2024-03-01', 100, 0);
        """)
        result = retrospective_segment_daily(conn, "2024-01-01", "2024-03-01")
        assert set(result["date"].dt.date) == {date(2024, 1, 31)}
        assert result.groupby("dimension")["net_revenue_cents"].sum().to_dict() == {
            "region": 2695, "channel": 2695, "product": 2695,
        }
        scoped = retrospective_segment_daily(conn, "2024-01-01", "2024-03-01", dimension="region", segment="North")
        product = scoped[scoped["dimension"] == "product"]
        assert dict(zip(product["segment"], product["net_revenue_cents"], strict=True)) == {
            "Gizmo [#2]": 500, "Widget [#1]": 1495,
        }
