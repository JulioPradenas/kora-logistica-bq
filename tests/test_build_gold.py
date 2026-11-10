"""
Tests Paso 7 — build gold datamart (mocks; sin credenciales GCP).

CI debe pasar sin ADC ni project real.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_gold.py"
SQL_DIR = ROOT / "sql" / "gold"
DDL_GOLD = ROOT / "sql" / "ddl_gold.sql"
MAKEFILE = ROOT / "Makefile"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def build_gold():
    return _load_module(SCRIPT, "build_gold")


def test_build_files_order_dims_facts_views(build_gold):
    names = build_gold.BUILD_FILES
    assert names[0] == "01_d_fecha.sql"
    assert names[1].startswith("02_d_")
    assert "05_f_movimientos_bodega.sql" in names
    assert "10_v_stock_salud.sql" in names
    assert names[-1] == "13_v_entregas_drill.sql"
    idx_dim = names.index("04_d_transportista.sql")
    idx_fact = names.index("05_f_movimientos_bodega.sql")
    idx_view = names.index("08_v_otif_diario.sql")
    idx_looker = names.index("11_v_heatmap_bodega_semana.sql")
    assert idx_dim < idx_fact < idx_view < idx_looker


def test_sql_files_exist_ctas_no_dml_and_partition(build_gold):
    for name in build_gold.BUILD_FILES:
        path = SQL_DIR / name
        assert path.is_file(), name
        text = path.read_text(encoding="utf-8")
        upper = text.upper()
        assert "INSERT " not in upper
        assert "MERGE " not in upper
        assert "DELETE " not in upper
        table = name.split("_", 1)[1].replace(".sql", "")
        # 01_d_fecha → d_fecha; 05_f_movimientos_bodega → f_movimientos_bodega
        table = name[3:].replace(".sql", "")
        errs = build_gold.assert_ctas_redeclarations(text, table)
        assert errs == [], errs
        if table.startswith("f_"):
            assert "PARTITION BY RANGE_BUCKET" in text
            assert "GENERATE_ARRAY(20250401, 20261201, 100)" in text
            assert "GENERATE_ARRAY(20250401, 20261101, 100)" not in text
            assert "CLUSTER BY" in text


def test_expected_gold_counts_seed_42(build_gold):
    assert build_gold.EXPECTED_GOLD_COUNTS == {
        "d_fecha": 583,
        "d_bodega": 8,
        "d_sku": 250,
        "d_transportista": 12,
        "f_movimientos_bodega": 55_548,
        "f_stock_diario": 208_440,
        "f_entregas": 29_803,
    }
    # f_entregas = silver entregas; f_stock = silver stock
    assert build_gold.EXPECTED_GOLD_COUNTS["f_entregas"] == 29_803
    assert build_gold.EXPECTED_GOLD_COUNTS["f_stock_diario"] == 208_440


def test_d_fecha_covers_all_fact_dates_including_delivery_lag(build_gold):
    """
    Regresión orphan ent_fecha (Julio): delivery_date puede ser WMS_END+0..4.
    d_fecha debe cubrir hasta 2026-11-04; el SQL viejo a 2026-10-31 falla.
    """
    from datetime import date

    sql = (SQL_DIR / "01_d_fecha.sql").read_text(encoding="utf-8")
    start, end = build_gold.parse_d_fecha_bounds(sql)
    assert build_gold.d_fecha_covers_fact_dates(
        d_fecha_start=start, d_fecha_end=end
    )
    assert end == build_gold.D_FECHA_END == "2026-11-04"
    assert start == build_gold.D_FECHA_START
    # Conteos: 2025-04-01 → 2026-11-04 inclusive = 583
    n = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    assert n == 583
    assert build_gold.EXPECTED_GOLD_COUNTS["d_fecha"] == n

    # Control negativo: horizonte WMS-only (bug Julio, 79 orphans).
    assert not build_gold.d_fecha_covers_fact_dates(
        d_fecha_start="2025-04-01",
        d_fecha_end="2026-10-31",
    )

    # Contra seed 42 real (si CSV presente): max delivery_date ≤ d_fecha end.
    raw_ent = ROOT / "data" / "raw" / "sap_tm_entregas.csv"
    if raw_ent.is_file():
        import pandas as pd

        ent = pd.read_csv(raw_ent, dtype=str)
        dd = pd.to_datetime(ent["delivery_date"], errors="coerce").dropna()
        assert dd.max().date() <= date.fromisoformat(end)
        # Exact orphan count under old calendar
        orphans = (dd.dt.date > date(2026, 10, 31)).sum()
        assert orphans == 79


def test_ddl_required_modes_match_ctas_outputs(build_gold):
    """
    Lección Paso 6 → Paso 7: columnas NOT NULL del DDL gold deben existir
    en el SELECT del CTAS; flags BOOL REQUIRED vía IF(..., TRUE, FALSE).
    """
    ddl = DDL_GOLD.read_text(encoding="utf-8")
    required = build_gold.parse_ddl_required_columns(ddl)
    assert "on_time" in required["f_entregas"]
    assert "in_full" in required["f_entregas"]
    assert "ata_ts" not in required["f_entregas"]  # NULLABLE (in_transit)
    assert "qty_ordered" not in required.get("f_movimientos_bodega", [])

    errs = build_gold.assert_ctas_covers_required(ddl, SQL_DIR)
    assert errs == [], errs

    ent = (SQL_DIR / "07_f_entregas.sql").read_text(encoding="utf-8")
    assert "AS on_time" in ent or "as on_time" in ent.lower()
    assert "AS in_full" in ent or "as in_full" in ent.lower()
    assert "TRUE" in ent and "FALSE" in ent


def test_otif_and_stock_views_follow_kpi_doc(build_gold):
    otif = (SQL_DIR / "08_v_otif_diario.sql").read_text(encoding="utf-8")
    cycle = (SQL_DIR / "09_v_cycle_time.sql").read_text(encoding="utf-8")
    stock = (SQL_DIR / "10_v_stock_salud.sql").read_text(encoding="utf-8")
    assert "in_transit" in otif  # excluded from closed denom
    assert "otif_pct" in otif
    assert "on_time" in otif
    assert "pick_start_ts" in cycle
    assert "cycle_hours" in cycle
    assert "is_stockout" in stock
    assert "doh_7d" in stock
    assert "is_overstock" in stock
    # Assumption documented in SQL comment (no invented SLA as if from workshop)
    assert "ASUNCIÓN" in stock or "ASUNCION" in stock.upper()


def test_views_08_10_columns_exist_in_gold_ddl(build_gold):
    """
    Regresión dry-run vistas: columnas referenciadas (p. ej. f.on_time) deben
    existir en ddl_gold.sql — el contrato que apply_ddl sincroniza a BQ.
    Falla si se quitan on_time/in_full del DDL aunque el CTAS 07 las produzca.
    """
    ddl = DDL_GOLD.read_text(encoding="utf-8")
    errs = build_gold.assert_views_columns_in_gold_contract(ddl, SQL_DIR)
    assert errs == [], errs

    # Control negativo: DDL sin on_time/in_full debe fallar (bug Julio).
    ddl_legacy_lines = []
    for line in ddl.splitlines():
        if re.search(r"\bon_time\b|\bin_full\b", line):
            continue
        ddl_legacy_lines.append(line)
    ddl_legacy = "\n".join(ddl_legacy_lines)
    entregas_body = ddl_legacy.split("f_entregas")[1].split("PARTITION")[0]
    assert "on_time" not in entregas_body
    assert "in_full" not in entregas_body
    neg = build_gold.assert_views_columns_in_gold_contract(ddl_legacy, SQL_DIR)
    assert any("on_time" in e for e in neg), neg
    assert any("in_full" in e for e in neg), neg

    # Refs explícitas de las vistas OTIF/cycle.
    otif_refs = build_gold.view_gold_column_refs(
        (SQL_DIR / "08_v_otif_diario.sql").read_text(encoding="utf-8")
    )
    assert "on_time" in otif_refs.get("f_entregas", set())
    assert "in_full" in otif_refs.get("f_entregas", set())
    cycle_refs = build_gold.view_gold_column_refs(
        (SQL_DIR / "09_v_cycle_time.sql").read_text(encoding="utf-8")
    )
    assert "on_time" in cycle_refs.get("f_entregas", set())
    stock_refs = build_gold.view_gold_column_refs(
        (SQL_DIR / "10_v_stock_salud.sql").read_text(encoding="utf-8")
    )
    assert "f_stock_diario" in stock_refs or "d_fecha" in stock_refs
    assert "usable_qty" in stock_refs.get("f_stock_diario", set()) or any(
        "usable_qty" in cols for cols in stock_refs.values()
    )


def test_dry_run_no_writes(build_gold, tmp_path: Path):
    sql_dir = tmp_path / "gold"
    sql_dir.mkdir()
    for name in build_gold.BUILD_FILES:
        # NN_v_* = views; resto = tables (dims/facts).
        is_view = "_v_" in name
        if is_view:
            (sql_dir / name).write_text(
                f"CREATE OR REPLACE VIEW `gold_kora.v_{name}` AS SELECT 1 AS x;",
                encoding="utf-8",
            )
        else:
            (sql_dir / name).write_text(
                f"CREATE OR REPLACE TABLE `gold_kora.t_{name}` AS SELECT 1 AS x;",
                encoding="utf-8",
            )

    client = MagicMock()
    job = MagicMock()
    job.total_bytes_processed = 0
    client.query.return_value = job

    code = build_gold.run(
        dry_run=True,
        project_id="kora-sports",
        client=client,
        sql_dir=sql_dir,
    )
    assert code == 0
    assert client.query.call_count == len(build_gold.BUILD_FILES)
    for call in client.query.call_args_list:
        cfg = call.kwargs["job_config"]
        assert cfg.dry_run is True
    job.result.assert_not_called()


def test_run_post_checks_mismatch_exits_1(build_gold, tmp_path: Path):
    sql_dir = tmp_path / "gold"
    sql_dir.mkdir()
    for name in build_gold.BUILD_FILES:
        (sql_dir / name).write_text(
            "CREATE OR REPLACE TABLE `gold_kora.x` AS SELECT 1 AS x;",
            encoding="utf-8",
        )

    client = MagicMock()
    with patch.object(build_gold, "apply_build_file") as apply_mock:
        apply_mock.side_effect = lambda client, path, dry_run=False: build_gold.BuildResult(
            file_name=path.name, action="applied", detail="ok"
        )
        with patch.object(build_gold, "post_run_checks", return_value=2):
            code = build_gold.run(
                dry_run=False,
                project_id="kora-sports",
                client=client,
                sql_dir=sql_dir,
            )
    assert code == 1


def test_run_post_checks_pass(build_gold, tmp_path: Path):
    sql_dir = tmp_path / "gold"
    sql_dir.mkdir()
    for name in build_gold.BUILD_FILES:
        (sql_dir / name).write_text(
            "CREATE OR REPLACE TABLE `gold_kora.x` AS SELECT 1 AS x;",
            encoding="utf-8",
        )

    client = MagicMock()
    with patch.object(build_gold, "apply_build_file") as apply_mock:
        apply_mock.side_effect = lambda client, path, dry_run=False: build_gold.BuildResult(
            file_name=path.name, action="applied", detail="ok"
        )
        with patch.object(build_gold, "post_run_checks", return_value=0):
            code = build_gold.run(
                dry_run=False,
                project_id="kora-sports",
                client=client,
                sql_dir=sql_dir,
            )
    assert code == 0


def test_makefile_n_run_includes_build_gold():
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
    assert "scripts/build_gold.py" in out
    assert "scripts/demo_partition_prune.py" in out
    idx_silver = out.index("scripts/transform_silver.py")
    idx_gold = out.index("scripts/build_gold.py")
    idx_demo = out.index("scripts/demo_partition_prune.py")
    assert idx_silver < idx_gold < idx_demo
    mk = MAKEFILE.read_text(encoding="utf-8")
    assert "55548" in mk
    assert "583" in mk
    assert "gold_kora row counts" in mk
    assert "__UNPARTITIONED__" in mk


def test_f_entregas_flags_never_null_expression(build_gold):
    ent = (SQL_DIR / "07_f_entregas.sql").read_text(encoding="utf-8")
    # Coercion pattern for REQUIRED BOOL
    assert "IF(" in ent
    assert "TRUE" in ent and "FALSE" in ent
    # No bare `ata_ts <= eta_ts AS on_time` without IF (would be NULL when ata null)
    assert "IF(e.ata_ts IS NOT NULL AND e.ata_ts <= e.eta_ts, TRUE, FALSE) AS on_time" in ent.replace(
        "\n", " "
    ).replace("  ", " ") or "IF(e.ata_ts IS NOT NULL AND e.ata_ts <= e.eta_ts, TRUE, FALSE) AS on_time" in ent
