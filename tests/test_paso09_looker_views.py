"""
Tests Paso 9 — vistas Looker (heatmap / funnel / drill) + wiring build_gold.

Diseño: fallan en el código de Paso 8 (f7bb0b7) si faltan SQL 11–13 o
BUILD_FILES/VIEW_TABLES/post-checks. CI verde tras este PR (mocks; sin GCP).
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
LOOKER_README = ROOT / "looker" / "README.md"
PASO09_DOC = ROOT / "docs" / "paso-09-looker.md"
DDL_SILVER = ROOT / "sql" / "ddl_silver.sql"
DDL_GOLD = ROOT / "sql" / "ddl_gold.sql"

LOOKER_SQL = (
    "11_v_heatmap_bodega_semana.sql",
    "12_v_funnel_logistico.sql",
    "13_v_entregas_drill.sql",
)


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def build_gold():
    return _load_module(SCRIPT, "build_gold_paso09")


def test_looker_sql_files_exist_and_are_views():
    """Falla en main Paso 8: no existen 11–13."""
    for name in LOOKER_SQL:
        path = SQL_DIR / name
        assert path.is_file(), f"missing {name}"
        text = path.read_text(encoding="utf-8")
        assert "CREATE OR REPLACE VIEW" in text.upper()
        assert "INSERT " not in text.upper()
        assert "MERGE " not in text.upper()
        assert "DELETE " not in text.upper()


def test_build_files_include_looker_views_after_kpi(build_gold):
    names = list(build_gold.BUILD_FILES)
    for fname in LOOKER_SQL:
        assert fname in names, fname
    assert names.index("10_v_stock_salud.sql") < names.index(
        "11_v_heatmap_bodega_semana.sql"
    )
    assert "v_heatmap_bodega_semana" in build_gold.VIEW_TABLES
    assert "v_funnel_logistico" in build_gold.VIEW_TABLES
    assert "v_entregas_drill" in build_gold.VIEW_TABLES
    for v in build_gold.LOOKER_VIEWS:
        assert v in build_gold.VIEW_TABLES


def test_schema_gaps_documented_no_invented_stage_columns():
    """
    Honestidad schema:
    - comuna SÍ en gold f_entregas / silver entregas
    - NO hay stage_picking/despacho/entrega como columna → funnel lo documenta
    - NO inventar d_comuna_destino si no está en ddl_gold
    """
    ddl_s = DDL_SILVER.read_text(encoding="utf-8")
    ddl_g = DDL_GOLD.read_text(encoding="utf-8")
    assert "comuna_id" in ddl_s and "comuna_name" in ddl_s
    assert "comuna_id" in ddl_g and "comuna_name" in ddl_g
    assert "d_comuna_destino" not in ddl_g

    # No stage process columns in DDL
    for forbidden in (
        "stage_picking",
        "funnel_stage",
        "estado_proceso",
        "picking_stage",
    ):
        assert forbidden not in ddl_s.lower()
        assert forbidden not in ddl_g.lower()

    funnel = (SQL_DIR / "12_v_funnel_logistico.sql").read_text(encoding="utf-8")
    assert "GAP" in funnel
    assert "shipment_e2e" in funnel
    assert "movement_type" in funnel
    assert "pick_start_ts" in funnel
    # Must not SELECT a fake physical stage column from facts
    assert "e.stage" not in funnel
    assert "f.stage" not in funnel

    drill = (SQL_DIR / "13_v_entregas_drill.sql").read_text(encoding="utf-8")
    assert "comuna_id" in drill
    assert "sku_id" in drill
    assert "transportista_id" in drill
    # Gap dim documentada en comentario OK; no debe haber FROM/JOIN a d_comuna_destino.
    assert not re.search(
        r"(?:FROM|JOIN)\s+`?gold_kora\.d_comuna_destino`?",
        drill,
        flags=re.I,
    )

    heat = (SQL_DIR / "11_v_heatmap_bodega_semana.sql").read_text(encoding="utf-8")
    assert "bodega_id" in heat
    assert "semana" in heat.lower()
    assert "late_rate" in heat


def test_funnel_and_heatmap_reuse_existing_kpis_not_duplicate_otif():
    """Overview reusa 08/09/10; 11–13 no reimplementan otif_pct como SSoT."""
    for name in ("08_v_otif_diario.sql", "09_v_cycle_time.sql", "10_v_stock_salud.sql"):
        assert (SQL_DIR / name).is_file()
    heat = (SQL_DIR / "11_v_heatmap_bodega_semana.sql").read_text(encoding="utf-8")
    funnel = (SQL_DIR / "12_v_funnel_logistico.sql").read_text(encoding="utf-8")
    assert "otif_pct" not in heat
    assert "otif_pct" not in funnel


def test_post_run_view_checks_wired(build_gold):
    """post_run_checks debe invocar post_run_view_checks (Paso 9)."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "def post_run_view_checks" in src
    assert "post_run_view_checks(client)" in src
    assert "LOOKER_VIEWS" in src


