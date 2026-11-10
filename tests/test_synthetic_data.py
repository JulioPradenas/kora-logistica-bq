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

    assert len(bodega) == 8
    assert len(sku) == 250
    assert len(carrier) == 12
    assert len(mov) >= 30_000
    assert len(ent) >= 10_000
    assert len(stk) > len(mov)

    assert pd.api.types.is_integer_dtype(sku["unit_cost_clp"]) or sku["unit_cost_clp"].astype(int).notna().all()
    assert pd.to_numeric(mov["qty"], errors="raise").notna().all()
    assert pd.to_numeric(stk["on_hand_qty"], errors="raise").notna().all()
    assert pd.to_numeric(stk["reserved_qty"], errors="raise").notna().all()
    assert pd.to_numeric(ent["qty_delivered"], errors="raise").notna().all()


def test_key_uniqueness(frames: dict[str, pd.DataFrame]) -> None:
    assert frames["md_bodega.csv"]["warehouse_id"].is_unique
    assert frames["md_sku.csv"]["sku_id"].is_unique
    assert frames["md_transportista.csv"]["carrier_id"].is_unique
    assert frames["sap_wms_movimientos.csv"]["movement_id"].is_unique
    assert frames["sap_tm_entregas.csv"]["shipment_id"].is_unique

    stk = frames["sap_wms_stock_snapshot.csv"]
    assert not stk.duplicated(subset=["snapshot_date", "warehouse_id", "sku_id"]).any()


def test_referential_integrity(frames: dict[str, pd.DataFrame]) -> None:
    wh = set(frames["md_bodega.csv"]["warehouse_id"])
    skus = set(frames["md_sku.csv"]["sku_id"])
    carriers = set(frames["md_transportista.csv"]["carrier_id"])

    mov = frames["sap_wms_movimientos.csv"]
    stk = frames["sap_wms_stock_snapshot.csv"]
    ent = frames["sap_tm_entregas.csv"]

    assert set(mov["warehouse_id"]).issubset(wh)
    assert set(mov["sku_id"]).issubset(skus)
    assert set(stk["warehouse_id"]).issubset(wh)
    assert set(stk["sku_id"]).issubset(skus)
    assert set(ent["warehouse_id"]).issubset(wh)
    assert set(ent["carrier_id"]).issubset(carriers)

    # Each delivery refs an existing shipment/order on an OUT movement
    mov_ships = set(mov.loc[mov["shipment_id"].astype(str).str.len() > 0, "shipment_id"])
    mov_orders = set(mov.loc[mov["order_id"].astype(str).str.len() > 0, "order_id"])
    assert set(ent["shipment_id"]).issubset(mov_ships)
    assert set(ent["order_id"]).issubset(mov_orders)
    assert len(set(ent["shipment_id"]) & mov_ships) > 1000


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

    mov_dates = pd.to_datetime(mov["event_date"])
    stk_dates = pd.to_datetime(stk["snapshot_date"])
    ship_dates = pd.to_datetime(ent["ship_date"])
    deliv_dates = pd.to_datetime(ent["delivery_date"])

    assert mov_dates.min().date() == DEFAULT_HORIZON_START
    assert mov_dates.max().date() == DEFAULT_HORIZON_END
    span_days = (mov_dates.max() - mov_dates.min()).days
    assert span_days >= 365, f"horizon too short: {span_days} days"
    assert stk_dates.min() >= mov_dates.min()
    assert stk_dates.max() <= mov_dates.max()
    assert (deliv_dates >= ship_dates).all()
    # deliveries should not start before movement horizon
    assert ship_dates.min() >= mov_dates.min()


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
