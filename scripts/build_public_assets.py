"""Project verified Phase 1.5 evidence into a self-contained synthetic demo bundle."""

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from revenuescope.benchmark import DEV_SEEDS, HOLDOUT_SEEDS, PROTOCOL
from revenuescope.db import open_dataset
from revenuescope.screening import PORTFOLIO_POLICY, PORTFOLIO_POLICY_NAME
from revenuescope.validation import snapshot_inventory
from revenuescope.workspace_data import INVESTIGATION_FILES, load_workspace
from revenuescope.workspace_evaluation import load_evaluation

DATABASE_NAME = "revenuescope.duckdb"
DEVELOPMENT_NAME = "anomaly-development.json"
HELDOUT_NAME = "anomaly-heldout.json"
EXPECTED_SNAPSHOT = "099423e888cfe94ebaf88054cabbaca293e214e6bbab01f91feea6ef79edf17b"
EXPECTED_DEVELOPMENT = "a6c40440078d8665defa94baa6425bf143e3c8e8e125a7513f3dbc51b6ab3d52"
EXPECTED_HELDOUT = "bda11b4193fc7282b78ec07cfce8388b4256840fe67c8399c225832692addd39"


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: dict) -> None:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    path.write_bytes((payload + "\n").encode("utf-8"))


def write_public_benchmark(verified: dict, destination: Path) -> None:
    """Serialize only the validated view, without dates, event IDs, or labels."""
    policy = asdict(PORTFOLIO_POLICY)
    development = destination / DEVELOPMENT_NAME
    _write(development, {"protocol": PROTOCOL, "seeds": DEV_SEEDS,
                         "selected": PORTFOLIO_POLICY_NAME, "locked_policy": policy})
    _write(destination / HELDOUT_NAME, {
        "protocol": PROTOCOL,
        "seeds": HOLDOUT_SEEDS,
        "locked_candidate": PORTFOLIO_POLICY_NAME,
        "locked_policy": policy,
        "development_sha256": digest(development),
        "pooled": verified["pooled"],
        "per_seed": verified["by_seed"],
    })


def build_assets(database: Path, evidence: Path, destination: Path) -> None:
    if destination.exists():
        raise ValueError("Choose a fresh asset directory. Existing files are never replaced.")
    original_development = evidence / DEVELOPMENT_NAME
    original_heldout = evidence / HELDOUT_NAME
    if digest(original_development) != EXPECTED_DEVELOPMENT or digest(original_heldout) != EXPECTED_HELDOUT:
        raise ValueError("The developer evidence differs from the frozen Phase 1.5 inputs.")
    benchmark = load_evaluation(original_heldout, original_development)

    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent, prefix=".revenuescope-assets-") as scratch:
        staged = Path(scratch) / "assets"
        staged.mkdir()
        bundled_db = staged / DATABASE_NAME
        copyfile(database, bundled_db)
        with open_dataset(bundled_db) as conn:
            inventory = snapshot_inventory(conn)
        if inventory["logical_sha256"] != EXPECTED_SNAPSHOT:
            raise ValueError("The bundled database differs from the verified synthetic snapshot.")

        for metric, filename in INVESTIGATION_FILES.items():
            workspace = load_workspace(bundled_db, evidence, metric)
            _write(staged / filename, {
                "synthetic": True,
                "currency": "USD",
                "metric": metric,
                "policy": PORTFOLIO_POLICY_NAME,
                "coverage": {str(key): int(value) for key, value in workspace.daily.status.value_counts().items()},
                "alerts": list(workspace.alerts.values()),
            })
            # Exercise the same validation that runs on an actual public page load.
            load_workspace(bundled_db, staged, metric)
        write_public_benchmark(benchmark, staged)
        if load_evaluation(staged / HELDOUT_NAME, staged / DEVELOPMENT_NAME) != benchmark:
            raise ValueError("Public evidence did not preserve the original evaluation values.")
        _write(staged / "provenance.json", {
            "synthetic": True,
            "currency": "USD",
            "seed": 202501,
            "snapshot": inventory,
            "developer_development_sha256": EXPECTED_DEVELOPMENT,
            "developer_heldout_sha256": EXPECTED_HELDOUT,
            "files_sha256": {path.name: digest(path) for path in sorted(staged.iterdir())},
        })
        staged.rename(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/demo/revenuescope.duckdb"))
    parser.add_argument("--evidence", type=Path, default=Path("output"))
    parser.add_argument("--destination", type=Path, default=Path("demo_assets"))
    args = parser.parse_args()
    build_assets(args.database, args.evidence, args.destination)
    print(f"Verified public assets written to {args.destination}")


if __name__ == "__main__":
    main()
