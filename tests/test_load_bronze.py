"""
Tests Paso 5 — load bronze (mocks; sin credenciales GCP).

CI debe pasar sin ADC ni project real.
"""
from __future__ import annotations

import csv
import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "load_bronze.py"
MAKEFILE = ROOT / "Makefile"


def _load_module():
    spec = importlib.util.spec_from_file_location("load_bronze", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["load_bronze"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def load_bronze():
    return _load_module()


def _field(name: str):
    f = MagicMock()
    f.name = name
    return f


def test_csv_to_table_map(load_bronze, tmp_path: Path):
    for table_id in load_bronze.BRONZE_TABLES:
        path = load_bronze.csv_path_for(table_id, tmp_path)
        assert path == tmp_path / f"{table_id}.csv"
    assert set(load_bronze.BRONZE_TABLES) == set(load_bronze.EXPECTED_COUNTS)
    assert load_bronze.EXPECTED_COUNTS == {
        "md_bodega": 8,
        "md_sku": 250,
        "md_transportista": 12,
        "sap_wms_movimientos": 60_285,
        "sap_wms_stock_snapshot": 208_555,
        "sap_tm_entregas": 29_923,
    }


def test_validate_header_order_ok(load_bronze, tmp_path: Path):
    path = tmp_path / "md_bodega.csv"
    cols = ["warehouse_id", "warehouse_name", "region_code", "city", "is_active"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerow(["WH-01", "CD Centro", "RM", "Santiago", "True"])
        w.writerow(["WH-02", "CD Sur", "VIII", "Concepción", "True"])
    schema = [_field(c) for c in cols]
    header, rows = load_bronze.validate_csv_against_table(path, schema)
    assert header == cols
    assert rows == 2


def test_validate_header_mismatch(load_bronze, tmp_path: Path):
    path = tmp_path / "md_bodega.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["warehouse_name", "warehouse_id"])  # orden incorrecto
        w.writerow(["CD", "WH-01"])
    schema = [_field("warehouse_id"), _field("warehouse_name")]
    with pytest.raises(ValueError, match="Header mismatch"):
        load_bronze.validate_csv_against_table(path, schema)


def test_build_load_job_config_explicit_no_autodetect(load_bronze):
    from google.cloud import bigquery

    schema = [
        bigquery.SchemaField("warehouse_id", "STRING"),
        bigquery.SchemaField("warehouse_name", "STRING"),
    ]
    cfg = load_bronze.build_load_job_config(schema)
    assert cfg.write_disposition == bigquery.WriteDisposition.WRITE_TRUNCATE
    assert cfg.skip_leading_rows == 1
    assert cfg.autodetect is False
    assert cfg.source_format == bigquery.SourceFormat.CSV
    assert [f.name for f in cfg.schema] == ["warehouse_id", "warehouse_name"]


def test_verify_row_count_pass_fail(load_bronze):
    load_bronze.verify_row_count(csv_rows=8, bq_rows=8, table_id="md_bodega")
    with pytest.raises(ValueError, match="Row count mismatch"):
        load_bronze.verify_row_count(csv_rows=8, bq_rows=7, table_id="md_bodega")


def test_dry_run_no_writes(load_bronze, tmp_path: Path):
    path = tmp_path / "md_bodega.csv"
    cols = ["warehouse_id", "warehouse_name", "region_code", "city", "is_active"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for i in range(8):
            w.writerow([f"WH-{i:02d}", f"CD {i}", "RM", "Santiago", "True"])

    client = MagicMock()
    table = MagicMock()
    table.schema = [_field(c) for c in cols]
    table.num_rows = 0
    client.get_table.return_value = table

    result = load_bronze.load_one_table(
        client,
        project_id="kora-sports",
        table_id="md_bodega",
        raw_dir=tmp_path,
        dry_run=True,
    )
    assert result.action == "would_load"
    assert result.csv_rows == 8
    client.load_table_from_file.assert_not_called()
    # get_table once for schema; no refresh load path
    assert client.get_table.call_count == 1


def test_missing_csv_clear_error(load_bronze, tmp_path: Path):
    client = MagicMock()
    result = load_bronze.load_one_table(
        client,
        project_id="kora-sports",
        table_id="md_bodega",
        raw_dir=tmp_path,
        dry_run=True,
    )
    assert result.action == "error"
    assert "CSV missing" in result.detail
    client.get_table.assert_not_called()
    client.load_table_from_file.assert_not_called()


def test_load_calls_load_table_from_file_and_verifies(load_bronze, tmp_path: Path):
    path = tmp_path / "md_sku.csv"
    cols = ["sku_id", "sku_name", "category", "brand", "unit_cost_clp"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerow(["SKU-00001", "Zapatilla", "CALZADO", "KORA", "19990"])

    client = MagicMock()
    table_before = MagicMock()
    table_before.schema = [_field(c) for c in cols]
    table_after = MagicMock()
    table_after.num_rows = 1
    client.get_table.side_effect = [table_before, table_after]

    job = MagicMock()
    client.load_table_from_file.return_value = job

    # Real file open for validate + binary open for load — use real path.
    result = load_bronze.load_one_table(
        client,
        project_id="kora-sports",
        table_id="md_sku",
        raw_dir=tmp_path,
        dry_run=False,
    )
    assert result.action == "loaded"
    assert result.csv_rows == 1
    assert result.bq_rows == 1
    client.load_table_from_file.assert_called_once()
    job.result.assert_called_once()
    kwargs = client.load_table_from_file.call_args.kwargs
    assert kwargs["location"] == load_bronze.BQ_LOCATION
    cfg = kwargs["job_config"]
    from google.cloud import bigquery

    assert cfg.write_disposition == bigquery.WriteDisposition.WRITE_TRUNCATE
    assert cfg.autodetect is False
    assert cfg.skip_leading_rows == 1


def test_load_mismatch_exits_failure(load_bronze, tmp_path: Path):
    path = tmp_path / "md_transportista.csv"
    cols = ["carrier_id", "carrier_name", "service_level"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerow(["CAR-01", "3PL A", "STD"])

    client = MagicMock()
    table_before = MagicMock()
    table_before.schema = [_field(c) for c in cols]
    table_after = MagicMock()
    table_after.num_rows = 99  # mismatch
    client.get_table.side_effect = [table_before, table_after]
    client.load_table_from_file.return_value = MagicMock()

    result = load_bronze.load_one_table(
        client,
        project_id="kora-sports",
        table_id="md_transportista",
        raw_dir=tmp_path,
        dry_run=False,
    )
    assert result.action == "mismatch"
    assert "Row count mismatch" in result.detail


def test_run_dry_run_propagates_missing(load_bronze, tmp_path: Path):
    client = MagicMock()
    code = load_bronze.run(
        dry_run=True,
        project_id="kora-sports",
        client=client,
        raw_dir=tmp_path,
        tables=("md_bodega",),
    )
    assert code == 1


def test_makefile_n_run_includes_new_stages():
    """`make -n run` debe planificar generate + load + transform_silver (CI)."""
    proc = subprocess.run(
        ["make", "-n", "run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    # make -n deja `$script` sin expandir; STAGES aparece en el `for`.
    assert "scripts/generate_synthetic.py" in out
    assert "scripts/load_bronze.py" in out
    assert "scripts/transform_silver.py" in out
    assert "$script --dry-run" in out or "--dry-run" in out
    # Sigue incluyendo etapas previas.
    assert "scripts/setup_bigquery.py" in out
    assert "scripts/apply_ddl.py" in out
    # Orden del pipeline en la lista STAGES del for.
    idx_setup = out.index("scripts/setup_bigquery.py")
    idx_ddl = out.index("scripts/apply_ddl.py")
    idx_gen = out.index("scripts/generate_synthetic.py")
    idx_load = out.index("scripts/load_bronze.py")
    idx_silver = out.index("scripts/transform_silver.py")
    assert idx_setup < idx_ddl < idx_gen < idx_load < idx_silver


def test_makefile_stages_comment_justifies_always_generate():
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "scripts/generate_synthetic.py" in text
    assert "scripts/load_bronze.py" in text
    assert "scripts/transform_silver.py" in text
    assert "SIEMPRE" in text or "always" in text.lower()
    assert ".gitignore" in text or "gitignored" in text.lower() or "gitignore" in text
