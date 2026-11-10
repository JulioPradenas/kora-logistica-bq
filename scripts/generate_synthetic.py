#!/usr/bin/env python3
"""
KORA Deportes — generador de exports SAP-like (lab portafolio).
Uso:
  python scripts/generate_synthetic.py
  python scripts/generate_synthetic.py --dry-run
  RANDOM_SEED=42 HORIZON_MONTHS=18 python scripts/generate_synthetic.py
  HORIZON_END=2026-10-31 python scripts/generate_synthetic.py   # default fijo

El fin de horizonte es FIJO por defecto (DEFAULT_HORIZON_END) para que seed 42
produzca siempre los mismos CSV, independiente del día de ejecución.
Override opcional: HORIZON_END=YYYY-MM-DD

`--dry-run` (Paso 5 / Makefile): reporta seed, horizonte y paths de salida
sin escribir CSV (compatible con `make run` dry-run → apply).
"""
from __future__ import annotations

import argparse
import os
import random
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw"

SEED = int(os.getenv("RANDOM_SEED", "42"))
HORIZON_MONTHS = int(os.getenv("HORIZON_MONTHS", "18"))
# Fecha de cierre del lab: fija → determinismo seed 42 (no date.today()).
DEFAULT_HORIZON_END = date(2026, 10, 31)
N_SKUS = int(os.getenv("N_SKUS", "250"))
N_WAREHOUSES = 8
N_CARRIERS = 12
SKUS_PER_WH = 45  # assortment activo por bodega → stock manejable

CATEGORIES = [
    ("CALZADO", ["Zapatilla running", "Zapatilla trekking", "Guayos"]),
    ("TEXTIL", ["Polera dry-fit", "Short entrenamiento", "Chaqueta cortaviento"]),
    ("ACCESORIO", ["Mochila", "Botella", "Rodillera"]),
    ("EQUIPO", ["Balón fútbol", "Raqueta tenis", "Colchoneta"]),
]
COMUNAS = [
    ("13101", "Santiago"),
    ("13114", "Las Condes"),
    ("13115", "Lo Barnechea"),
    ("13119", "Maipú"),
    ("13127", "Recoleta"),
    ("13132", "Vitacura"),
    ("5601", "Quilpué"),
    ("5109", "Viña del Mar"),
    ("6101", "Rancagua"),
    ("8101", "Concepción"),
]
# (code, type, label, weight) — sesgo a 601 OUT para ~25–40k entregas
MOV_TYPES = [
    ("101", "IN", "Compra / recepción", 12),
    ("601", "OUT", "Salida pedido", 55),
    ("701", "ADJ", "Ajuste ciclo", 8),
    ("311", "OUT", "Traslado salida", 13),
    ("312", "IN", "Traslado entrada", 12),
]
DELIVERY_STATUS = ["delivered", "delivered", "delivered", "delivered", "late_delivered", "failed", "in_transit"]


def pick_mov_type():
    codes, types, labels, weights = zip(*MOV_TYPES)
    i = random.choices(range(len(MOV_TYPES)), weights=weights, k=1)[0]
    return MOV_TYPES[i][0], MOV_TYPES[i][1], MOV_TYPES[i][2]


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def resolve_horizon_end() -> date:
    """Fin de horizonte: DEFAULT_HORIZON_END, o HORIZON_END=YYYY-MM-DD si se setea."""
    raw = os.getenv("HORIZON_END", "").strip()
    if not raw:
        return DEFAULT_HORIZON_END
    return date.fromisoformat(raw)


