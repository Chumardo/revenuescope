"""Read-only ledger and verified investigation evidence for the analyst workspace."""

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path

import duckdb
import pandas as pd

from .analytics import daily_revenue
from .db import DatasetMetadata, metadata, open_dataset
from .investigation import investigate_alerts, retrospective_segment_daily
from .pit import POSTED_METRIC, RETROSPECTIVE_METRIC, SYNTHETIC_STATUS_WARNING, posted_activity
from .screening import PORTFOLIO_POLICY, PORTFOLIO_POLICY_NAME, screen, synthetic_promotion_dates

INVESTIGATION_FILES = {
    POSTED_METRIC: "anomaly-investigation.json",
    RETROSPECTIVE_METRIC: "anomaly-investigation-retrospective.json",
}
DIMENSIONS = ("region", "product", "channel")
_RETROSPECTIVE_WARNING = (
    "Retrospective order-date values include later refunds. This view cannot replay a live alert."
)
_QUEUE_COLUMNS = (
    "date", "actual_revenue_cents", "expected_revenue_cents", "deviation_cents", "absolute_deviation_cents",
    "percentage_deviation", "direction", "severity",
)


class WorkspaceDataError(ValueError):
    """Data unavailable or mismatched, with a message suitable for the workspace."""


@dataclass(frozen=True)
class WorkspaceData:
    metric: str
    metadata: DatasetMetadata
    daily: pd.DataFrame
    segment_daily: pd.DataFrame
    alerts: dict[date, dict]
    segments: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class OverviewKpis:
    actual_cents: int | None
    scored_actual_cents: int | None
    expected_cents: int | None
    deviation_cents: int | None
    absolute_deviation_cents: int | None
    percentage_deviation: float | None
    calendar_days: int
    complete_days: int
    scored_days: int
    flagged_days: int
    warmup_days: int
    sparse_days: int
    incomplete_days: int


def _load_alerts(path: Path, metric: str, scored: pd.DataFrame, segments: pd.DataFrame) -> dict[date, dict]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkspaceDataError(f"Missing {path.name}. Recreate the Phase 1.5 investigation export.") from exc
    except (OSError, UnicodeError, ValueError) as exc:
        raise WorkspaceDataError(f"Could not read {path.name} as a UTF-8 investigation export.") from exc
    if not isinstance(report, dict) or not (
        report.get("synthetic") is True and report.get("currency") == "USD"
        and report.get("metric") == metric and report.get("policy") == PORTFOLIO_POLICY_NAME
    ):
        raise WorkspaceDataError(f"{path.name} has incompatible synthetic-data, currency, revenue basis, or policy metadata.")

    # Reuse the locked evidence calculation to check every displayed field, including
    # each segment's actual cents, reference dates, and reconciled expected amounts.
    warning = SYNTHETIC_STATUS_WARNING if metric == POSTED_METRIC else _RETROSPECTIVE_WARNING
    expected = investigate_alerts(scored, segments, metric=metric, data_quality_warnings=[warning])
    coverage = {str(key): int(value) for key, value in scored.status.value_counts().items()}
    try:
        same_alerts = json.dumps(report.get("alerts"), sort_keys=True, allow_nan=False) == json.dumps(
            expected, sort_keys=True, allow_nan=False,
        )
    except (TypeError, ValueError):
        same_alerts = False
    if report.get("coverage") != coverage or not same_alerts:
        raise WorkspaceDataError(f"{path.name} disagrees with the ledger or locked detector. Recreate the export.")
    return {date.fromisoformat(alert["date"]): alert for alert in report["alerts"]}


def load_workspace(database: Path, output_dir: Path, metric: str = POSTED_METRIC) -> WorkspaceData:
    """Score the full snapshot before any UI filtering and validate its evidence file."""
    if metric not in INVESTIGATION_FILES:
        raise WorkspaceDataError("Choose either the posted-day or retrospective order-day revenue basis.")
    database = Path(database)
    if not database.is_file():
        raise WorkspaceDataError("The synthetic RevenueScope database is missing. Generate the demo snapshot first.")
    try:
        with open_dataset(database) as connection:
            snapshot = metadata(connection)
            start, end = snapshot.coverage_start, snapshot.coverage_end
            if metric == POSTED_METRIC:
                daily, segment_daily = posted_activity(connection, start, end)
            else:
                daily = daily_revenue(connection, start, end)
                segment_daily = retrospective_segment_daily(connection, start, end)
    except (duckdb.Error, OSError, ValueError) as exc:
        raise WorkspaceDataError("Could not read the synthetic snapshot and its coverage metadata.") from exc

    scored = screen(daily, PORTFOLIO_POLICY, planned_dates=synthetic_promotion_dates(start, end))
    alerts = _load_alerts(Path(output_dir) / INVESTIGATION_FILES[metric], metric, scored, segment_daily)
    scored["deviation_cents"] = scored["residual_cents"]
    scored["absolute_deviation_cents"] = scored["residual_cents"].abs()
    expected = scored["expected_revenue_cents"].abs().replace(0, pd.NA)
    scored["percentage_deviation"] = 100 * scored["residual_cents"].astype("Float64") / expected
    segments = {
        dimension: tuple(sorted(segment_daily.loc[segment_daily.dimension.eq(dimension), "segment"].unique()))
        for dimension in DIMENSIONS
    }
    return WorkspaceData(metric, snapshot, scored, segment_daily, alerts, segments)


