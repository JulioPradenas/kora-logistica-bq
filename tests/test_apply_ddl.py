"""
Tests Paso 4 — DDL BigQuery (mocks; sin credenciales GCP).

CI debe pasar sin ADC ni project real.
"""
from __future__ import annotations

import importlib.util
import re
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


def test_bronze_ddl_landing_has_no_not_null(apply_ddl):
    """
    Regresión Paso 6: bronze debe ser landing NULLABLE.
    Si alguien reintroduce NOT NULL, load de anomalías null falla
    ('Required column value is missing').
    """
    sql = (SQL_DIR / "ddl_bronze.sql").read_text(encoding="utf-8")
    assert apply_ddl.bronze_ddl_has_not_null(sql) is False
    # Heurística directa sobre cuerpo sin comentarios.
    body = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
    assert not re.search(r"\bNOT\s+NULL\b", body, flags=re.IGNORECASE)


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
    # 6 bronze + 7 silver (incl. quarantine_rows) + 4 dims + 3 facts = 20
    assert len(specs) == 20
    layers = {s.layer for s in specs}
    assert layers == {"bronze", "silver", "gold"}
    bronze = [s for s in specs if s.layer == "bronze"]
    assert {s.table_fqn.split(".")[-1] for s in bronze} == set(apply_ddl.BRONZE_CONTRACT)
    silver_tables = {s.table_fqn.split(".")[-1] for s in specs if s.layer == "silver"}
    assert "quarantine_rows" in silver_tables


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
    # Alterna: primeras 3 missing, resto exist con schema completo (sin ADD).
    state = {"n": 0}
    gold_cols = apply_ddl.extract_gold_columns_from_sql()

    def _full_schema(table_ref: str):
        name = str(table_ref).split(".")[-1]
        cols = gold_cols.get(name) or []
        fields = []
        for col, _typ in cols:
            f = MagicMock()
            f.name = col
            f.mode = "NULLABLE"
            fields.append(f)
        # bronze/silver existing: empty schema → relax may ALTER; give no REQUIRED
        table = MagicMock()
        table.schema = fields
        return table

    def get_table(ref):
        state["n"] += 1
        if state["n"] <= 3:
            raise _not_found()
        return _full_schema(ref)

    client.get_table.side_effect = get_table
    dry_job = MagicMock()
    dry_job.total_bytes_processed = 0
    client.query.return_value = dry_job

    rc = apply_ddl.run(dry_run=True, project_id="kora-sports", client=client)
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry_run=True" in out
    assert "would_create" in out
    assert "would_skip" in out or "schema_ok" in out
    # Solo los 3 CREATE dry-run (tablas existentes no disparan ADD si schema completo).
    assert client.query.call_count == 3
    assert "sync gold schema" in out


def test_run_apply_all_missing(apply_ddl, capsys):
    client = MagicMock()
    client.get_table.side_effect = _not_found()
    job = MagicMock()
    client.query.return_value = job

    rc = apply_ddl.run(dry_run=False, project_id="kora-sports", client=client)
    assert rc == 0
    # 20 CREATE; relax ve tablas missing → sin ALTER queries extra
    assert client.query.call_count == 20
    out = capsys.readouterr().out
    assert "OK:" in out
    assert "created" in out
    assert "relax bronze nullability" in out


def test_relax_bronze_nullability_alters_required(apply_ddl):
    """Tablas ya creadas con REQUIRED → ALTER DROP NOT NULL (idempotente)."""
    client = MagicMock()

    def field(name: str, mode: str = "REQUIRED"):
        f = MagicMock()
        f.name = name
        f.mode = mode
        return f

    table = MagicMock()
    table.schema = [
        field("event_date", "REQUIRED"),
        field("warehouse_id", "REQUIRED"),
        field("qty_ordered", "NULLABLE"),
    ]
    client.get_table.return_value = table
    job = MagicMock()
    client.query.return_value = job

    results = apply_ddl.relax_bronze_nullability(
        client,
        project_id="kora-sports",
        dry_run=False,
        tables=("sap_wms_movimientos",),
    )
    assert len(results) == 1
    assert results[0].action == "relaxed"
    assert "event_date" in results[0].detail
    assert "warehouse_id" in results[0].detail
    assert client.query.call_count == 2
    sqls = [c.args[0] for c in client.query.call_args_list]
    assert any("DROP NOT NULL" in s and "event_date" in s for s in sqls)
    assert any("DROP NOT NULL" in s and "warehouse_id" in s for s in sqls)


