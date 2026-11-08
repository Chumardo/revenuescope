"""Reproducible anomaly audit. Run tune before opening the held-out evaluation."""

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean, median

import duckdb

from .analytics import daily_revenue
from .anomaly import detect_anomalies
from .cli import _write_json
from .db import metadata, open_dataset
from .generator import generate
from .pit import POSTED_METRIC, RETROSPECTIVE_METRIC, posted_activity, segment_series
from .screening import PORTFOLIO_POLICY, PORTFOLIO_POLICY_NAME, ScreeningPolicy, screen, synthetic_promotion_dates

DEV_SEEDS = (202502, 202503)
HOLDOUT_SEEDS = (202504, 202505, 202506)
_CALENDAR = dict(planned_promotion_policy=True)
_GATES = dict(min_impact_cents=125_000, min_relative_impact=0.25)
CANDIDATES = {
    "weekday_median": ScreeningPolicy(),
    "weekday_trend_calendar": ScreeningPolicy(method="trend", **_CALENDAR),
    PORTFOLIO_POLICY_NAME: PORTFOLIO_POLICY,
    "weekday_median_calendar_gates": ScreeningPolicy(**_CALENDAR, **_GATES),
}
PROTOCOL = "v1-fixed-windows-f05-pooled-across-two-dev-metrics"


def alert_metrics(scored, manifest: dict) -> dict:
    """Measure strict date hits and one-to-one same-direction event/episode hits."""
    statuses = dict(zip(scored.date.dt.date, scored.status, strict=True))
    alerts = {row.date.date(): row.direction for row in scored.itertuples() if row.is_anomaly}
    events = sorted((event for event in manifest["anomalies"] if event.get("scope", "overall") == "overall"),
                    key=lambda event: (event["end"], event["start"], event["id"]))
    observed, eligible, matched, dates_in_truth = [], [], set(), set()
    for event in events:
        start, end = date.fromisoformat(event["start"]), date.fromisoformat(event["end"])
        days = {day for day in statuses if start <= day < end}
        observed.append(days)
        eligible.append({day for day in days if statuses[day] == "scored"})
        dates_in_truth.update(days)
        matched.update(day for day in days if alerts.get(day) == event["direction"])

    episodes: list[list[date]] = []
    for day in sorted(alerts):
        if episodes and day - episodes[-1][-1] == timedelta(days=1) and alerts[day] == alerts[episodes[-1][-1]]:
            episodes[-1].append(day)
        else:
            episodes.append([day])
    edges = [{i: min(overlap) for i, days in enumerate(episodes)
              if alerts[days[0]] == event["direction"] and (overlap := in_window.intersection(days))}
             for event, in_window in zip(events, observed, strict=True)]
    assignment: dict[int, int] = {}

    def claim(event_index, visited):
        for episode_index in edges[event_index]:
            if episode_index not in visited:
                visited.add(episode_index)
                if episode_index not in assignment or claim(assignment[episode_index], visited):
                    assignment[episode_index] = event_index
                    return True
        return False

    for i in range(len(events)):
        claim(i, set())
    matches = {i: edges[i][episode] for episode, i in assignment.items()}
    event_rows = []
    for i, event in enumerate(events):
        match = matches.get(i)
        event_rows.append({
            "id": event["id"], "start": event["start"], "end_exclusive": event["end"],
            "expected_direction": event["direction"], "weight_cents": abs(event["net_revenue_delta_cents"]),
            "outcome": "detected" if match else "missed" if eligible[i] else "unscored" if observed[i] else "outside",
            "first_matching_date": match.isoformat() if match else None,
            "delay_days": (match - date.fromisoformat(event["start"])).days if match else None,
        })
    unmatched = set(alerts) - matched
    count_status = Counter(statuses.values())
    counts = {
        "calendar_days": len(statuses), "scored_days": count_status["scored"],
        "insufficient_history_days": count_status["insufficient_history"], "sparse_days": count_status["sparse_history"],
        "incomplete_days": count_status["incomplete_day"],
        "observed_calendar_months": len({(day.year, day.month) for day in statuses}),
        "alert_days": len(alerts), "date_matches": len(matched), "unmatched_alert_days": len(unmatched),
        "wrong_direction_days": len(unmatched & dates_in_truth), "alert_episodes": len(episodes),
        "eligible_injected_days": len(set().union(*eligible)), "injected_days_in_coverage": len(dates_in_truth),
        "event_matches": len(matches), "eligible_events": sum(bool(days) for days in eligible),
        "events_in_coverage": sum(bool(days) for days in observed),
        "detected_weight_cents": sum(row["weight_cents"] for row in event_rows if row["outcome"] == "detected"),
        "eligible_weight_cents": sum(row["weight_cents"] for row in event_rows if row["outcome"] in {"detected", "missed"}),
    }
    delays = [row["delay_days"] for row in event_rows if row["delay_days"] is not None]
    return {"counts": counts, **_rates(counts, delays), "delays_days": delays, "events": event_rows,
            "unmatched_dates": sorted(day.isoformat() for day in unmatched),
            "matching_dates": sorted(day.isoformat() for day in matched),
            "unmatched_by_month": dict(sorted(Counter(day.isoformat()[:7] for day in unmatched).items()))}


