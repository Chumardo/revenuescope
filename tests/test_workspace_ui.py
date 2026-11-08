import pandas as pd

from revenuescope.workspace_ui import _money, _queue_order, _revenue_chart


def test_chart_uses_exact_tooltip_values_and_gaps_for_warmup_and_incomplete():
    frame = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=3),
        "net_revenue_cents": pd.Series([12_999, 15_001, 999_999], dtype="Int64"),
        "expected_revenue_cents": pd.Series([None, 10_000, None], dtype="Int64"),
        "residual_cents": pd.Series([None, 5_001, None], dtype="Int64"),
        "percentage_deviation": pd.Series([None, 50.01, None], dtype="Float64"),
        "is_complete": [True, True, False],
        "is_anomaly": [False, True, False],
    })
    figure = _revenue_chart(frame)
    actual, expected, flagged = figure.data
    assert list(actual.y[:2]) == [129.99, 150.01]
    assert pd.isna(actual.y[2]) and pd.isna(expected.y[0])
    assert list(flagged.x) == [pd.Timestamp("2024-01-02")]
    assert list(flagged.customdata[0]) == ["$150.01", "$100.00", "+$50.01", "$50.01", "+50.01%"]
    assert _money(-1201, signed=True) == "−$12.01"


def test_queue_impact_sort_uses_integer_magnitude_and_newest_tie_break():
    queue = pd.DataFrame({"date": pd.to_datetime(["2024-01-03", "2024-01-01", "2024-01-02"]),
                          "absolute_deviation_cents": [5, 10, 10]})
    assert _queue_order(queue, "Largest absolute deviation").index.tolist() == [2, 1, 0]
    assert _queue_order(queue, "Oldest first").index.tolist() == [1, 2, 0]
