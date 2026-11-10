"""
Tests Paso 4 — DDL BigQuery (mocks; sin credenciales GCP).

CI debe pasar sin ADC ni project real.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "apply_ddl.py"
SQL_DIR = ROOT / "sql"


def _load_module():
    spec = importlib.util.spec_from_file_location("apply_ddl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["apply_ddl"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def apply_ddl():
    return _load_module()


def test_partition_constants_under_10k(apply_ddl):
    n = apply_ddl.partition_count(
        apply_ddl.FECHA_ID_START,
        apply_ddl.FECHA_ID_END_EXCLUSIVE,
        apply_ddl.FECHA_ID_INTERVAL,
    )
    assert apply_ddl.FECHA_ID_INTERVAL == 100
    # YYYYMMDD span 20250401→20261101 = 10700; /100 = 107 buckets.
    assert n == 107
    assert n <= 10_000
    # Daily interval=1 on YYYYMMDD integer space exceeds the 10k quota.
    daily = apply_ddl.partition_count(
        apply_ddl.FECHA_ID_START,
        apply_ddl.FECHA_ID_END_EXCLUSIVE,
        1,
    )
    assert daily == 10_700
    assert daily > 10_000


def test_bronze_columns_match_data_contract(apply_ddl):
    sql = (SQL_DIR / "ddl_bronze.sql").read_text(encoding="utf-8")
    parsed = apply_ddl.extract_bronze_columns_from_sql(sql)
    assert set(parsed) == set(apply_ddl.BRONZE_CONTRACT)
    for table, expected in apply_ddl.BRONZE_CONTRACT.items():
        assert parsed[table] == expected, f"mismatch in {table}"


def test_gold_facts_integer_range_and_clustering(apply_ddl):
    sql = (SQL_DIR / "ddl_gold.sql").read_text(encoding="utf-8")
    info = apply_ddl.gold_fact_partition_cluster(sql)
    assert set(info) == set(apply_ddl.GOLD_FACT_TABLES)
    for table, meta in info.items():
        assert meta["has_integer_range"] is True, table
        assert meta["generate_array"] == (
            apply_ddl.FECHA_ID_START,
            apply_ddl.FECHA_ID_END_EXCLUSIVE,
            apply_ddl.FECHA_ID_INTERVAL,
        ), table
        assert len(meta["cluster_by"]) >= 1, table
    assert info["f_movimientos_bodega"]["cluster_by"] == ["bodega_id", "sku_id"]
    assert info["f_stock_diario"]["cluster_by"] == ["bodega_id", "sku_id"]
    assert info["f_entregas"]["cluster_by"] == ["bodega_id", "transportista_id"]


def test_load_all_statements_counts(apply_ddl):
    specs = apply_ddl.load_all_statements(SQL_DIR)
    # 6 bronze + 6 silver + 4 dims + 3 facts = 19
    assert len(specs) == 19
    layers = {s.layer for s in specs}
    assert layers == {"bronze", "silver", "gold"}
    bronze = [s for s in specs if s.layer == "bronze"]
    assert {s.table_fqn.split(".")[-1] for s in bronze} == set(apply_ddl.BRONZE_CONTRACT)


def test_all_statements_are_create_if_not_exists(apply_ddl):
    for spec in apply_ddl.load_all_statements(SQL_DIR):
        assert "CREATE TABLE IF NOT EXISTS" in spec.sql.upper()
        assert "INSERT " not in spec.sql.upper()
        assert "UPDATE " not in spec.sql.upper()
        assert "DELETE " not in spec.sql.upper()
        assert "MERGE " not in spec.sql.upper()


def _not_found():
    from google.api_core.exceptions import NotFound

    return NotFound("not found")


def test_apply_statement_dry_run_would_create(apply_ddl):
    client = MagicMock()
    client.get_table.side_effect = _not_found()
    dry_job = MagicMock()
    dry_job.total_bytes_processed = 0
    client.query.return_value = dry_job

    spec = apply_ddl.StatementSpec(
        sql="CREATE TABLE IF NOT EXISTS `bronze_kora.md_bodega` (warehouse_id STRING NOT NULL)",
        table_fqn="bronze_kora.md_bodega",
        layer="bronze",
    )
    result = apply_ddl.apply_statement(
        client, project_id="kora-sports", spec=spec, dry_run=True
    )
    assert result.action == "would_create"
    client.query.assert_called_once()
    job_config = client.query.call_args.kwargs["job_config"]
    assert job_config.dry_run is True


def test_apply_statement_dry_run_would_skip(apply_ddl):
    client = MagicMock()
    client.get_table.return_value = MagicMock()
    spec = apply_ddl.StatementSpec(
        sql="CREATE TABLE IF NOT EXISTS `gold_kora.d_sku` (sku_id STRING NOT NULL)",
        table_fqn="gold_kora.d_sku",
        layer="gold",
    )
    result = apply_ddl.apply_statement(
        client, project_id="kora-sports", spec=spec, dry_run=True
    )
    assert result.action == "would_skip"
    client.query.assert_not_called()


def test_apply_statement_creates_then_exists(apply_ddl):
    """Idempotencia: primera crea, segunda exists."""
    client = MagicMock()
    client.get_table.side_effect = _not_found()
    job = MagicMock()
    client.query.return_value = job

    spec = apply_ddl.StatementSpec(
        sql="CREATE TABLE IF NOT EXISTS `silver_kora.md_sku` (sku_id STRING NOT NULL)",
        table_fqn="silver_kora.md_sku",
        layer="silver",
    )
    r1 = apply_ddl.apply_statement(
        client, project_id="kora-sports", spec=spec, dry_run=False
    )
    assert r1.action == "created"
    job.result.assert_called_once()

    client2 = MagicMock()
    client2.get_table.return_value = MagicMock()
    r2 = apply_ddl.apply_statement(
        client2, project_id="kora-sports", spec=spec, dry_run=False
    )
    assert r2.action == "exists"
    client2.query.assert_not_called()


def test_run_dry_run_idempotent_report(apply_ddl, capsys):
    client = MagicMock()
    # Alterna: primeras 3 missing, resto exist (simula partial)
    state = {"n": 0}

    def get_table(ref):
        state["n"] += 1
        if state["n"] <= 3:
            raise _not_found()
        return MagicMock()

    client.get_table.side_effect = get_table
    dry_job = MagicMock()
    dry_job.total_bytes_processed = 0
    client.query.return_value = dry_job

    rc = apply_ddl.run(dry_run=True, project_id="kora-sports", client=client)
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry_run=True" in out
    assert "would_create" in out
    assert "would_skip" in out
    assert client.query.call_count == 3


def test_run_apply_all_missing(apply_ddl, capsys):
    client = MagicMock()
    client.get_table.side_effect = _not_found()
    job = MagicMock()
    client.query.return_value = job

    rc = apply_ddl.run(dry_run=False, project_id="kora-sports", client=client)
    assert rc == 0
    assert client.query.call_count == 19
    out = capsys.readouterr().out
    assert "OK:" in out
    assert "created" in out


def test_load_project_id_from_env(apply_ddl, tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("GCP_PROJECT=demo-ddl\n", encoding="utf-8")
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
    assert apply_ddl.load_project_id(env_file) == "demo-ddl"


def test_parse_args_dry_run(apply_ddl):
    assert apply_ddl.parse_args(["--dry-run"]).dry_run is True
    assert apply_ddl.parse_args([]).dry_run is False


def test_silver_facts_also_integer_range(apply_ddl):
    sql = (SQL_DIR / "ddl_silver.sql").read_text(encoding="utf-8")
    for table in (
        "sap_wms_movimientos",
        "sap_wms_stock_snapshot",
        "sap_tm_entregas",
    ):
        assert "RANGE_BUCKET(fecha_id" in sql
        assert f"`silver_kora.{table}`" in sql
    assert "GENERATE_ARRAY(20250401, 20261101, 100)" in sql
    # Quarantine deferred to Paso 6 — no CREATE de tablas quarantine
    assert "CREATE TABLE" in sql.upper()
    assert not any(
        "quarantine" in spec.table_fqn.lower()
        for spec in apply_ddl.load_all_statements(SQL_DIR)
        if spec.layer == "silver"
    )
