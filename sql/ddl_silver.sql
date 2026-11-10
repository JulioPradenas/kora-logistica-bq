-- KORA Deportes — Paso 4: DDL silver (entidades limpias, vacío).
-- Dataset: silver_kora. Misma entidad que bronze; claves NOT NULL; estados canónicos.
-- Quarantine: silver_kora.quarantine_rows (Paso 6 — rejects con reason/payload).
-- Hechos con fecha: fecha_id INT64 YYYYMMDD + integer-range (sandbox).
-- Partición: RANGE_BUCKET mensuals (~interval 100) — ver docs/paso-04-ddl.md.
-- CREATE TABLE IF NOT EXISTS (sandbox sin DML). Transforms = pasos posteriores.

CREATE TABLE IF NOT EXISTS `silver_kora.md_bodega` (
  warehouse_id STRING NOT NULL,
  warehouse_name STRING NOT NULL,
  region_code STRING NOT NULL,
  city STRING NOT NULL,
  is_active BOOL NOT NULL
)
OPTIONS(
  description="Silver: maestro bodegas limpio (PK warehouse_id). Lab KORA."
);

CREATE TABLE IF NOT EXISTS `silver_kora.md_sku` (
  sku_id STRING NOT NULL,
  sku_name STRING NOT NULL,
  category STRING NOT NULL OPTIONS(description="CALZADO | TEXTIL | ACCESORIO | EQUIPO"),
  brand STRING NOT NULL,
  unit_cost_clp INT64 NOT NULL
)
OPTIONS(
  description="Silver: maestro SKU limpio (PK sku_id). Lab KORA."
);

CREATE TABLE IF NOT EXISTS `silver_kora.md_transportista` (
  carrier_id STRING NOT NULL,
  carrier_name STRING NOT NULL,
  service_level STRING NOT NULL OPTIONS(description="STD | EXP canónico")
)
OPTIONS(
  description="Silver: maestro transportista limpio (PK carrier_id). Lab KORA."
);

CREATE TABLE IF NOT EXISTS `silver_kora.sap_wms_movimientos` (
  movement_id STRING NOT NULL,
  fecha_id INT64 NOT NULL OPTIONS(description="YYYYMMDD desde event_date — partición"),
  event_date DATE NOT NULL,
  warehouse_id STRING NOT NULL,
  sku_id STRING NOT NULL,
  movement_code STRING NOT NULL,
  movement_type STRING NOT NULL OPTIONS(description="Canónico: IN | OUT | ADJ"),
  qty INT64 NOT NULL,
  qty_ordered INT64,
  order_id STRING,
  shipment_id STRING,
  pick_start_ts DATETIME,
  pick_end_ts DATETIME
)
PARTITION BY RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261101, 100))
CLUSTER BY warehouse_id, sku_id
OPTIONS(
  description="Silver: movimientos limpios — integer-range en fecha_id (mensual YYYYMMDD)."
);

CREATE TABLE IF NOT EXISTS `silver_kora.sap_wms_stock_snapshot` (
  fecha_id INT64 NOT NULL OPTIONS(description="YYYYMMDD desde snapshot_date"),
  snapshot_date DATE NOT NULL,
  warehouse_id STRING NOT NULL,
  sku_id STRING NOT NULL,
  on_hand_qty INT64 NOT NULL,
  reserved_qty INT64 NOT NULL,
  usable_qty INT64 NOT NULL OPTIONS(description="on_hand_qty - reserved_qty (regla lab)")
)
PARTITION BY RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261101, 100))
CLUSTER BY warehouse_id, sku_id
OPTIONS(
  description="Silver: stock diario limpio — integer-range en fecha_id."
);

CREATE TABLE IF NOT EXISTS `silver_kora.sap_tm_entregas` (
  shipment_id STRING NOT NULL,
  order_id STRING NOT NULL,
  fecha_id INT64 NOT NULL OPTIONS(description="YYYYMMDD desde delivery_date — partición"),
  warehouse_id STRING NOT NULL,
  carrier_id STRING NOT NULL,
  comuna_id STRING NOT NULL,
  comuna_name STRING NOT NULL,
  ship_date DATE NOT NULL,
  delivery_date DATE NOT NULL,
  eta_ts DATETIME NOT NULL,
  ata_ts DATETIME,
  status STRING NOT NULL OPTIONS(description="Canónico: delivered | late_delivered | failed | in_transit"),
  qty_delivered INT64 NOT NULL
)
PARTITION BY RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261101, 100))
CLUSTER BY warehouse_id, carrier_id
OPTIONS(
  description="Silver: entregas limpias — integer-range en fecha_id — cluster carrier."
);

CREATE TABLE IF NOT EXISTS `silver_kora.quarantine_rows` (
  source_table STRING NOT NULL OPTIONS(description="Entidad bronze de origen"),
  natural_key STRING NOT NULL OPTIONS(description="PK lógica / clave natural"),
  reason STRING NOT NULL OPTIONS(description="missing_fk_* | null_required_* | invalid_status | …"),
  payload STRING OPTIONS(description="JSON snapshot de la fila rechazada"),
  quarantine_ts TIMESTAMP NOT NULL
)
OPTIONS(
  description="Quarantine silver (Paso 6): rejects de calidad; no drop silencioso. Lab KORA."
);
