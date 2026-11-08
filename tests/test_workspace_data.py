from datetime import date
import json

import duckdb
import pandas as pd
import pytest

from revenuescope.anomaly_audit import export_investigation
from revenuescope.db import initialize_schema
from revenuescope.pit import POSTED_METRIC, RETROSPECTIVE_METRIC
from revenuescope.workspace_data import (
    INVESTIGATION_FILES,
    WorkspaceDataError,
    alert_detail,
    filter_dates,
    load_workspace,
    overview_kpis,
    review_queue,
    scope_daily,
)


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory):
    root = tmp_path_factory.mktemp("workspace")
    database = root / "demo.duckdb"
    with duckdb.connect(str(database)) as conn:
        initialize_schema(conn)
        conn.execute("""
            INSERT INTO dataset_meta VALUES ('2024-01-01', '2024-03-11', '2024-03-11', 1);
            INSERT INTO customers VALUES (1, '2023-12-01');
            INSERT INTO products VALUES (1, 'Desk', 'Office'), (2, 'Chair', 'Office');
            INSERT INTO orders
            SELECT day_num * 10 + booking_num, 1,
                   TIMESTAMP '2024-01-01 10:00' + day_num * INTERVAL '1 day',
                   CASE WHEN booking_num < 5 THEN 'North' ELSE 'South' END,
                   'Web', CASE WHEN day_num = 0 AND booking_num = 0 THEN 'partially_refunded' ELSE 'completed' END
            FROM range(70) d(day_num) CROSS JOIN range(10) b(booking_num);
            INSERT INTO order_lines
            SELECT order_id, order_id, order_id % 2 + 1, 1,
                   CASE WHEN order_id // 10 = 63 THEN 40000 WHEN order_id // 10 = 64 THEN 2500 ELSE 20000 END,
                   0, 1000 FROM orders;
            INSERT INTO refunds VALUES (1, 0, '2024-01-02 11:00', 10000, 0);
        """)
    for metric, filename in INVESTIGATION_FILES.items():
        export_investigation(database, None, root / filename, metric)
    return database, root


def test_kpis_compare_only_the_same_scored_dates(snapshot):
    workspace = load_workspace(*snapshot)
    kpis = overview_kpis(workspace.daily)
    assert kpis.actual_cents == 14_015_000  # 68 × $2,000 + $4,000 + $250 − $100
    assert (kpis.calendar_days, kpis.complete_days, kpis.scored_days, kpis.warmup_days) == (70, 70, 14, 56)
    assert (kpis.scored_actual_cents, kpis.expected_cents) == (2_825_000, 2_800_000)
    assert (kpis.deviation_cents, kpis.absolute_deviation_cents, kpis.flagged_days) == (25_000, 25_000, 2)
    assert kpis.percentage_deviation == pytest.approx(25_000 / 2_800_000 * 100)


def test_basis_and_inclusive_single_date_filter_keep_refund_definitions_separate(snapshot):
    posted = load_workspace(*snapshot)
    retrospective = load_workspace(*snapshot, RETROSPECTIVE_METRIC)
    jan1 = date(2024, 1, 1)
    jan2 = date(2024, 1, 2)
    assert posted.metric == POSTED_METRIC
    assert retrospective.metric == RETROSPECTIVE_METRIC
    assert filter_dates(posted.daily, jan1, jan1).net_revenue_cents.tolist() == [200_000]
    assert filter_dates(retrospective.daily, jan1, jan1).net_revenue_cents.tolist() == [190_000]
    assert filter_dates(posted.daily, jan1, jan2).net_revenue_cents.tolist() == [200_000, 190_000]
    assert filter_dates(retrospective.daily, jan1, jan2).net_revenue_cents.tolist() == [190_000, 200_000]
    assert filter_dates(posted.daily, jan1, jan2).expected_revenue_cents.isna().all()
    with pytest.raises(WorkspaceDataError, match="on or before"):
        filter_dates(posted.daily, jan2, jan1)


