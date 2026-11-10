"""
Tests Paso 8 — demo partition/cluster prune (mocks; sin credenciales GCP).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "demo_partition_prune.py"
SQL_FILE = ROOT / "sql" / "demo_partition_prune.sql"
MAKEFILE = ROOT / "Makefile"


def _load_module():
    spec = importlib.util.spec_from_file_location("demo_partition_prune", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["demo_partition_prune"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def demo():
    return _load_module()


def test_demo_sql_has_three_labeled_queries():
    text = SQL_FILE.read_text(encoding="utf-8")
    assert "QUERY_A" in text
    assert "QUERY_B" in text
    assert "QUERY_C" in text
    assert "gold_kora.f_stock_diario" in text
    assert "bodega_id" in text
    # (a) sin filtro fecha; (b)/(c) con rango mensual
    assert text.count("FROM `gold_kora.f_stock_diario`") == 3


def test_parse_demo_queries(demo):
    queries = demo.parse_demo_queries()
    assert [q.label for q in queries] == ["a", "b", "c"]
    assert "fecha_id" not in queries[0].sql or "WHERE" not in queries[0].sql.upper()
    assert "fecha_id >=" in queries[1].sql or "fecha_id>=" in queries[1].sql.replace(
        " ", ""
    )
    assert "bodega_id" in queries[2].sql
    for q in queries:
        assert "INSERT " not in q.sql.upper()
        assert "MERGE " not in q.sql.upper()


def test_pct_and_format_helpers(demo):
    assert demo.pct_of_baseline(50, 100) == "50.0%"
    assert demo.pct_of_baseline(0, 0) == "n/a"
    assert "MiB" in demo.format_bytes(5 * 1024 * 1024)


def test_makefile_includes_demo_stage():
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "scripts/demo_partition_prune.py" in text
    # Debe ir después de build_gold.
    assert text.index("scripts/build_gold.py") < text.index(
        "scripts/demo_partition_prune.py"
    )


def test_run_dry_run_prints_bytes_vs_baseline(demo, monkeypatch, capsys):
    queries = demo.parse_demo_queries()
    assert len(queries) == 3

    bytes_map = {
        queries[0].sql: 10_000_000,
        queries[1].sql: 1_000_000,
        queries[2].sql: 1_000_000,
    }

    class _Job:
        def __init__(self, n):
            self.total_bytes_processed = n

        def result(self):
            return []

    client = MagicMock()

    def fake_query(sql, job_config=None, location=None):
        # dry_run path
        n = bytes_map.get(sql)
        if n is None:
            # unpartitioned check SQL
            return _Job(0)
        return _Job(n)

    client.query.side_effect = fake_query
    client.project = "kora-sports"

    # Evitar ADC / .env
    monkeypatch.setattr(demo, "load_project_id", lambda: "kora-sports")
    monkeypatch.setattr(demo, "build_client", lambda project_id: client)

    # check_no_unpartitioned_rows dry_run → (0, [])
    apply_mod = MagicMock()
    apply_mod.check_no_unpartitioned_rows.return_value = (0, [])
    monkeypatch.setattr(demo, "_load_apply_ddl", lambda: apply_mod)

    rc = demo.run(dry_run=True, client=client, project_id="kora-sports")
    assert rc == 0
    out = capsys.readouterr().out
    assert "totalBytesProcessed=10000000" in out
    assert "totalBytesProcessed=1000000" in out
    assert "% vs (a)" in out or "of (a)" in out
    assert "10.0%" in out
    assert "clustering" in out.lower() or "dry_run" in out.lower()


def test_run_fails_on_unpartitioned_rows(demo, monkeypatch, capsys):
    client = MagicMock()

    class _Job:
        total_bytes_processed = 100

        def result(self):
            return []

    client.query.return_value = _Job()
    client.project = "kora-sports"
    monkeypatch.setattr(demo, "load_project_id", lambda: "kora-sports")
    monkeypatch.setattr(demo, "dry_run_bytes", lambda client, sql: 100)

    apply_mod = MagicMock()
    apply_mod.check_no_unpartitioned_rows.return_value = (
        1,
        [
            {
                "table_schema": "gold_kora",
                "table_name": "f_entregas",
                "partition_id": "__UNPARTITIONED__",
                "total_rows": 79,
            }
        ],
    )
    monkeypatch.setattr(demo, "_load_apply_ddl", lambda: apply_mod)

    rc = demo.run(dry_run=False, client=client, project_id="kora-sports")
    assert rc == 1
    out = capsys.readouterr().out
    assert "__UNPARTITIONED__" in out
