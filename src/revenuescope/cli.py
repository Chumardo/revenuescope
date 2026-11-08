"""Generate, analyze, and verify a local RevenueScope dataset."""

import argparse
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
import pandas as pd

from .db import metadata, open_dataset


DB_NAME = "revenuescope.duckdb"
MANIFEST_NAME = "verification_manifest.json"
DEMO_DIR = Path("data/demo")


def _json_default(value):
    if isinstance(value, (date, Decimal)):
        return str(value)
    if isinstance(value, Fraction):
        return {"numerator": value.numerator, "denominator": value.denominator}
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Cannot serialize {type(value).__name__}.")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent, prefix=".revenuescope-json-") as temporary:
        pending = Path(temporary) / path.name
        pending.write_text(
            json.dumps(data, indent=2, default=_json_default, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        pending.replace(path)


def _records(frame: pd.DataFrame) -> list[dict]:
    return [{k: None if pd.isna(v) else v for k, v in row.items()}
            for row in frame.to_dict(orient="records")]


def _generate(args: argparse.Namespace) -> None:
    from .generator import generate
    from .validation import independent_ledger, snapshot_inventory

    target = args.output.resolve()
    if target.exists():
        raise ValueError(f"Dataset directory already exists: {target}. Choose a new output directory.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=target.parent, prefix=".revenuescope-build-") as temporary:
        staged = Path(temporary) / "dataset"
        staged.mkdir()
        with duckdb.connect(str(staged / DB_NAME)) as conn:
            manifest = generate(conn, seed=args.seed, start=args.start, months=args.months)
            independent_ledger(conn)
            manifest["inventory"] = snapshot_inventory(conn)
            meta = metadata(conn)
        _write_json(staged / MANIFEST_NAME, manifest)
        staged.rename(target)
    print(f"Generated {target / DB_NAME}")
    print(f"Coverage {meta.coverage_start} to {meta.coverage_end} (exclusive)")
    print(f"Rows: {json.dumps(manifest['inventory']['row_counts'])}")
    print(f"Verification answer key: {target / MANIFEST_NAME}")


def _default_periods(complete_months: list[date]):
    from .variance import Period

    if len(complete_months) < 2:
        return None
    previous, latest = complete_months[-2:]
    following = (latest.replace(day=28) + timedelta(days=4)).replace(day=1)
    return Period(previous, latest), Period(latest, following)


def _report(args: argparse.Namespace) -> None:
    from .analytics import customer_cohorts, daily_revenue, monthly_kpis, summarize
    from .anomaly import detect_anomalies
    from .variance import Period, compare_periods

    if args.output.resolve() == args.database.resolve() or args.output.name == MANIFEST_NAME:
        raise ValueError("Choose a report path separate from the database and verification manifest.")
    dates = (args.base_start, args.base_end, args.current_start, args.current_end)
    if any(dates) and not all(dates):
        raise ValueError("Supply all four comparison date arguments together.")
    with open_dataset(args.database) as conn:
        meta = metadata(conn)
        start, end = meta.coverage_start, meta.coverage_end
        monthly = monthly_kpis(conn, start, end)
        if all(dates):
            comparison = Period(args.base_start, args.base_end), Period(args.current_start, args.current_end)
        else:
            comparison = _default_periods([date.fromisoformat(str(m)[:10])
                                            for m in monthly.loc[monthly["is_complete"], "month"]])
        daily = daily_revenue(conn, start, end)
        variance = asdict(compare_periods(conn, *comparison)) if comparison else None
        if variance:
            variance["baseline_days"], variance["current_days"] = (p.days for p in comparison)
        result = {
            "dataset": {**meta._asdict(), "synthetic": True, "currency": "USD",
                        "time_zone": "UTC", "dates": "start inclusive, end exclusive"},
            "summary": _records(summarize(conn, start, end))[0],
            "monthly": _records(monthly),
            "segments": {d: _records(summarize(conn, start, end, dimension=d))
                         for d in ("product", "category", "region", "channel")},
            "variance": variance,
            "cohorts": _records(customer_cohorts(conn, start, end)),
            "daily_anomaly_scores": _records(detect_anomalies(daily)),
        }
    _write_json(args.output, result)
    print(f"Report: {args.output.resolve()}")
    print(f"Recognized orders: {result['summary']['orders']:,}")
    print(f"Net revenue (USD cents): {result['summary']['net_revenue_cents']:,}")


def _verify(args: argparse.Namespace) -> None:
    from .analytics import daily_revenue
    from .anomaly import detect_anomalies, evaluate_anomalies
    from .generator import generate
    from .validation import independent_ledger, snapshot_inventory, verify_analytics

    if args.output.resolve() in (args.database.resolve(), args.manifest.resolve()):
        raise ValueError("Choose a verification output separate from the inputs.")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    with open_dataset(args.database) as conn:
        meta = metadata(conn)
        inventory = snapshot_inventory(conn)
        if inventory != manifest.get("inventory"):
            raise ValueError("The verification manifest does not match this dataset snapshot.")
        ledger = independent_ledger(conn)
        checked = verify_analytics(conn, ledger)
        scored = detect_anomalies(daily_revenue(conn, meta.coverage_start, meta.coverage_end))
    evaluation = evaluate_anomalies(scored, manifest, scope="overall")
    result = {
        "inventory": inventory, **checked, "order_status_counts": ledger["order_status_counts"],
        "anomaly_evaluation": evaluation,
    }
    if args.replay:
        months = ((meta.coverage_end.year - meta.coverage_start.year) * 12
                  + meta.coverage_end.month - meta.coverage_start.month)
        with duckdb.connect() as conn:
            generate(conn, seed=meta.seed, start=meta.coverage_start, months=months)
            if snapshot_inventory(conn) != inventory:
                raise ArithmeticError("Replay did not reproduce the logical snapshot.")
        with duckdb.connect() as conn:
            generate(conn, seed=meta.seed, start=meta.coverage_start, months=months, inject_anomalies=False)
            counterfactual = independent_ledger(conn)
        checks = []
        injected_dates = set()
        for event in manifest["anomalies"]:
            start, end = date.fromisoformat(event["start"]), date.fromisoformat(event["end"])
            days = {start + timedelta(days=i) for i in range((end - start).days)}
            injected_dates.update(days)
            actual = {
                f"{scenario}_{measure}": sum(source[key].get(day, 0) for day in days)
                for scenario, source in (("baseline", counterfactual), ("observed", ledger))
                for measure, key in (("net_revenue_cents", "daily"), ("recognized_orders", "daily_orders"))
            }
            impact = actual["observed_net_revenue_cents"] - actual["baseline_net_revenue_cents"]
            if any(event[k] != v for k, v in actual.items()) or impact != event["net_revenue_delta_cents"]:
                raise ArithmeticError(f"Injected event values differ from replay for {event['id']}.")
            checks.append({"id": event["id"], **actual, "observed_minus_baseline_cents": impact})
        for key in ("daily", "daily_orders"):
            if any(ledger[key].get(day, 0) != counterfactual[key].get(day, 0)
                   for day in (ledger[key].keys() | counterfactual[key].keys()) - injected_dates):
                raise ArithmeticError(f"Replay changed {key} outside the injected event windows.")
        result["replay"] = {"logical_snapshot_reproduced": True, "outside_event_windows_unchanged": True,
                            "counterfactual_checks": checks}
    _write_json(args.output, result)
    print(f"Verification: {args.output.resolve()}")
    print(f"Independent ledger matched SQL across {checked['checked_days']} daily buckets, "
          f"{checked['checked_months']} monthly buckets and "
          f"{len(checked['checked_dimensions'])} segment dimensions.")
    print("Manifest detection results and false alerts are in the verification file.")


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use an ISO date (YYYY-MM-DD).") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="revenuescope", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    gen = commands.add_parser("generate", help="Create a reproducible synthetic dataset.")
    gen.add_argument("--output", type=Path, default=DEMO_DIR)
    gen.add_argument("--seed", type=int, default=202501)
    gen.add_argument("--start", type=_parse_date, default=date(2023, 1, 1))
    gen.add_argument("--months", type=int, default=24)
    gen.set_defaults(run=_generate)

    report = commands.add_parser("report", help="Run the analytical reports without the answer key.")
    report.add_argument("--database", type=Path, default=DEMO_DIR / DB_NAME)
    report.add_argument("--output", type=Path, default=Path("output/report.json"))
    for option in ("base-start", "base-end", "current-start", "current-end"):
        report.add_argument(f"--{option}", type=_parse_date)
    report.set_defaults(run=_report)

    verify = commands.add_parser("verify", help="Reconcile SQL and score anomaly alerts against the answer key.")
    verify.add_argument("--database", type=Path, default=DEMO_DIR / DB_NAME)
    verify.add_argument("--manifest", type=Path, default=DEMO_DIR / MANIFEST_NAME)
    verify.add_argument("--output", type=Path, default=Path("output/verification.json"))
    verify.add_argument("--replay", action="store_true",
                        help="Regenerate the snapshot and a no-injection baseline for independent checks.")
    verify.set_defaults(run=_verify)

    args = parser.parse_args(argv)
    try:
        args.run(args)
    except (ValueError, OSError, ArithmeticError, duckdb.Error) as exc:
        parser.exit(1, f"RevenueScope: {exc}\n")
    return 0