def _rates(counts, delays):
    def ratio(n, d):
        return counts[n] / counts[d] if counts[d] else None

    return {
        "coverage": ratio("scored_days", "calendar_days"),
        "date_precision": ratio("date_matches", "alert_days"),
        "date_recall": ratio("date_matches", "eligible_injected_days"),
        "event_precision": ratio("event_matches", "alert_episodes"),
        "event_recall": ratio("event_matches", "eligible_events"),
        "severity_weighted_recall": ratio("detected_weight_cents", "eligible_weight_cents"),
        "unmatched_per_calendar_month": ratio("unmatched_alert_days", "observed_calendar_months"),
        "mean_delay_days": mean(delays) if delays else None,
        "median_delay_days": median(delays) if delays else None,
    }


def _pool(rows):
    counts = Counter()
    delays = []
    for row in rows:
        counts.update(row["counts"])
        delays.extend(row["delays_days"])
    return {"counts": dict(counts), **_rates(counts, delays), "delays_days": delays}


def _f05(counts):
    # Algebraic form avoids undefined precision when no episode is flagged.
    return 1.25 * counts["event_matches"] / (0.25 * counts["eligible_events"] + counts["alert_episodes"])


def _series(conn):
    start, end, *_ = metadata(conn)
    posted, segments = posted_activity(conn, start, end)
    return {RETROSPECTIVE_METRIC: daily_revenue(conn, start, end), POSTED_METRIC: posted}, segments, (
        synthetic_promotion_dates(start, end))


def _run(seeds, methods, *, segment_probe=None):
    by_seed = {}
    for seed in seeds:
        with duckdb.connect() as conn:
            manifest = generate(conn, seed=seed)
            series, segments, calendar = _series(conn)
        record = {}
        for metric, daily in series.items():
            record[metric] = {}
            for name, policy in methods.items():
                scored = detect_anomalies(daily) if policy is None else screen(daily, policy, planned_dates=calendar)
                result = alert_metrics(scored, manifest)
                result["unmatched_on_planned_promotion"] = len(set(result["unmatched_dates"]) & {
                    day.isoformat() for day in calendar})
                result["suppressed_promotion_candidates"] = (
                    int(scored.suppression_reason.eq("planned_promotion").sum()) if policy else 0)
                record[metric][name] = result
        if segment_probe:
            summary = {}
            for (dimension, segment), _ in segments.groupby(["dimension", "segment"], sort=True):
                daily = segment_series(series[POSTED_METRIC], segments, dimension, segment)
                result = screen(daily, segment_probe, planned_dates=calendar)
                stats = summary.setdefault(dimension, Counter())
                stats.update(series=1, scope_days=len(result), scored_scope_days=int(result.status.eq("scored").sum()),
                             sparse_scope_days=int(result.status.eq("sparse_history").sum()),
                             raw_alerts=int(result.is_anomaly.sum()))
            record["segment_probe"] = {d: dict(stats) for d, stats in summary.items()}
        by_seed[str(seed)] = record
        print(f"Evaluated seed {seed}", flush=True)
    totals = {metric: {name: _pool([record[metric][name] for record in by_seed.values()]) for name in methods}
              for metric in (RETROSPECTIVE_METRIC, POSTED_METRIC)}
    return {"seeds": list(seeds), "per_seed": by_seed, "pooled": totals}


def _digest(path):
    return sha256(path.read_bytes()).hexdigest()


def tune(output):
    result = _run(DEV_SEEDS, {"original": None, **CANDIDATES})
    pooled = {name: _pool([result["pooled"][metric][name] for metric in (RETROSPECTIVE_METRIC, POSTED_METRIC)])
              for name in ("original", *CANDIDATES)}
    scores = {name: _f05(value["counts"]) for name, value in pooled.items()}
    # Keep severity-weighted recall at or above the original within each basis.
    finalists = [name for name in CANDIDATES if all(
        result["pooled"][metric][name]["severity_weighted_recall"] >=
        result["pooled"][metric]["original"]["severity_weighted_recall"]
        for metric in (RETROSPECTIVE_METRIC, POSTED_METRIC))]
    selected = max(finalists, key=scores.get) if finalists else "original"
    result.update(protocol=PROTOCOL, selection_scores_f05=scores, finalists=finalists, selected=selected,
                  locked_policy=asdict(CANDIDATES[selected]) if selected in CANDIDATES else None)
    _write_json(output, result)
    print(f"Locked on development seeds: {selected}; SHA256 {_digest(output)}", flush=True)


def load_lock(lock):
    development = json.loads(lock.read_text(encoding="utf-8"))
    if development.get("protocol") != PROTOCOL or development.get("seeds") != list(DEV_SEEDS):
        raise ValueError("Expected the development result from the fixed protocol.")
    selected = development["selected"]
    policy = ScreeningPolicy(**development["locked_policy"]) if development["locked_policy"] else None
    if selected not in {"original", *CANDIDATES} or policy != CANDIDATES.get(selected):
        raise ValueError("The locked selection does not match the registered protocol.")
    return development, selected, policy


def evaluate(lock, output):
    development, selected, policy = load_lock(lock)
    result = _run(HOLDOUT_SEEDS, {"original": None, "locked_candidate": policy}, segment_probe=policy)
    result.update(protocol=PROTOCOL, development_sha256=_digest(lock), locked_candidate=selected,
                  locked_policy=development["locked_policy"])
    _write_json(output, result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    development = commands.add_parser("tune")
    development.add_argument("--output", type=Path, default=Path("output/anomaly-development.json"))
    heldout = commands.add_parser("evaluate")
    heldout.add_argument("--lock", type=Path, default=Path("output/anomaly-development.json"))
    heldout.add_argument("--output", type=Path, default=Path("output/anomaly-heldout.json"))
    args = parser.parse_args(argv)
    if args.command == "tune":
        tune(args.output)
    else:
        evaluate(args.lock, args.output)


if __name__ == "__main__":
    main()
