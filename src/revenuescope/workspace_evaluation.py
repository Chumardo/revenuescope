"""Read and display the frozen Phase 1.5 held-out benchmark."""

from dataclasses import asdict
from hashlib import sha256
import json
from math import isclose
from pathlib import Path
import re
from statistics import mean, median

import pandas as pd

from .benchmark import DEV_SEEDS, HOLDOUT_SEEDS, PROTOCOL
from .pit import POSTED_METRIC, RETROSPECTIVE_METRIC
from .screening import PORTFOLIO_POLICY, PORTFOLIO_POLICY_NAME


BASES = {"Posted-day": POSTED_METRIC, "Order-day retrospective": RETROSPECTIVE_METRIC}
METHODS = {"Original detector": "original", "Locked detector": "locked_candidate"}
COUNTS = (
    "calendar_days", "scored_days", "insufficient_history_days", "sparse_days", "incomplete_days",
    "observed_calendar_months", "alert_days", "date_matches", "unmatched_alert_days", "wrong_direction_days",
    "alert_episodes", "eligible_injected_days", "injected_days_in_coverage", "event_matches", "eligible_events",
    "events_in_coverage", "detected_weight_cents", "eligible_weight_cents",
)
RATIOS = {
    "coverage": ("scored_days", "calendar_days"),
    "date_precision": ("date_matches", "alert_days"),
    "date_recall": ("date_matches", "eligible_injected_days"),
    "event_precision": ("event_matches", "alert_episodes"),
    "event_recall": ("event_matches", "eligible_events"),
    "severity_weighted_recall": ("detected_weight_cents", "eligible_weight_cents"),
    "unmatched_per_calendar_month": ("unmatched_alert_days", "observed_calendar_months"),
}


def _summary(raw: dict) -> dict:
    counts = {name: raw["counts"][name] for name in COUNTS}
    if any(type(value) is not int or value < 0 for value in counts.values()):
        raise ValueError("Benchmark counts must be nonnegative integers.")
    if counts["calendar_days"] != sum(counts[key] for key in (
        "scored_days", "insufficient_history_days", "sparse_days", "incomplete_days",
    )) or counts["alert_days"] != counts["date_matches"] + counts["unmatched_alert_days"]:
        raise ValueError("Benchmark coverage or alert counts do not reconcile.")
    bounds = (
        ("alert_days", "scored_days"), ("alert_episodes", "alert_days"),
        ("date_matches", "eligible_injected_days"), ("wrong_direction_days", "unmatched_alert_days"),
        ("eligible_injected_days", "injected_days_in_coverage"),
        ("injected_days_in_coverage", "calendar_days"),
        ("event_matches", "eligible_events"), ("event_matches", "alert_episodes"),
        ("eligible_events", "events_in_coverage"), ("detected_weight_cents", "eligible_weight_cents"),
    )
    if any(counts[lower] > counts[upper] for lower, upper in bounds):
        raise ValueError("A benchmark numerator exceeds its denominator.")
    delays = raw["delays_days"]
    if not isinstance(delays, list) or len(delays) != counts["event_matches"] or any(
        type(delay) is not int or delay < 0 for delay in delays
    ):
        raise ValueError("Benchmark delay samples do not match detected events.")
    measures = {
        name: counts[numerator] / counts[denominator] if counts[denominator] else None
        for name, (numerator, denominator) in RATIOS.items()
    }
    measures.update(mean_delay_days=mean(delays) if delays else None,
                    median_delay_days=median(delays) if delays else None)
    for name, expected in measures.items():
        provided = raw[name]
        if expected is None:
            matches = provided is None
        else:
            matches = type(provided) in {float, int} and isclose(provided, expected, abs_tol=1e-12)
        if not matches:
            raise ValueError(f"Benchmark measure {name} does not match its underlying counts.")
    return {"counts": counts, "delays_days": tuple(delays), **measures}


