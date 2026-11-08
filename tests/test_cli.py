import json

import duckdb
import pytest

from revenuescope.cli import main
from revenuescope.db import initialize_schema


def test_cli_reports_from_read_only_database_without_manifest(tmp_path):
    database = tmp_path / "fixture.duckdb"
    output = tmp_path / "report.json"
    with duckdb.connect(str(database)) as conn:
        initialize_schema(conn)
        conn.execute("""
            INSERT INTO dataset_meta VALUES ('2024-01-01', '2024-03-01', '2024-03-01', 1);
            INSERT INTO customers VALUES (1, '2023-12-01');
            INSERT INTO products VALUES (1, 'Widget', 'Home');
            INSERT INTO orders VALUES (1, 1, '2024-02-29 23:59:59', 'North', 'Web', 'completed');
            INSERT INTO order_lines VALUES (1, 1, 1, 2, 1001, 3, 400);
        """)
    assert main(["report", "--database", str(database), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["summary"]["net_revenue_cents"] == 1999
    assert report["summary"]["gross_profit_cents"] == 1199
    assert report["summary"]["orders"] == 1
    assert len(report["daily_anomaly_scores"]) == 60
    assert report["variance"]["bridge"]["volume_effect_cents"] == {"numerator": 1999, "denominator": 1}
    assert "anomalies" not in report and "manifest" not in report
    assert not (tmp_path / "verification_manifest.json").exists()


def test_generation_never_replaces_an_existing_directory(tmp_path, capsys):
    sentinel = tmp_path / "untouched.txt"
    sentinel.write_text("original", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["generate", "--output", str(tmp_path)])
    assert exc.value.code == 1
    assert "already exists" in capsys.readouterr().err
    assert sentinel.read_text(encoding="utf-8") == "original"
