"""Build or verify the local public source archive from an explicit allowlist."""

import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "release-manifest.sha256"
ASSETS = (
    "anomaly-development.json", "anomaly-heldout.json", "anomaly-investigation.json",
    "anomaly-investigation-retrospective.json", "provenance.json", "revenuescope.duckdb",
)
MODULES = (
    "__init__", "__main__", "analytics", "anomaly", "anomaly_audit", "benchmark", "cli",
    "db", "generator", "investigation", "pit", "screening", "validation", "variance",
    "workspace_data", "workspace_detail", "workspace_evaluation", "workspace_ui",
)
SQL = (
    "customer_cohorts", "daily_revenue", "kpi_ratios", "kpi_sums", "line_facts", "monthly_kpis",
    "posted_activity", "summarize",
)
TESTS = (
    "analytics", "anomaly", "benchmark", "cli", "generator", "investigation", "pit", "release",
    "screening", "variance", "workspace_data", "workspace_detail", "workspace_evaluation", "workspace_ui",
)
FILES = tuple(sorted((
    ".gitignore", ".streamlit/config.toml", "README.md", "app.py", "demo_app.py", "pyproject.toml",
    "requirements.txt", "requirements-test.txt", "src/revenuescope/workspace.css",
    "docs/anomaly_method.md", "docs/architecture.md", "docs/case_study.md", "docs/deployment.md",
    "docs/security_review.md", "docs/upwork_portfolio.md",
    "scripts/build_public_assets.py", "scripts/build_release.py", "scripts/verify_workspace_browser.cjs",
    *(f"src/revenuescope/{name}.py" for name in MODULES),
    *(f"src/revenuescope/sql/{name}.sql" for name in SQL),
    *(f"tests/test_{name}.py" for name in TESTS),
    *(f"demo_assets/{name}" for name in ASSETS),
    *(f"docs/screenshots/release/{name}.png" for name in (
        "01-overview", "02-review-queue", "03-investigation", "04-evaluation",
    )),
)))


def _manifest(files: dict[str, bytes]) -> bytes:
    return "".join(f"{sha256(content).hexdigest()}  {name}\n" for name, content in sorted(files.items())).encode()


def verify_archive(path: Path) -> None:
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != {*FILES, MANIFEST}:
            raise ValueError("The archive contains missing, duplicate, or unapproved files.")
        actual = {name: archive.read(name) for name in FILES}
        if archive.read(MANIFEST) != _manifest(actual):
            raise ValueError("The archive contents differ from their SHA-256 manifest.")
    assets = json.loads(actual["demo_assets/provenance.json"])
    hashes = {name: sha256(actual[f"demo_assets/{name}"]).hexdigest() for name in ASSETS if name != "provenance.json"}
    if assets.get("synthetic") is not True or assets.get("files_sha256") != hashes:
        raise ValueError("The demo assets differ from their verified provenance.")


def build_archive(root: Path, output: Path) -> None:
    if output.exists():
        raise ValueError("Choose a new output archive. Existing files are never replaced.")
    root = root.resolve(strict=True)
    files = {}
    for name in FILES:
        relative = PurePosixPath(name)
        source = root / relative
        if any((root.joinpath(*relative.parts[:depth])).is_symlink() for depth in range(1, len(relative.parts) + 1)):
            raise ValueError(f"Unexpected symbolic link in release input: {name}")
        if not source.is_file() or not source.resolve().is_relative_to(root):
            raise ValueError(f"Required release input is missing or outside the project: {name}")
        files[name] = source.read_bytes()
    files[MANIFEST] = _manifest(files)
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, content, compress_type=ZIP_DEFLATED, compresslevel=9)
    verify_archive(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("archive", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("archive", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        build_archive(ROOT, args.archive)
    else:
        verify_archive(args.archive)
    print(f"Verified {len(FILES)} release inputs plus {MANIFEST}.")
    print(f"Archive SHA-256: {sha256(args.archive.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
