"""Audit the original snapshot and produce evidence for locked-policy alerts."""

import argparse
from datetime import date
import json
from pathlib import Path

import duckdb

from .anomaly import detect_anomalies
from .benchmark import _series, alert_metrics, load_lock
from .cli import _write_json
from .db import metadata, open_dataset
from .generator import generate
from .investigation import investigate_alerts, retrospective_segment_daily
from .pit import POSTED_METRIC, RETROSPECTIVE_METRIC, SYNTHETIC_STATUS_WARNING, posted_activity, segment_series
from .screening import PORTFOLIO_POLICY, PORTFOLIO_POLICY_NAME, screen
from .validation import snapshot_inventory


def audit_snapshot(database, manifest_path, output):
    if output.resolve() in {database.resolve(), manifest_path.resolve()}:
        raise ValueError("Use an audit output path separate from the inputs.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with open_dataset(database) as conn:
        start, end, cutoff, seed = metadata(conn)
        if (manifest["coverage_start"], manifest["coverage_end"], manifest["seed"]) != (str(start), str(end), seed):
            raise ValueError("The manifest seed and coverage must match the snapshot.")
        if manifest["inventory"] != snapshot_inventory(conn):
            raise ValueError("The manifest inventory does not match the snapshot.")
        series, _, promotions = _series(conn)
        original = detect_anomalies(series[RETROSPECTIVE_METRIC])
        late_refunds = conn.execute("""
            SELECT COUNT(*), COALESCE(SUM(r.refund_cents), 0), COUNT(DISTINCT o.ordered_at::DATE)
            FROM refunds r JOIN order_lines l USING (line_id) JOIN orders o USING (order_id)
            WHERE r.refunded_at < ? AND r.refunded_at::DATE > o.ordered_at::DATE
        """, [cutoff]).fetchone()
    months = (end.year - start.year) * 12 + end.month - start.month
    with duckdb.connect() as conn:
        generate(conn, seed=seed, start=start, months=months, inject_anomalies=False)
        controls, _, _ = _series(conn)
    for metric in series:
        if int(series[metric].net_revenue_cents.sum()) != int(series[RETROSPECTIVE_METRIC].net_revenue_cents.sum()):
            raise ArithmeticError("Complete-window posted and order-date money should reconcile in this snapshot.")
    metrics = alert_metrics(original, manifest)
    control_alerts = detect_anomalies(controls[RETROSPECTIVE_METRIC])
    identical = {(row.date.date(), row.direction) for row in control_alerts.itertuples() if row.is_anomaly}
    rows = {row.date.date(): row for row in original.itertuples()}
    impacts = {}
    for metric, observed in series.items():
        paired = observed.merge(controls[metric], on="date", validate="one_to_one", suffixes=("", "_control"))
        impacts[metric] = {row.date.date(): int(row.net_revenue_cents) - int(row.net_revenue_cents_control)
                           for row in paired.itertuples()}
    event_details = []
    for event in manifest["anomalies"]:
        left, right = date.fromisoformat(event["start"]), date.fromisoformat(event["end"])
        event_details.append({"id": event["id"], "manifest_dates": [
            {"date": day.isoformat(), "actual_cents": row.net_revenue_cents,
             "expected_cents": round(row.expected_revenue_cents) if row.status == "scored" else None,
             "score": row.score if row.status == "scored" else None,
             "alert": row.is_anomaly, "retrospective_effect_cents": impacts[RETROSPECTIVE_METRIC][day],
             "posted_effect_cents": impacts[POSTED_METRIC][day]}
            for day, row in rows.items() if left <= day < right]})
    unmatched = []
    for day in map(date.fromisoformat, metrics["unmatched_dates"]):
        row = rows[day]
        unmatched.append({"date": day.isoformat(), "direction": row.direction, "score": row.score,
                          "planned_promotion": day in promotions, "same_alert_in_no_injection_control":
                          (day, row.direction) in identical})
    outside = [day for day in impacts[POSTED_METRIC] if not any(
        event["start"] <= day.isoformat() < event["end"] for event in manifest["anomalies"])]
    _write_json(output, {"seed": seed, "original": metrics, "event_details": event_details,
                         "unmatched_alerts": unmatched, "temporal_audit": {
        "later_day_refund_count": late_refunds[0], "later_day_refund_cents": late_refunds[1],
        "original_order_dates_affected": late_refunds[2],
        "full_window_net_cents_both_bases": int(series[POSTED_METRIC].net_revenue_cents.sum()),
        "posted_effect_outside_manifest_windows": [{"date": day.isoformat(), "cents": impacts[POSTED_METRIC][day]}
                                                    for day in outside if impacts[POSTED_METRIC][day]],
    }})


def export_investigation(database, lock, output, metric, *, segments=False):
    if output.resolve() == database.resolve() or (lock is not None and output.resolve() == lock.resolve()):
        raise ValueError("Use an investigation output path separate from the inputs.")
    if lock is None:
        name, policy = PORTFOLIO_POLICY_NAME, PORTFOLIO_POLICY
    else:
        _, name, policy = load_lock(lock)
    if segments and metric != POSTED_METRIC:
        raise ValueError("The optional segment scan is available for the posted-day metric.")
    with open_dataset(database) as conn:
        start, end, *_ = metadata(conn)
        series, posted_segments, promotions = _series(conn)
        daily = series[metric]
        long = posted_segments if metric == POSTED_METRIC else retrospective_segment_daily(conn, start, end)
    if policy is None:
        raise ValueError("Investigation requires a locked Phase 1.5 scoring policy.")
    scored = screen(daily, policy, planned_dates=promotions)
    warnings = [SYNTHETIC_STATUS_WARNING] if metric == POSTED_METRIC else [
        "Retrospective order-date values include later refunds. This view cannot replay a live alert."]
    alerts = investigate_alerts(scored, long, metric=metric, data_quality_warnings=warnings)
    result = {"synthetic": True, "currency": "USD", "metric": metric, "policy": name,
              "coverage": {str(k): int(v) for k, v in scored.status.value_counts().items()},
              "alerts": alerts}
    if segments:
        diagnostics = []
        with open_dataset(database) as conn:
            for dimension, segment in long[["dimension", "segment"]].drop_duplicates().itertuples(index=False):
                rows = screen(segment_series(daily, long, dimension, segment), policy, planned_dates=promotions)
                evidence = []
                if rows.is_anomaly.any():
                    _, scoped = posted_activity(conn, start, end, dimension=dimension, segment=segment)
                    evidence = investigate_alerts(rows, scoped, metric=metric, scope=f"{dimension}: {segment}",
                                                 data_quality_warnings=[*warnings, "Exploratory segment screen: "
                                                 "no segment-level benchmark or multiple-comparison calibration."])
                diagnostics.append({"dimension": dimension, "segment": segment,
                                    "scored_days": int(rows.status.eq("scored").sum()),
                                    "sparse_days": int(rows.status.eq("sparse_history").sum()),
                                    "exploratory_alerts": evidence})
        result["segment_scan"] = diagnostics
    _write_json(output, result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit")
    audit.add_argument("--manifest", type=Path, default=Path("data/demo/verification_manifest.json"))
    investigate = commands.add_parser("investigate")
    investigate.add_argument("--lock", type=Path, help="Optionally use a local benchmark development lock.")
    investigate.add_argument("--metric", choices=(POSTED_METRIC, RETROSPECTIVE_METRIC), default=POSTED_METRIC)
    investigate.add_argument("--segments", action="store_true")
    for command in (audit, investigate):
        command.add_argument("--database", type=Path, default=Path("data/demo/revenuescope.duckdb"))
        command.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.command == "audit":
        audit_snapshot(args.database, args.manifest, args.output or Path("output/anomaly-audit.json"))
    else:
        export_investigation(args.database, args.lock, args.output or Path("output/anomaly-investigation.json"),
                             args.metric, segments=args.segments)


if __name__ == "__main__":
    main()
