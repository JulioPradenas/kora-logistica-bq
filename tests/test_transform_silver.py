"""
Tests Paso 6 — transform silver + quarantine (mocks; sin credenciales GCP).

CI debe pasar sin ADC ni project real.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "transform_silver.py"
SQL_DIR = ROOT / "sql" / "silver"
MAKEFILE = ROOT / "Makefile"
GEN_SCRIPT = ROOT / "scripts" / "generate_synthetic.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def transform_silver():
    return _load_module(SCRIPT, "transform_silver")


@pytest.fixture(scope="module")
def generate_synthetic():
    return _load_module(GEN_SCRIPT, "generate_synthetic_for_silver")


def test_transform_files_order_masters_before_facts(transform_silver):
    names = transform_silver.TRANSFORM_FILES
    assert names[0].startswith("01_md_")
    assert names[1].startswith("02_md_")
    assert names[2].startswith("03_md_")
    assert "04_sap_wms_movimientos.sql" in names
    assert names[-1] == "07_quarantine_rows.sql"
    idx_master = names.index("03_md_transportista.sql")
    idx_mov = names.index("04_sap_wms_movimientos.sql")
    idx_q = names.index("07_quarantine_rows.sql")
    assert idx_master < idx_mov < idx_q


def test_sql_files_exist_and_ctas_redeclarations(transform_silver):
    for name in transform_silver.TRANSFORM_FILES:
        path = SQL_DIR / name
        assert path.is_file(), name
        text = path.read_text(encoding="utf-8")
        assert "CREATE OR REPLACE TABLE" in text.upper()
        assert "INSERT " not in text.upper()
        assert "MERGE " not in text.upper()
        assert "DELETE " not in text.upper()
        table = name.split("_", 1)[1].replace(".sql", "")
        # 01_md_bodega → md_bodega; 07_quarantine_rows → quarantine_rows
        if name.startswith("0") and "sap_" in name:
            table = name[3:].replace(".sql", "")
            errs = transform_silver.assert_ctas_redeclarations(text, table)
            assert errs == [], errs


def test_quality_rules_snippets_in_rendered_sql(transform_silver):
    snippets = transform_silver.quality_rules_sql_snippets()
    mov = (SQL_DIR / "04_sap_wms_movimientos.sql").read_text(encoding="utf-8")
    ent = (SQL_DIR / "06_sap_tm_entregas.sql").read_text(encoding="utf-8")
    stk = (SQL_DIR / "05_sap_wms_stock_snapshot.sql").read_text(encoding="utf-8")
    assert snippets["dedup_row_number"] in mov
    assert snippets["movement_type_canonical"] in mov
    assert snippets["fk_join_bodega"] in mov
    assert snippets["fk_join_sku"] in mov
    assert snippets["status_canonical"] in ent
    assert snippets["fk_join_carrier"] in ent
    assert snippets["usable_qty"] in stk
    assert "PARTITION BY RANGE_BUCKET" in mov
    assert "CLUSTER BY" in mov


def test_canonical_status_normalize_not_drop(transform_silver):
    assert transform_silver.canonical_status("Delivered") == "delivered"
    assert transform_silver.canonical_status(" DELIVERED") == "delivered"
    assert transform_silver.canonical_status("LATE_DELIVERED") == "late_delivered"
    assert transform_silver.canonical_status(" Late_Delivered ") == "late_delivered"
    assert transform_silver.canonical_status("FAILED") == "failed"
    assert transform_silver.canonical_status("IN_TRANSIT") == "in_transit"
    assert transform_silver.canonical_status("In_Transit ") == "in_transit"
    assert transform_silver.canonical_status("weird_status") is None
    assert transform_silver.canonical_status("") is None
    assert transform_silver.canonical_status(None) is None


def test_reconciliation_equation_pass_fail(transform_silver):
    assert transform_silver.reconciliation_equation_holds(
        bronze=100, silver=80, quarantine=10, eliminated_dupes=10
    )
    assert not transform_silver.reconciliation_equation_holds(
        bronze=100, silver=80, quarantine=10, eliminated_dupes=5
    )
    row_ok = transform_silver.ReconcileRow(
        entity="sap_wms_movimientos",
        bronze=60_285,
        silver=60_120,
        quarantine=75,
        eliminated_dupes=90,
    )
    assert row_ok.ok
    row_bad = transform_silver.ReconcileRow(
        entity="x", bronze=10, silver=5, quarantine=1, eliminated_dupes=1
    )
    assert not row_bad.ok


def test_expected_counts_match_anomaly_budget(transform_silver, generate_synthetic):
    c = generate_synthetic.ANOMALY_COUNTS
    assert transform_silver.EXPECTED_BRONZE_COUNTS["sap_wms_movimientos"] == 60_120 + (
        c["exact_dupes_movimientos"]
        + c["key_dupes_movimientos"]
        + c["missing_fk_warehouse_movimientos"]
        + c["missing_fk_sku_movimientos"]
        + c["null_required_movimientos"]
    )
    assert transform_silver.EXPECTED_SILVER_COUNTS["sap_wms_movimientos"] == 60_120
    assert transform_silver.EXPECTED_ELIMINATED_DUPES["sap_wms_movimientos"] == (
        c["exact_dupes_movimientos"] + c["key_dupes_movimientos"]
    )
    # quarantine mov = missing FK + nulls
    q_mov = (
        c["missing_fk_warehouse_movimientos"]
        + c["missing_fk_sku_movimientos"]
        + c["null_required_movimientos"]
    )
    assert (
        transform_silver.EXPECTED_BRONZE_COUNTS["sap_wms_movimientos"]
        == transform_silver.EXPECTED_SILVER_COUNTS["sap_wms_movimientos"]
        + q_mov
        + transform_silver.EXPECTED_ELIMINATED_DUPES["sap_wms_movimientos"]
    )


def test_dry_run_no_writes(transform_silver, tmp_path: Path):
    # Copy minimal SQL set into tmp to avoid depending on network.
    sql_dir = tmp_path / "silver"
    sql_dir.mkdir()
    for name in transform_silver.TRANSFORM_FILES:
        (sql_dir / name).write_text(
            f"CREATE OR REPLACE TABLE `silver_kora.t_{name}` AS SELECT 1 AS x;",
            encoding="utf-8",
        )

    client = MagicMock()
    job = MagicMock()
    job.total_bytes_processed = 0
    client.query.return_value = job

    code = transform_silver.run(
        dry_run=True,
        project_id="kora-sports",
        client=client,
        sql_dir=sql_dir,
    )
    assert code == 0
    assert client.query.call_count == len(transform_silver.TRANSFORM_FILES)
    for call in client.query.call_args_list:
        cfg = call.kwargs["job_config"]
        assert cfg.dry_run is True
    # dry-run path never calls result()
    job.result.assert_not_called()


def test_run_reconcile_mismatch_exits_1(transform_silver, tmp_path: Path):
    sql_dir = tmp_path / "silver"
    sql_dir.mkdir()
    for name in transform_silver.TRANSFORM_FILES:
        (sql_dir / name).write_text(
            "CREATE OR REPLACE TABLE `silver_kora.x` AS SELECT 1 AS x;",
            encoding="utf-8",
        )

    client = MagicMock()

    def fake_query(sql, job_config=None, location=None):
        job = MagicMock()
        job.total_bytes_processed = 0
        # Apply path: result() used; scalar_count reads rows[0]["n"]
        if job_config is not None and getattr(job_config, "dry_run", False):
            return job
        # COUNT queries during reconcile — return mismatched numbers
        if "quarantine_rows" in sql and "GROUP BY" in sql:
            job.result.return_value = []
            return job
        if "QUALIFY" in sql:
            job.result.return_value = [{"n": 0}]
            return job
        if "source_table" in sql:
            job.result.return_value = [{"n": 0}]
            return job
        if "bronze_kora" in sql:
            job.result.return_value = [{"n": 100}]
            return job
        if "silver_kora" in sql:
            job.result.return_value = [{"n": 50}]
            return job
        job.result.return_value = []
        return job

    client.query.side_effect = fake_query

    # Patch QueryJobConfig so dry_run flag works on MagicMock path for apply
    with patch.object(transform_silver, "run_query", wraps=None) as _rq:
        # Simpler: monkeypatch apply to succeed then reconcile to fail
        pass

    # Direct unit: apply succeeds via dry_run=False with mocked run_query
    with patch.object(transform_silver, "apply_transform_file") as apply_mock:
        apply_mock.side_effect = lambda client, path, dry_run=False: transform_silver.TransformResult(
            file_name=path.name, action="applied", detail="ok"
        )
        with patch.object(transform_silver, "reconcile_entity") as rec_mock:
            rec_mock.return_value = transform_silver.ReconcileRow(
                entity="md_bodega",
                bronze=8,
                silver=7,
                quarantine=0,
                eliminated_dupes=0,
            )
            with patch.object(transform_silver, "print_quarantine_summary", return_value={}):
                code = transform_silver.run(
                    dry_run=False,
                    project_id="kora-sports",
                    client=client,
                    sql_dir=sql_dir,
                )
    assert code == 1


def test_run_reconcile_pass(transform_silver, tmp_path: Path):
    sql_dir = tmp_path / "silver"
    sql_dir.mkdir()
    for name in transform_silver.TRANSFORM_FILES:
        (sql_dir / name).write_text(
            "CREATE OR REPLACE TABLE `silver_kora.x` AS SELECT 1 AS x;",
            encoding="utf-8",
        )

    client = MagicMock()
    with patch.object(transform_silver, "apply_transform_file") as apply_mock:
        apply_mock.side_effect = lambda client, path, dry_run=False: transform_silver.TransformResult(
            file_name=path.name, action="applied", detail="ok"
        )
        with patch.object(transform_silver, "reconcile_entity") as rec_mock:
            rec_mock.side_effect = [
                transform_silver.ReconcileRow(e, b, s, q, d)
                for e, b, s, q, d in [
                    ("md_bodega", 8, 8, 0, 0),
                    ("md_sku", 250, 250, 0, 0),
                    ("md_transportista", 12, 12, 0, 0),
                    ("sap_wms_movimientos", 60285, 60120, 75, 90),
                    ("sap_wms_stock_snapshot", 208555, 208440, 35, 80),
                    ("sap_tm_entregas", 29923, 29803, 50, 70),
                ]
            ]
            with patch.object(
                transform_silver,
                "print_quarantine_summary",
                return_value={"missing_fk_sku": 50},
            ):
                code = transform_silver.run(
                    dry_run=False,
                    project_id="kora-sports",
                    client=client,
                    sql_dir=sql_dir,
                )
    assert code == 0


def test_makefile_n_run_includes_transform_silver():
    proc = subprocess.run(
        ["make", "-n", "run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "scripts/transform_silver.py" in out
    assert "scripts/load_bronze.py" in out
    idx_load = out.index("scripts/load_bronze.py")
    idx_silver = out.index("scripts/transform_silver.py")
    assert idx_load < idx_silver


def test_generator_status_variants_covered(generate_synthetic, transform_silver):
    for variant in generate_synthetic.STATUS_CASING_VARIANTS:
        assert transform_silver.canonical_status(variant) is not None, variant