def test_post_run_view_checks_fail_when_empty(build_gold):
    client = MagicMock()
    with patch.object(build_gold, "view_exists", return_value=True):
        with patch.object(build_gold, "scalar_count", return_value=0):
            with patch.object(build_gold, "run_query") as rq:
                rq.side_effect = [
                    [{"n_bodegas": 0, "n_semanas": 0, "n_null_late": 0}],
                    [
                        {
                            "n_rows": 0,
                            "n_picking": 0,
                            "n_despacho": 0,
                            "n_entrega": 0,
                            "c_picking": 0,
                            "c_despacho": 0,
                            "c_entrega": 0,
                        }
                    ],
                    [
                        {
                            "n": 0,
                            "n_sin_comuna": 0,
                            "n_sin_carrier": 0,
                            "n_con_sku": 0,
                        }
                    ],
                ]
                failures = build_gold.post_run_view_checks(client)
    # Cada vista vacía (+ coherencia heatmap/funnel/drill) suma fallos.
    assert failures >= len(build_gold.VIEW_TABLES)


def test_post_run_view_checks_pass_coherent(build_gold):
    client = MagicMock()
    n_views = len(build_gold.VIEW_TABLES)

    with patch.object(build_gold, "view_exists", return_value=True):
        with patch.object(build_gold, "scalar_count", return_value=100):
            with patch.object(build_gold, "run_query") as rq:
                rq.side_effect = [
                    [{"n_bodegas": 8, "n_semanas": 40, "n_null_late": 0}],
                    [
                        {
                            "n_rows": 3,
                            "n_picking": 1,
                            "n_despacho": 1,
                            "n_entrega": 1,
                            "c_picking": 20000,
                            "c_despacho": 29803,
                            "c_entrega": 25000,
                        }
                    ],
                    [
                        {
                            "n": 50000,
                            "n_sin_comuna": 0,
                            "n_sin_carrier": 0,
                            "n_con_sku": 48000,
                        }
                    ],
                ]
                failures = build_gold.post_run_view_checks(client)
    assert failures == 0
    assert rq.call_count == 3
    assert n_views == 6


def test_views_contract_includes_paso09(build_gold):
    ddl = DDL_GOLD.read_text(encoding="utf-8")
    errs = build_gold.assert_views_columns_in_gold_contract(ddl, SQL_DIR)
    assert errs == [], errs


def test_looker_readme_and_paso09_doc_placeholders():
    """Cierre Paso 9: viewer real + screenshots; sin PLACEHOLDER_*."""
    assert LOOKER_README.is_file()
    readme = LOOKER_README.read_text(encoding="utf-8")
    assert "kora-sports" in readme
    assert "gold_kora" in readme
    assert "PLACEHOLDER_LOOKER_VIEWER_URL" not in readme
    assert "PLACEHOLDER_SCREENSHOT_OVERVIEW" not in readme
    assert "PLACEHOLDER_SCREENSHOT_HEATMAP" not in readme
    assert "PLACEHOLDER_SCREENSHOT_FUNNEL" not in readme
    assert "PLACEHOLDER_SCREENSHOT_DRILL" not in readme
    viewer = (
        "https://datastudio.google.com/reporting/"
        "172aa667-fb63-4c36-84d4-4585c8ad89ba"
    )
    assert viewer in readme
    for name in (
        "01-overview.png",
        "02-heatmap.png",
        "03-funnel.png",
        "04-drill.png",
        "05-stock.png",
    ):
        assert name in readme
        assert (ROOT / "looker" / "screenshots" / name).is_file()
    assert "Stock salud" in readme
    assert "Lecciones de construcción" in readme
    assert "58,06%" in readme
    assert "SUM(n_otif)/SUM(n_intentadas_cerradas)" in readme
    assert PASO09_DOC.is_file()
    doc = PASO09_DOC.read_text(encoding="utf-8")
    assert "v_heatmap_bodega_semana" in doc
    assert "v_funnel_logistico" in doc
    assert "v_entregas_drill" in doc
    assert "GAP" in doc or "gap" in doc.lower()


def test_makefile_n_run_still_includes_build_gold_stage_6():
    proc = subprocess.run(
        ["make", "-n", "run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "scripts/build_gold.py" in out
    # Stage order: build_gold before demo prune
    assert out.index("scripts/build_gold.py") < out.index(
        "scripts/demo_partition_prune.py"
    )
