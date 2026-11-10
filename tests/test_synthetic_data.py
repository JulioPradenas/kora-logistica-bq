"""
Validación del generador sintético KORA (Paso 2).

Corre contra CSV recién generados (fixture session-scoped).
Determinismo: horizonte fijo (DEFAULT_HORIZON_END), independiente del día.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
GENERATOR = ROOT / "scripts" / "generate_synthetic.py"

# Debe coincidir con scripts/generate_synthetic.py
DEFAULT_HORIZON_END = date(2026, 10, 31)
DEFAULT_HORIZON_START = date(2025, 4, 1)  # 18 meses atrás, día 1 del mes

EXPECTED_FILES = [
    "md_bodega.csv",
    "md_sku.csv",
    "md_transportista.csv",
    "sap_wms_movimientos.csv",
    "sap_wms_stock_snapshot.csv",
    "sap_tm_entregas.csv",
]

COLUMNS = {
    "md_bodega.csv": ["warehouse_id", "warehouse_name", "region_code", "city", "is_active"],
    "md_sku.csv": ["sku_id", "sku_name", "category", "brand", "unit_cost_clp"],
    "md_transportista.csv": ["carrier_id", "carrier_name", "service_level"],
    "sap_wms_movimientos.csv": [
        "movement_id",
        "event_date",
        "warehouse_id",
        "sku_id",
        "movement_code",
        "movement_type",
        "qty",
        "qty_ordered",
        "order_id",
        "shipment_id",
        "pick_start_ts",
        "pick_end_ts",
    ],
    "sap_wms_stock_snapshot.csv": [
        "snapshot_date",
        "warehouse_id",
        "sku_id",
        "on_hand_qty",
        "reserved_qty",
    ],
    "sap_tm_entregas.csv": [
        "shipment_id",
        "order_id",
        "warehouse_id",
        "carrier_id",
        "comuna_id",
        "comuna_name",
        "ship_date",
        "delivery_date",
        "eta_ts",
        "ata_ts",
        "status",
        "qty_delivered",
    ],
}


def _run_generator(env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["RANDOM_SEED"] = "42"
    env["HORIZON_MONTHS"] = "18"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(GENERATOR)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="session")
def generated_raw() -> Path:
    """Generate once per session into data/raw (script OUT path = Julio runbook)."""
    result = _run_generator()
    assert result.returncode == 0, f"generator failed:\n{result.stdout}\n{result.stderr}"
    for name in EXPECTED_FILES:
        assert (RAW / name).exists(), f"missing {name}"
    return RAW


@pytest.fixture(scope="session")
def frames(generated_raw: Path) -> dict[str, pd.DataFrame]:
    return {name: pd.read_csv(generated_raw / name, keep_default_na=False) for name in EXPECTED_FILES}


def test_expected_columns(frames: dict[str, pd.DataFrame]) -> None:
    for name, cols in COLUMNS.items():
        assert list(frames[name].columns) == cols, f"column mismatch in {name}"


def test_types_and_non_empty(frames: dict[str, pd.DataFrame]) -> None:
    bodega = frames["md_bodega.csv"]
    sku = frames["md_sku.csv"]
    carrier = frames["md_transportista.csv"]
    mov = frames["sap_wms_movimientos.csv"]
    stk = frames["sap_wms_stock_snapshot.csv"]
    ent = frames["sap_tm_entregas.csv"]

    # Bronze con anomalías Paso 6 (seed 42): 8/250/12/60285/208555/29923
    assert len(bodega) == 8
    assert len(sku) == 250
    assert len(carrier) == 12
    assert len(mov) == 60_285
    assert len(ent) == 29_923
    assert len(stk) == 208_555
    assert len(stk) > len(mov)

    assert pd.api.types.is_integer_dtype(sku["unit_cost_clp"]) or sku["unit_cost_clp"].astype(int).notna().all()
    # qty puede ser numérico; null-required anomalies usan movement_id nuevos con qty heredado.
    assert pd.to_numeric(mov["qty"], errors="coerce").notna().all()
    assert pd.to_numeric(stk["on_hand_qty"], errors="coerce").notna().all()
    assert pd.to_numeric(stk["reserved_qty"], errors="coerce").notna().all()
    assert pd.to_numeric(ent["qty_delivered"], errors="coerce").notna().all()


def test_key_uniqueness(frames: dict[str, pd.DataFrame]) -> None:
    """Maestros únicos; hechos tienen dupes documentados (Paso 6)."""
    assert frames["md_bodega.csv"]["warehouse_id"].is_unique
    assert frames["md_sku.csv"]["sku_id"].is_unique
    assert frames["md_transportista.csv"]["carrier_id"].is_unique

    mov = frames["sap_wms_movimientos.csv"]
    # exact(50)+key(40) extras → 90 filas con movement_id duplicado (50+40 keys touched).
    mov_dupe_rows = int(mov["movement_id"].duplicated(keep=False).sum())
    assert mov_dupe_rows >= 90

    ent = frames["sap_tm_entregas.csv"]
    ent_dupe_rows = int(ent["shipment_id"].duplicated(keep=False).sum())
    assert ent_dupe_rows >= 70

    stk = frames["sap_wms_stock_snapshot.csv"]
    stk_dupe_mask = stk.duplicated(
        subset=["snapshot_date", "warehouse_id", "sku_id"], keep=False
    )
    assert int(stk_dupe_mask.sum()) >= 80


def test_referential_integrity(frames: dict[str, pd.DataFrame]) -> None:
    """Núcleo limpio respeta FKs; anomalías Paso 6 inyectan FK rotas documentadas."""
    import importlib.util

    gen_path = ROOT / "scripts" / "generate_synthetic.py"
    spec = importlib.util.spec_from_file_location("generate_synthetic", gen_path)
    assert spec and spec.loader
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    wh = set(frames["md_bodega.csv"]["warehouse_id"])
    skus = set(frames["md_sku.csv"]["sku_id"])
    carriers = set(frames["md_transportista.csv"]["carrier_id"])

    mov = frames["sap_wms_movimientos.csv"]
    stk = frames["sap_wms_stock_snapshot.csv"]
    ent = frames["sap_tm_entregas.csv"]

    assert gen.ANOMALY_WH in set(mov["warehouse_id"])
    assert gen.ANOMALY_SKU in set(mov["sku_id"])
    assert gen.ANOMALY_SKU in set(stk["sku_id"])
    assert gen.ANOMALY_CARRIER in set(ent["carrier_id"])
    assert gen.ANOMALY_WH in set(ent["warehouse_id"])

    # Conteos exactos de FK rotas
    assert (mov["warehouse_id"] == gen.ANOMALY_WH).sum() == gen.ANOMALY_COUNTS[
        "missing_fk_warehouse_movimientos"
    ]
    assert (mov["sku_id"] == gen.ANOMALY_SKU).sum() == gen.ANOMALY_COUNTS[
        "missing_fk_sku_movimientos"
    ]
    assert (stk["sku_id"] == gen.ANOMALY_SKU).sum() == gen.ANOMALY_COUNTS[
        "missing_fk_sku_stock"
    ]
    assert (ent["carrier_id"] == gen.ANOMALY_CARRIER).sum() == gen.ANOMALY_COUNTS[
        "missing_fk_carrier_entregas"
    ]
    assert (ent["warehouse_id"] == gen.ANOMALY_WH).sum() == gen.ANOMALY_COUNTS[
        "missing_fk_warehouse_entregas"
    ]

    # Intersección shipment/order del núcleo (excluye SHP-FK* / SHP-NULL*)
    core_ent = ent[~ent["shipment_id"].astype(str).str.startswith(("SHP-FK", "SHP-NULL"))]
    mov_ships = set(mov.loc[mov["shipment_id"].astype(str).str.len() > 0, "shipment_id"])
    mov_orders = set(mov.loc[mov["order_id"].astype(str).str.len() > 0, "order_id"])
    assert set(core_ent["shipment_id"]).issubset(mov_ships)
    assert set(core_ent["order_id"]).issubset(mov_orders)
    assert len(set(core_ent["shipment_id"]) & mov_ships) > 1000

    # Núcleo (sin anomalías) respeta FKs a maestros
    core_mov = mov[~mov["movement_id"].astype(str).str.startswith(("MOV-FK", "MOV-NULL"))]
    core_stk = stk[
        (stk["sku_id"] != gen.ANOMALY_SKU)
        & (stk["sku_id"].astype(str).str.startswith("SKU-NULL") == False)  # noqa: E712
        & (stk["warehouse_id"].astype(str).str.len() > 0)
        & (stk["snapshot_date"].astype(str).str.len() > 0)
    ]
    assert set(core_mov["warehouse_id"]).issubset(wh)
    assert set(core_mov["sku_id"]).issubset(skus)
    assert set(core_stk["warehouse_id"]).issubset(wh)
    assert set(core_stk["sku_id"]).issubset(skus)
    core_ent_fk = ent[
        ~ent["shipment_id"].astype(str).str.startswith(("SHP-FK", "SHP-NULL"))
    ]
    assert set(core_ent_fk["warehouse_id"]).issubset(wh)
    assert set(core_ent_fk["carrier_id"]).issubset(carriers)


def test_no_negative_stock(frames: dict[str, pd.DataFrame]) -> None:
    """Raw export must not carry negative on_hand/reserved.

    reserved ≤ on_hand is a silver rule (see data/README.md); ADJ can leave
    reserved > on_hand briefly in raw, which transforms clean up later.
    """
    stk = frames["sap_wms_stock_snapshot.csv"]
    on_hand = pd.to_numeric(stk["on_hand_qty"])
    reserved = pd.to_numeric(stk["reserved_qty"])
    assert (on_hand >= 0).all()
    assert (reserved >= 0).all()


def test_coherent_date_ranges(frames: dict[str, pd.DataFrame]) -> None:
    mov = frames["sap_wms_movimientos.csv"]
    stk = frames["sap_wms_stock_snapshot.csv"]
    ent = frames["sap_tm_entregas.csv"]

    # Excluir filas con fechas vacías (anomalías null_required Paso 6).
    mov_dates = pd.to_datetime(mov.loc[mov["event_date"].astype(str).str.len() > 0, "event_date"])
    stk_dates = pd.to_datetime(
        stk.loc[stk["snapshot_date"].astype(str).str.len() > 0, "snapshot_date"]
    )
    ship_dates = pd.to_datetime(ent["ship_date"])
    deliv_ok = ent["delivery_date"].astype(str).str.len() > 0
    deliv_dates = pd.to_datetime(ent.loc[deliv_ok, "delivery_date"])
    ship_for_deliv = pd.to_datetime(ent.loc[deliv_ok, "ship_date"])

    assert mov_dates.min().date() == DEFAULT_HORIZON_START
    assert mov_dates.max().date() == DEFAULT_HORIZON_END
    span_days = (mov_dates.max() - mov_dates.min()).days
    assert span_days >= 365, f"horizon too short: {span_days} days"
    assert stk_dates.min() >= mov_dates.min()
    # missing_fk_sku_stock usa fechas 2025-06-* dentro del horizonte
    assert stk_dates.max() <= mov_dates.max()
    assert (deliv_dates >= ship_for_deliv).all()
    # deliveries should not start before movement horizon
    assert ship_dates.min() >= mov_dates.min()


def test_documented_anomalies_exact_counts(frames: dict[str, pd.DataFrame]) -> None:
    """ANOMALY_COUNTS (seed 42) se materializan exactamente en los CSV."""
    import importlib.util

    gen_path = ROOT / "scripts" / "generate_synthetic.py"
    spec = importlib.util.spec_from_file_location("generate_synthetic", gen_path)
    assert spec and spec.loader
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    mov = frames["sap_wms_movimientos.csv"]
    stk = frames["sap_wms_stock_snapshot.csv"]
    ent = frames["sap_tm_entregas.csv"]
    c = gen.ANOMALY_COUNTS

    assert len(mov) == 60_120 + (
        c["exact_dupes_movimientos"]
        + c["key_dupes_movimientos"]
        + c["missing_fk_warehouse_movimientos"]
        + c["missing_fk_sku_movimientos"]
        + c["null_required_movimientos"]
    )
    assert len(stk) == 208_440 + (
        c["exact_dupes_stock"]
        + c["key_dupes_stock"]
        + c["missing_fk_sku_stock"]
        + c["null_required_stock"]
    )
    assert len(ent) == 29_803 + (
        c["exact_dupes_entregas"]
        + c["key_dupes_entregas"]
        + c["missing_fk_carrier_entregas"]
        + c["missing_fk_warehouse_entregas"]
        + c["null_required_entregas"]
    )

    # Null required: fechas/keys vacías
    assert ((mov["event_date"].astype(str) == "") | (mov["warehouse_id"].astype(str) == "")).sum() == c[
        "null_required_movimientos"
    ]
    assert (
        (stk["snapshot_date"].astype(str) == "") | (stk["warehouse_id"].astype(str) == "")
    ).sum() == c["null_required_stock"]
    assert (
        (ent["delivery_date"].astype(str) == "") | (ent["order_id"].astype(str) == "")
    ).sum() == c["null_required_entregas"]

    # Status casing: variantes no canónicas exactas (in-place)
    canonical = {"delivered", "late_delivered", "failed", "in_transit"}
    non_canon = ent[~ent["status"].isin(canonical)]
    assert len(non_canon) == c["status_casing_entregas"]


def test_seed_determinism(generated_raw: Path) -> None:
    """Seed 42 + fixed HORIZON_END → identical CSV bytes, any calendar day.

    Re-runs with an explicit HORIZON_END (and without) must match the session
    fixture hashes — proves reproducibility is not tied to date.today().
    """
    before = {
        name: hashlib.sha256((generated_raw / name).read_bytes()).hexdigest()
        for name in EXPECTED_FILES
    }

    # Explicit fixed end (same as default)
    result_explicit = _run_generator({"HORIZON_END": DEFAULT_HORIZON_END.isoformat()})
    assert result_explicit.returncode == 0, (
        f"regen with HORIZON_END failed:\n{result_explicit.stdout}\n{result_explicit.stderr}"
    )
    after_explicit = {
        name: hashlib.sha256((generated_raw / name).read_bytes()).hexdigest()
        for name in EXPECTED_FILES
    }
    assert before == after_explicit, "explicit HORIZON_END diverged from default run"

    # Default path again (no HORIZON_END in env from parent — clear if present)
    env_clear = {k: v for k, v in os.environ.items() if k != "HORIZON_END"}
    env_clear["RANDOM_SEED"] = "42"
    env_clear["HORIZON_MONTHS"] = "18"
    result_default = subprocess.run(
        [sys.executable, str(GENERATOR)],
        cwd=ROOT,
        env=env_clear,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result_default.returncode == 0, (
        f"default regen failed:\n{result_default.stdout}\n{result_default.stderr}"
    )
    after_default = {
        name: hashlib.sha256((generated_raw / name).read_bytes()).hexdigest()
        for name in EXPECTED_FILES
    }
    assert before == after_default, "default re-run (no today dependency) diverged"

    # Override to a different end must change hashes (sanity: end date is honored)
    result_other = _run_generator({"HORIZON_END": "2026-09-30", "HORIZON_MONTHS": "18"})
    assert result_other.returncode == 0, (
        f"alt HORIZON_END failed:\n{result_other.stdout}\n{result_other.stderr}"
    )
    after_other = {
        name: hashlib.sha256((generated_raw / name).read_bytes()).hexdigest()
        for name in EXPECTED_FILES
    }
    assert before != after_other, "changing HORIZON_END should change output"

    # Restore canonical files for any later inspection
    restore = _run_generator({"HORIZON_END": DEFAULT_HORIZON_END.isoformat()})
    assert restore.returncode == 0