def test_relax_bronze_already_nullable_no_alter(apply_ddl):
    client = MagicMock()
    f = MagicMock()
    f.name = "event_date"
    f.mode = "NULLABLE"
    table = MagicMock()
    table.schema = [f]
    client.get_table.return_value = table

    results = apply_ddl.relax_bronze_nullability(
        client,
        project_id="kora-sports",
        dry_run=False,
        tables=("sap_wms_movimientos",),
    )
    assert results[0].action == "nullable_ok"
    client.query.assert_not_called()


def test_sync_gold_schema_adds_missing_on_time_in_full(apply_ddl):
    """
    Regresión Julio make run: f_entregas Paso 4 sin on_time/in_full →
    apply_ddl debe emitir ADD COLUMN IF NOT EXISTS (DDL = contrato).
    """
    client = MagicMock()

    def field(name: str):
        f = MagicMock()
        f.name = name
        f.mode = "NULLABLE"
        return f

    # Schema legacy Paso 4 (sin flags OTIF).
    legacy = [
        "fecha_id",
        "shipment_id",
        "order_id",
        "bodega_id",
        "transportista_id",
        "comuna_id",
        "comuna_name",
        "ship_fecha_id",
        "eta_ts",
        "ata_ts",
        "status",
        "qty_delivered",
    ]
    table = MagicMock()
    table.schema = [field(c) for c in legacy]
    client.get_table.return_value = table
    job = MagicMock()
    client.query.return_value = job

    results = apply_ddl.sync_gold_schema(
        client,
        project_id="kora-sports",
        dry_run=False,
        tables=("f_entregas",),
    )
    assert len(results) == 1
    assert results[0].action == "added_columns"
    assert "on_time" in results[0].detail
    assert "in_full" in results[0].detail
    sqls = [c.args[0] for c in client.query.call_args_list]
    assert any("ADD COLUMN IF NOT EXISTS" in s and "on_time" in s for s in sqls)
    assert any("ADD COLUMN IF NOT EXISTS" in s and "in_full" in s for s in sqls)


def test_sync_gold_schema_ok_when_complete(apply_ddl):
    client = MagicMock()
    cols = apply_ddl.extract_gold_columns_from_sql()["f_entregas"]
    fields = []
    for name, _typ in cols:
        f = MagicMock()
        f.name = name
        f.mode = "NULLABLE"
        fields.append(f)
    table = MagicMock()
    table.schema = fields
    client.get_table.return_value = table

    results = apply_ddl.sync_gold_schema(
        client,
        project_id="kora-sports",
        dry_run=False,
        tables=("f_entregas",),
    )
    assert results[0].action == "schema_ok"
    client.query.assert_not_called()


def test_gold_ddl_includes_otif_flags(apply_ddl):
    cols = {
        name: typ
        for name, typ in apply_ddl.extract_gold_columns_from_sql()["f_entregas"]
    }
    assert cols["on_time"] == "BOOL"
    assert cols["in_full"] == "BOOL"


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
    # Paso 6: quarantine_rows en DDL silver (shape vacío; CTAS la llena).
    assert "CREATE TABLE" in sql.upper()
    assert any(
        spec.table_fqn.endswith("quarantine_rows")
        for spec in apply_ddl.load_all_statements(SQL_DIR)
        if spec.layer == "silver"
    )
