from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json

import pytest

from revenuescope.benchmark import DEV_SEEDS, HOLDOUT_SEEDS, PROTOCOL
from revenuescope.pit import POSTED_METRIC, RETROSPECTIVE_METRIC
from revenuescope.screening import PORTFOLIO_POLICY, PORTFOLIO_POLICY_NAME
from revenuescope.workspace_evaluation import event_headlines, load_evaluation


def _method(n, unmatched):
    return {
        "counts": {
            "calendar_days": 12 * n, "scored_days": 10 * n, "insufficient_history_days": 2 * n,
            "sparse_days": 0, "incomplete_days": 0, "observed_calendar_months": n,
            "alert_days": (2 + unmatched) * n, "date_matches": 2 * n,
            "unmatched_alert_days": unmatched * n, "wrong_direction_days": 0,
            "alert_episodes": (1 + unmatched) * n, "eligible_injected_days": 3 * n,
            "injected_days_in_coverage": 3 * n, "event_matches": n, "eligible_events": 2 * n,
            "events_in_coverage": 2 * n, "detected_weight_cents": 300 * n, "eligible_weight_cents": 400 * n,
        },
        "coverage": 10 / 12, "date_precision": 2 / (2 + unmatched), "date_recall": 2 / 3,
        "event_precision": 1 / (1 + unmatched), "event_recall": 0.5, "severity_weighted_recall": 0.75,
        "unmatched_per_calendar_month": unmatched, "mean_delay_days": 1, "median_delay_days": 1,
        "delays_days": [1] * n, "events": [{"private_marker": "only developer evidence"}],
    }


@pytest.fixture
def artifacts(tmp_path):
    lock = tmp_path / "development.json"
    lock.write_text(json.dumps({"protocol": PROTOCOL, "seeds": list(DEV_SEEDS),
                                "selected": PORTFOLIO_POLICY_NAME, "locked_policy": asdict(PORTFOLIO_POLICY)}))

    def comparisons(n):
        return {POSTED_METRIC: {"original": _method(n, 4), "locked_candidate": _method(n, 2)},
                RETROSPECTIVE_METRIC: {"original": _method(n, 3), "locked_candidate": _method(n, 1)}}

    artifact = {"protocol": PROTOCOL, "seeds": list(HOLDOUT_SEEDS), "locked_candidate": PORTFOLIO_POLICY_NAME,
                "locked_policy": asdict(PORTFOLIO_POLICY), "development_sha256": sha256(lock.read_bytes()).hexdigest(),
                "pooled": comparisons(len(HOLDOUT_SEEDS)),
                "per_seed": {str(seed): comparisons(1) for seed in HOLDOUT_SEEDS}}
    return tmp_path / "heldout.json", lock, artifact


def test_validated_comparisons_keep_distinct_bases_and_denominators(artifacts):
    heldout, lock, payload = artifacts
    heldout.write_text(json.dumps(payload))
    result = load_evaluation(heldout, lock)
    assert result["development_lock_verified"]
    posted = result["pooled"][POSTED_METRIC]["locked_candidate"]
    retro = result["pooled"][RETROSPECTIVE_METRIC]["locked_candidate"]
    assert posted["date_precision"] == 6 / 12
    assert posted["event_precision"] == 3 / 9
    assert posted["event_recall"] == 3 / 6
    assert posted["counts"]["unmatched_alert_days"] == 6
    assert retro["counts"]["unmatched_alert_days"] == 3
    assert "private_marker" not in str(result)


def test_reject_mismatched_lock_and_altered_policy(artifacts):
    heldout, lock, payload = artifacts
    heldout.write_text(json.dumps(payload))
    lock.write_text(lock.read_text() + "\n")
    with pytest.raises(ValueError, match="supplied development lock"):
        load_evaluation(heldout, lock)
    payload["locked_policy"]["min_history"] += 1
    heldout.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="frozen Phase 1.5 protocol"):
        load_evaluation(heldout)


def test_reject_inconsistent_rates_and_pooled_counts(artifacts):
    heldout, lock, payload = artifacts
    altered_rate = deepcopy(payload)
    altered_rate["pooled"][POSTED_METRIC]["locked_candidate"]["date_precision"] = 0.99
    heldout.write_text(json.dumps(altered_rate))
    with pytest.raises(ValueError, match="underlying counts"):
        load_evaluation(heldout, lock)
    payload["pooled"][POSTED_METRIC]["locked_candidate"] = _method(3, 1)
    heldout.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="reconcile to the held-out seeds"):
        load_evaluation(heldout, lock)


def test_missing_artifact_returns_an_actionable_error(tmp_path):
    with pytest.raises(ValueError, match="Could not validate the Phase 1.5 evaluation artifacts"):
        load_evaluation(tmp_path / "missing.json")


def test_event_headlines_use_episode_and_eligible_event_denominators():
    posted = event_headlines(_method(3, 2))
    retro = event_headlines(_method(3, 1))
    assert posted == (
        ("Locked event precision", "33.33%", "3 of 9 alert episodes matched"),
        ("Locked event recall", "50.00%", "3 of 6 eligible events detected"),
    )
    assert retro[0] == ("Locked event precision", "50.00%", "3 of 6 alert episodes matched")