def horizon_start(end: date, months: int) -> date:
    """Primer día del mes, `months` meses atrás desde `end`."""
    y, m = end.year, end.month - months
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Genera CSV sintéticos KORA (seed 42, horizonte fijo)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Reporta seed/horizonte/paths sin escribir CSV.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    end = resolve_horizon_end()
    start = horizon_start(end, HORIZON_MONTHS)

    if args.dry_run:
        print("=== KORA synthetic export (dry-run) ===")
        print(f"seed={SEED} horizon_months={HORIZON_MONTHS} range={start} → {end}")
        print(f"would_write → {OUT}")
        for name in (
            "md_bodega.csv",
            "md_sku.csv",
            "md_transportista.csv",
            "sap_wms_movimientos.csv",
            "sap_wms_stock_snapshot.csv",
            "sap_tm_entregas.csv",
        ):
            print(f"  would_create  {OUT / name}")
        print("dry-run: no se escribieron CSV.")
        return

    random.seed(SEED)
    OUT.mkdir(parents=True, exist_ok=True)

    # --- maestros ---
    bodegas = []
    regiones = ["RM", "RM", "RM", "V", "V", "VI", "VIII", "RM"]
    for i in range(1, N_WAREHOUSES + 1):
        bodegas.append(
            {
                "warehouse_id": f"WH-{i:02d}",
                "warehouse_name": f"CD KORA {i:02d}",
                "region_code": regiones[i - 1],
                "city": ["Santiago", "Santiago", "Santiago", "Valparaíso", "Viña del Mar", "Rancagua", "Concepción", "Puente Alto"][i - 1],
                "is_active": True,
            }
        )
    df_bodega = pd.DataFrame(bodegas)

    skus = []
    for i in range(1, N_SKUS + 1):
        cat, names = CATEGORIES[(i - 1) % len(CATEGORIES)]
        skus.append(
            {
                "sku_id": f"SKU-{i:05d}",
                "sku_name": f"{names[(i - 1) % len(names)]} {i:03d}",
                "category": cat,
                "brand": "KORA",
                "unit_cost_clp": random.randint(3000, 89000),
            }
        )
    df_sku = pd.DataFrame(skus)

    carriers = []
    for i in range(1, N_CARRIERS + 1):
        carriers.append(
            {
                "carrier_id": f"CAR-{i:02d}",
                "carrier_name": f"3PL Partner {i:02d}",
                "service_level": random.choice(["STD", "STD", "EXP"]),
            }
        )
    df_carrier = pd.DataFrame(carriers)

    # assortment: SKUs activos por bodega
    assortment: dict[str, list[str]] = {}
    sku_ids = df_sku["sku_id"].tolist()
    for wh in df_bodega["warehouse_id"]:
        assortment[wh] = random.sample(sku_ids, k=SKUS_PER_WH)

    # stock on-hand / reserved en memoria
    stock: dict[tuple[str, str], dict[str, int]] = {}
    for wh, skus_wh in assortment.items():
        for sku in skus_wh:
            stock[(wh, sku)] = {
                "on_hand": random.randint(20, 200),
                "reserved": random.randint(0, 25),
            }

    movimientos: list[dict] = []
    entregas: list[dict] = []
    stock_rows: list[dict] = []

    order_seq = 100000
    ship_seq = 500000
    mov_seq = 1

    days = list(daterange(start, end))
    # ~120 movimientos/día → ~65k en 18 meses
    target_mov_per_day = 120

    for event_date in days:
        # ruido fin de semana: menos actividad
        weekend = event_date.weekday() >= 5
        n_mov = int(target_mov_per_day * (0.55 if weekend else 1.0))
        n_mov = max(40, int(random.gauss(n_mov, 15)))

        for _ in range(n_mov):
            wh = random.choice(bodegas)["warehouse_id"]
            sku = random.choice(assortment[wh])
            code, mtype, _ = pick_mov_type()
            st = stock[(wh, sku)]

            order_id = None
            shipment_id = None
            qty_ordered = None
            pick_start = None
            pick_end = None

            if mtype == "IN":
                qty = random.randint(5, 80)
                st["on_hand"] += qty
            elif mtype == "ADJ":
                qty = random.randint(-5, 5)
                if qty == 0:
                    qty = -1
                st["on_hand"] = max(0, st["on_hand"] + qty)
            else:  # OUT
                qty_ordered = random.randint(1, 12)
                # ~12% short-pick
                if random.random() < 0.12:
                    qty = max(0, qty_ordered - random.randint(1, 3))
                else:
                    qty = qty_ordered
                qty = min(qty, st["on_hand"])
                st["on_hand"] -= qty
                st["reserved"] = max(0, st["reserved"] - min(st["reserved"], qty_ordered))

                order_seq += 1
                ship_seq += 1
                order_id = f"ORD-{order_seq}"
                shipment_id = f"SHP-{ship_seq}"

                # lead time pick 0.5–8 h
                pick_h = random.uniform(0.4, 7.5)
                base_dt = datetime.combine(event_date, datetime.min.time()) + timedelta(hours=random.randint(7, 16))
                pick_start = base_dt
                pick_end = base_dt + timedelta(hours=pick_h)

                # ~92% de salidas 601 con qty>0 → entrega last-mile (restan traslados / retiros)
                if code == "601" and qty > 0 and random.random() < 0.92:
                    carrier = random.choice(carriers)
                    comuna_id, comuna_name = random.choice(COMUNAS)
                    # tránsito 0–4 días
                    transit = random.randint(0, 4)
                    delivery_date = event_date + timedelta(days=transit)
                    eta = datetime.combine(delivery_date, datetime.min.time()) + timedelta(hours=random.choice([12, 14, 18]))
                    status = random.choice(DELIVERY_STATUS)
                    if status == "in_transit":
                        ata = None
                    elif status in ("delivered", "late_delivered"):
                        delay_h = random.uniform(-6, 30) if status == "late_delivered" else random.uniform(-10, 2)
                        ata = eta + timedelta(hours=delay_h)
                        if ata <= eta:
                            status = "delivered"
                        else:
                            status = "late_delivered"
                    else:  # failed
                        ata = eta + timedelta(hours=random.uniform(1, 20))

                    entregas.append(
                        {
                            "shipment_id": shipment_id,
                            "order_id": order_id,
                            "warehouse_id": wh,
                            "carrier_id": carrier["carrier_id"],
                            "comuna_id": comuna_id,
                            "comuna_name": comuna_name,
                            "ship_date": event_date.isoformat(),
                            "delivery_date": delivery_date.isoformat(),
                            "eta_ts": eta.strftime("%Y-%m-%d %H:%M:%S"),
                            "ata_ts": ata.strftime("%Y-%m-%d %H:%M:%S") if ata else "",
                            "status": status,
                            "qty_delivered": qty if status in ("delivered", "late_delivered") else 0,
                        }
                    )

            movimientos.append(
                {
                    "movement_id": f"MOV-{mov_seq:08d}",
                    "event_date": event_date.isoformat(),
                    "warehouse_id": wh,
                    "sku_id": sku,
                    "movement_code": code,
                    "movement_type": mtype,
                    "qty": qty,
                    "qty_ordered": qty_ordered if qty_ordered is not None else "",
                    "order_id": order_id or "",
                    "shipment_id": shipment_id or "",
                    "pick_start_ts": pick_start.strftime("%Y-%m-%d %H:%M:%S") if pick_start else "",
                    "pick_end_ts": pick_end.strftime("%Y-%m-%d %H:%M:%S") if pick_end else "",
                }
            )
            mov_seq += 1

        # snapshot stock diario del assortment
        for wh, skus_wh in assortment.items():
            for sku in skus_wh:
                st = stock[(wh, sku)]
                # reserved flota un poco
                if random.random() < 0.15:
                    st["reserved"] = min(st["on_hand"], max(0, st["reserved"] + random.randint(-3, 5)))
                stock_rows.append(
                    {
                        "snapshot_date": event_date.isoformat(),
                        "warehouse_id": wh,
                        "sku_id": sku,
                        "on_hand_qty": st["on_hand"],
                        "reserved_qty": st["reserved"],
                    }
                )

    df_mov = pd.DataFrame(movimientos)
    df_stk = pd.DataFrame(stock_rows)
    df_ent = pd.DataFrame(entregas)

    df_bodega.to_csv(OUT / "md_bodega.csv", index=False)
    df_sku.to_csv(OUT / "md_sku.csv", index=False)
    df_carrier.to_csv(OUT / "md_transportista.csv", index=False)
    df_mov.to_csv(OUT / "sap_wms_movimientos.csv", index=False)
    df_stk.to_csv(OUT / "sap_wms_stock_snapshot.csv", index=False)
    df_ent.to_csv(OUT / "sap_tm_entregas.csv", index=False)

    print("=== KORA synthetic export ===")
    print(f"seed={SEED} horizon_months={HORIZON_MONTHS} range={start} → {end}")
    print(f"md_bodega:              {len(df_bodega):>8,}")
    print(f"md_sku:                 {len(df_sku):>8,}")
    print(f"md_transportista:       {len(df_carrier):>8,}")
    print(f"sap_wms_movimientos:    {len(df_mov):>8,}")
    print(f"sap_wms_stock_snapshot: {len(df_stk):>8,}")
    print(f"sap_tm_entregas:        {len(df_ent):>8,}")
    print(f"out → {OUT}")
    if len(df_mov) < 30_000:
        raise SystemExit("ERROR: movimientos < 30k — sube target_mov_per_day o HORIZON_MONTHS")


if __name__ == "__main__":
    main()