def test_queue_and_drilldown_conserve_contributions(snapshot):
    workspace = load_workspace(*snapshot)
    up, down = date(2024, 3, 4), date(2024, 3, 5)
    assert review_queue(workspace, up, down).date.dt.date.tolist() == [down, up]
    assert review_queue(workspace, up, down, directions=("up",), severities=("high",)).date.dt.date.tolist() == [up]
    assert review_queue(workspace, up, up, directions=("down",)).empty
    assert review_queue(workspace, up, down, severities=("low",)).empty
    assert alert_detail(workspace, date(2024, 3, 6)) is None
    alert = alert_detail(workspace, up)
    assert (alert["actual_revenue_cents"], alert["expected_revenue_cents"], alert["deviation_cents"]) == (
        400_000, 200_000, 200_000,
    )
    for group in alert["contributions"].values():
        assert sum(row["actual_cents"] for row in group["rows"]) == 400_000
        assert sum(row["allocated_expected_cents"] for row in group["rows"]) + group["unallocated_expected_cents"] == 200_000
        assert sum(row["deviation_cents"] for row in group["rows"]) + group["unallocated_deviation_cents"] == 200_000
    assert {r["segment"]: r["deviation_cents"] for r in alert["contributions"]["region"]["rows"]} == {
        "North": 100_000, "South": 100_000,
    }


def test_segment_view_shows_ledger_actuals_without_allocating_an_expectation(snapshot):
    workspace = load_workspace(*snapshot)
    jan2 = date(2024, 1, 2)
    north = scope_daily(workspace, "region", "North")
    assert filter_dates(north, jan2, jan2).net_revenue_cents.tolist() == [90_000]
    assert north.expected_revenue_cents.isna().all() and not north.is_anomaly.any()
    kpis = overview_kpis(north)
    assert kpis.actual_cents == 7_002_500
    assert kpis.scored_days == 0 and kpis.expected_cents is None
    with pytest.raises(WorkspaceDataError, match="Choose a segment"):
        scope_daily(workspace, "region", "Unknown")


def test_warmup_incomplete_empty_and_zero_expected_have_distinct_kpis():
    rows = pd.DataFrame({
        "is_complete": [True, True, False],
        "status": ["insufficient_history", "scored", "incomplete_day"],
        "net_revenue_cents": [50, 15, 1000],
        "expected_revenue_cents": pd.array([None, 0, None], dtype="Int64"),
        "is_anomaly": [False, False, False],
    })
    kpis = overview_kpis(rows)
    assert (kpis.actual_cents, kpis.expected_cents, kpis.deviation_cents) == (65, 0, 15)
    assert kpis.incomplete_days == 1 and kpis.warmup_days == 1 and kpis.percentage_deviation is None
    assert overview_kpis(rows.iloc[:1]).expected_cents is None
    assert overview_kpis(rows.iloc[2:]).actual_cents is None
    assert overview_kpis(rows.iloc[:0]).calendar_days == 0


def test_missing_or_mismatched_evidence_is_rejected(snapshot, tmp_path):
    database, root = snapshot
    with pytest.raises(WorkspaceDataError, match="database is missing"):
        load_workspace(tmp_path / "missing.duckdb", root)
    with pytest.raises(WorkspaceDataError, match="Missing anomaly-investigation.json"):
        load_workspace(database, tmp_path)
    filename = INVESTIGATION_FILES[POSTED_METRIC]
    evidence = json.loads((root / filename).read_text(encoding="utf-8"))
    # Move a cent between actual contributor buckets while preserving the aggregate.
    north, south = evidence["alerts"][0]["contributions"]["region"]["rows"]
    north["actual_cents"] += 1
    south["actual_cents"] -= 1
    (tmp_path / filename).write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(WorkspaceDataError, match="disagrees with the ledger"):
        load_workspace(database, tmp_path)