def scope_daily(workspace: WorkspaceData, dimension: str | None = None, segment: str | None = None) -> pd.DataFrame:
    """Select overall scored data or an actual-only segment ledger over the same calendar."""
    if dimension is None and segment is None:
        return workspace.daily
    if dimension not in workspace.segments or segment not in workspace.segments[dimension]:
        raise WorkspaceDataError("Choose a segment from the selected revenue dimension.")
    segment_daily = workspace.segment_daily
    values = segment_daily.loc[
        segment_daily.dimension.eq(dimension) & segment_daily.segment.eq(segment), ["date", "net_revenue_cents"],
    ].astype({"net_revenue_cents": "Int64"})
    rows = workspace.daily[["date", "is_complete"]].merge(values, on="date", how="left", validate="one_to_one")
    rows["net_revenue_cents"] = rows["net_revenue_cents"].fillna(0).astype("int64")
    rows["status"] = rows["is_complete"].map({True: "actual_only", False: "incomplete_day"})
    rows["is_anomaly"] = False
    for column in ("expected_revenue_cents", "residual_cents", "deviation_cents", "absolute_deviation_cents"):
        rows[column] = pd.Series(pd.NA, index=rows.index, dtype="Int64")
    rows["percentage_deviation"] = pd.Series(pd.NA, index=rows.index, dtype="Float64")
    return rows


def filter_dates(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """The UI uses inclusive UTC calendar dates, distinct from SQL's exclusive end."""
    if start > end:
        raise WorkspaceDataError("The first filter date must be on or before the last date.")
    return frame.loc[frame.date.between(pd.Timestamp(start), pd.Timestamp(end))].copy()


def overview_kpis(frame: pd.DataFrame) -> OverviewKpis:
    """Use complete dates for actuals and the same scored dates for both sides of a comparison."""
    complete = frame.loc[frame["is_complete"]]
    comparable = complete.loc[complete["status"].eq("scored") & complete["expected_revenue_cents"].notna()]
    actual = sum(map(int, complete["net_revenue_cents"])) if len(complete) else None
    scored_actual = sum(map(int, comparable["net_revenue_cents"])) if len(comparable) else None
    expected = sum(map(int, comparable["expected_revenue_cents"])) if len(comparable) else None
    deviation = scored_actual - expected if expected is not None else None
    return OverviewKpis(
        actual, scored_actual, expected, deviation, abs(deviation) if deviation is not None else None,
        100 * deviation / abs(expected) if expected else None,
        len(frame), len(complete), len(comparable), int(comparable["is_anomaly"].sum()),
        int(frame["status"].eq("insufficient_history").sum()), int(frame["status"].eq("sparse_history").sum()),
        len(frame) - len(complete),
    )


def review_queue(workspace: WorkspaceData, start: date, end: date, *, directions=(), severities=()) -> pd.DataFrame:
    """Return reviewed-basis detector flags in range, newest first."""
    if start > end:
        raise WorkspaceDataError("The first filter date must be on or before the last date.")
    rows = [
        {column: alert[column] for column in _QUEUE_COLUMNS}
        for day, alert in sorted(workspace.alerts.items(), reverse=True)
        if start <= day <= end and (not directions or alert["direction"] in directions)
        and (not severities or alert["severity"] in severities)
    ]
    queue = pd.DataFrame(rows, columns=_QUEUE_COLUMNS)
    queue["date"] = pd.to_datetime(queue["date"])
    return queue


def alert_detail(workspace: WorkspaceData, day: date) -> dict | None:
    """Find a flagged date only within this workspace's selected revenue basis."""
    return workspace.alerts.get(day)
