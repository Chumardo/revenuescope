"""Streamlit entry point for the bundled, read-only synthetic portfolio demo."""

from pathlib import Path

from app import main


if __name__ == "__main__":
    assets = Path(__file__).resolve().parent / "demo_assets"
    main(database=assets / "revenuescope.duckdb", evidence_dir=assets)
