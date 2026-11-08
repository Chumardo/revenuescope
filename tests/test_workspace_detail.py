from datetime import date

import pandas as pd
import pytest

from revenuescope.workspace_detail import contribution_rows, history_window


def test_detail_history_ends_on_selected_date_and_preserves_missing_expectation():
    daily = pd.DataFrame({"date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
                          "expected_revenue_cents": pd.array([None, None, 120], dtype="Int64")})
    shown = history_window(daily, date(2024, 1, 2), prior_days=1)
    assert shown.date.dt.day.tolist() == [1, 2]
    assert shown.expected_revenue_cents.isna().all()


def test_detail_displays_unallocated_cents_and_missing_dimension_separately():
    alert = {"actual_revenue_cents": 1503, "expected_revenue_cents": 2001, "deviation_cents": -498,
             "contributions": {"product": {"rows": [
                 {"segment": "One", "actual_cents": 1503, "allocated_expected_cents": 0, "deviation_cents": 1503}
             ], "unallocated_expected_cents": 2001, "unallocated_deviation_cents": -2001}}}
    rows = contribution_rows(alert, "product")
    assert rows[-1]["segment"] == "Unallocated expectation"
    assert sum(row["deviation_cents"] for row in rows) == -498
    assert contribution_rows(alert, "region") is None
    alert["contributions"]["product"]["unallocated_deviation_cents"] += 1
    with pytest.raises(ValueError, match="do not reconcile"):
        contribution_rows(alert, "product")
