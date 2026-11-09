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


def test_release_includes_license_and_assets_and_rebuilds_byte_for_byte(tmp_path):
    archive = tmp_path / "release.zip"
    build_archive(ROOT, archive)
    verify_archive(archive)
    extracted = tmp_path / "clean"
    with ZipFile(archive) as package:
        package.extractall(extracted)
    assert (extracted / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    assert b"[MIT License](LICENSE)" in (extracted / "README.md").read_bytes()
    assets = extracted / "demo_assets"
    assert {path.name for path in assets.iterdir()} == {
        "anomaly-development.json", "anomaly-heldout.json", "anomaly-investigation.json",
        "anomaly-investigation-retrospective.json", "provenance.json", "revenuescope.duckdb",
    }
    for asset in assets.iterdir():
        assert asset.read_bytes() == (ROOT / "demo_assets" / asset.name).read_bytes()
    rebuilt = tmp_path / "rebuilt.zip"
    build_archive(extracted, rebuilt)
    assert rebuilt.read_bytes() == archive.read_bytes()


def test_release_manifest_detects_a_changed_listed_file(tmp_path):
    archive = tmp_path / "release.zip"
    build_archive(ROOT, archive)
    altered = tmp_path / "altered.zip"
    with ZipFile(archive) as package, ZipFile(altered, "w") as target:
        for name in package.namelist():
            content = package.read(name)
            target.writestr(name, content + b"\nChanged README" if name == "README.md" else content)
    with pytest.raises(ValueError, match="SHA-256 manifest"):
        verify_archive(altered)
