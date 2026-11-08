import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from revenuescope.pit import POSTED_METRIC
from revenuescope.workspace_evaluation import load_evaluation
from scripts.build_release import build_archive, verify_archive

ROOT = Path(__file__).resolve().parents[1]


def test_demo_entrypoint_uses_only_bundled_paths(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "app", SimpleNamespace(main=lambda **kwargs: calls.append(kwargs)))
    runpy.run_path(str(ROOT / "demo_app.py"), run_name="__main__")
    assert calls == [{"database": ROOT / "demo_assets/revenuescope.duckdb", "evidence_dir": ROOT / "demo_assets"}]


def test_public_evidence_preserves_results_without_event_labels():
    assets = ROOT / "demo_assets"
    heldout = assets / "anomaly-heldout.json"
    development = assets / "anomaly-development.json"
    report = load_evaluation(heldout, development)
    locked = report["pooled"][POSTED_METRIC]["locked_candidate"]
    assert locked["event_precision"] == 7 / 88
    assert locked["event_recall"] == 7 / 15
    for path in (heldout, development):
        payload = path.read_text(encoding="utf-8")
        assert all(f'"{key}"' not in payload for key in ("events", "matching_dates", "unmatched_dates", "segment_probe"))
    for path in assets.glob("anomaly-investigation*.json"):
        assert set(json.loads(path.read_text(encoding="utf-8"))) == {
            "synthetic", "currency", "metric", "policy", "coverage", "alerts",
        }


def test_release_manifest_detects_unapproved_additions(tmp_path):
    archive = tmp_path / "release.zip"
    build_archive(ROOT, archive)
    with ZipFile(archive, "a") as package:
        package.writestr("output/developer-notes.json", "{}")
    with pytest.raises(ValueError, match="unapproved files"):
        verify_archive(archive)