def load_evaluation(path: Path, development_path: Path | None = None) -> dict:
    """Validate benchmark identity, totals and rates, and expose only aggregate evidence."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        digest = raw["development_sha256"]
        if (raw["protocol"] != PROTOCOL or raw["seeds"] != list(HOLDOUT_SEEDS)
                or raw["locked_candidate"] != PORTFOLIO_POLICY_NAME
                or raw["locked_policy"] != asdict(PORTFOLIO_POLICY)
                or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)):
            raise ValueError("Benchmark identity does not match the frozen Phase 1.5 protocol.")
        if development_path is not None:
            payload = Path(development_path).read_bytes()
            if sha256(payload).hexdigest() != digest:
                raise ValueError("The held-out benchmark does not match the supplied development lock.")
            lock = json.loads(payload)
            if (lock["protocol"] != PROTOCOL or lock["seeds"] != list(DEV_SEEDS)
                    or lock["selected"] != PORTFOLIO_POLICY_NAME
                    or lock["locked_policy"] != asdict(PORTFOLIO_POLICY)):
                raise ValueError("The development lock does not match the frozen policy.")
        pooled, by_seed = {}, {seed: {} for seed in HOLDOUT_SEEDS}
        if set(raw["per_seed"]) != {str(seed) for seed in HOLDOUT_SEEDS}:
            raise ValueError("Benchmark does not have the expected held-out seed records.")
        for basis in BASES.values():
            pooled[basis] = {}
            for seed in by_seed:
                by_seed[seed][basis] = {}
            for method in METHODS.values():
                combined = _summary(raw["pooled"][basis][method])
                individual = []
                for seed in by_seed:
                    row = _summary(raw["per_seed"][str(seed)][basis][method])
                    by_seed[seed][basis][method] = row
                    individual.append(row)
                if any(combined["counts"][key] != sum(row["counts"][key] for row in individual)
                       for key in COUNTS) or combined["delays_days"] != tuple(
                    delay for row in individual for delay in row["delays_days"]
                ):
                    raise ValueError("Pooled benchmark results do not reconcile to the held-out seeds.")
                pooled[basis][method] = combined
    except OSError as exc:
        raise ValueError("Could not validate the Phase 1.5 evaluation artifacts. "
                         "Check that the held-out benchmark and development lock are present and readable.") from exc
    except (AttributeError, KeyError, OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"Could not validate the Phase 1.5 evaluation artifacts. {exc}") from exc
    return {"seeds": HOLDOUT_SEEDS, "pooled": pooled, "by_seed": by_seed,
            "development_lock_verified": development_path is not None}


def _percent(value):
    return f"{value:.2%}" if value is not None else "Unavailable"


def _number(value):
    return f"{value:.2f}" if value is not None else "Unavailable"


def _comparison_rows(methods: dict) -> list[dict]:
    cells = (
        ("Date precision", lambda m: _percent(m["date_precision"])),
        ("Date recall", lambda m: _percent(m["date_recall"])),
        ("Event precision", lambda m: _percent(m["event_precision"])),
        ("Event recall", lambda m: _percent(m["event_recall"])),
        ("Severity-weighted event recall", lambda m: _percent(m["severity_weighted_recall"])),
        ("Benchmark-unmatched alert dates", lambda m: f"{m['counts']['unmatched_alert_days']:,}"),
        ("Unmatched alert dates per month", lambda m: _number(m["unmatched_per_calendar_month"])),
        ("Mean detection delay (days)", lambda m: _number(m["mean_delay_days"])),
        ("Median detection delay (days)", lambda m: _number(m["median_delay_days"])),
        ("Scored dates", lambda m: f"{m['counts']['scored_days']:,} / {m['counts']['calendar_days']:,}"),
        ("Warmup exclusions", lambda m: f"{m['counts']['insufficient_history_days']:,}"),
    )
    return [{"Measure": label, **{name: formatter(methods[key]) for name, key in METHODS.items()}}
            for label, formatter in cells]


def event_headlines(method: dict) -> tuple[tuple[str, str, str], ...]:
    counts = method["counts"]
    matches = counts["event_matches"]
    return (
        ("Locked event precision", _percent(method["event_precision"]),
         f"{matches} of {counts['alert_episodes']} alert episodes matched"),
        ("Locked event recall", _percent(method["event_recall"]),
         f"{matches} of {counts['eligible_events']} eligible events detected"),
    )


def render_evaluation(benchmark: dict) -> None:
    """Present out-of-sample evidence separately from the analyst queue."""
    import streamlit as st

    st.title("Detector evaluation")
    st.write("Compare the original and locked detectors on the same held-out synthetic datasets.")
    label = st.radio("Evaluation revenue basis", list(BASES), horizontal=True, key="evaluation_basis")
    basis = BASES[label]
    if basis == RETROSPECTIVE_METRIC:
        st.info("Order-day retrospective revenue attributes later refunds to the original order date. "
                "This comparison uses hindsight.")
    else:
        st.caption("Posted-day activity books checkout value and subtracts refunds on the day they are posted.")

    methods = benchmark["pooled"][basis]
    original, locked = methods["original"], methods["locked_candidate"]
    counts = locked["counts"]
    with st.container(key="rs_eval_kpis"):
        cards = st.columns(3, gap="small")
        for card, (label, value, denominator) in zip(cards, event_headlines(locked)):
            card.metric(label, value, border=True)
            card.caption(denominator)
        cards[2].metric("Unmatched alert dates", f"{counts['unmatched_alert_days']:,}", border=True)
        cards[2].caption(f"{original['counts']['unmatched_alert_days']:,} with the original detector")
    st.caption("Three held-out seeds repeat the same five synthetic event types. Both revenue bases use the "
               "original order-date event windows. A refund can affect posted-day revenue after its window ends.")

    st.subheader("Original vs. locked detector")
    st.caption("Benchmark-unmatched dates had no same-direction match in the synthetic manifest. "
               "Verifying a false positive would require additional evidence.")
    st.html(pd.DataFrame(_comparison_rows(methods)).to_html(index=False, escape=True, border=0,
                                                            classes="rs-comparison", justify="left"))

    st.subheader("How to read the results")
    st.markdown(
        "**Date-level** precision divides matching flag dates by all flagged dates. "
        "Recall divides by scored injected dates. **Event-level** precision divides matched alert episodes "
        "by all alert episodes. Recall divides by eligible injected events. An event is eligible when at least "
        "one day in its original order-date window was scored. Consecutive same-direction flags form one "
        "episode, and an episode can match at most one event."
    )
    st.markdown(
        "A match needs a flag in the expected direction inside the original window. An alert episode entirely "
        "outside that window cannot count toward that event's recall, even if a refund has just posted. "
        "Detection delay counts calendar days from the window start to the first matching date in the "
        "assigned episode. A zero-day delay means the match fell on the start date. Severity-weighted recall "
        "uses the absolute injected revenue change attributed to the order window."
    )
    with st.expander("Results by held-out seed"):
        per_seed = []
        for seed, metrics in benchmark["by_seed"].items():
            for label, key in METHODS.items():
                row = metrics[basis][key]
                counts = row["counts"]
                per_seed.append({"Seed": str(seed), "Detector": label,
                                 "Matched dates": counts["date_matches"], "Matched events": counts["event_matches"],
                                 "Unmatched alert dates": counts["unmatched_alert_days"]})
        st.dataframe(per_seed, width="stretch", hide_index=True, key="heldout_seed_results")
