-- KORA Deportes — Paso 4: DDL gold star (dm_logistica_retail conceptual, vacío).
-- Dataset: gold_kora. Dims + facts. Sin carga (Paso 5+) ni transforms (Paso 7).
-- Partición facts: integer-range en fecha_id INT64 YYYYMMDD (NO DATE).
-- Motivo sandbox: partition expiration ~60d aplica a time-unit/ingestion partitions
-- y borraría historia Apr-2025–Oct-2026; integer-range NO soporta partition expiration
-- (https://cloud.google.com/bigquery/docs/managing-partitioned-tables).
-- Intervalo 100 ≈ mensual en encoding YYYYMMDD; daily (1) excedería 10k partitions.
-- CREATE TABLE IF NOT EXISTS (sandbox sin DML).

-- ========== DIMENSIONES ==========

CREATE TABLE IF NOT EXISTS `gold_kora.d_fecha` (
  fecha_id INT64 NOT NULL OPTIONS(description="PK YYYYMMDD"),
  fecha DATE NOT NULL,
  anio INT64 NOT NULL,
  mes INT64 NOT NULL,
  dia INT64 NOT NULL,
  dia_semana INT64 NOT NULL OPTIONS(description="1=lunes … 7=domingo (ISO)"),
  nombre_mes STRING NOT NULL,
  es_fin_de_semana BOOL NOT NULL
)
OPTIONS(
  description="Dim fecha del datamart. Lab KORA — horizonte 2025-04-01→2026-10-31."
);

CREATE TABLE IF NOT EXISTS `gold_kora.d_bodega` (
  bodega_id STRING NOT NULL OPTIONS(description="PK = warehouse_id canónico"),
  bodega_name STRING NOT NULL,
  region_code STRING NOT NULL,
  city STRING NOT NULL,
  is_active BOOL NOT NULL
)
OPTIONS(
  description="Dim bodega / CD. Lab KORA."
);

CREATE TABLE IF NOT EXISTS `gold_kora.d_sku` (
  sku_id STRING NOT NULL,
  sku_name STRING NOT NULL,
  category STRING NOT NULL,
  brand STRING NOT NULL,
  unit_cost_clp INT64 NOT NULL
)
OPTIONS(
  description="Dim SKU / material. Lab KORA."
);

CREATE TABLE IF NOT EXISTS `gold_kora.d_transportista` (
  transportista_id STRING NOT NULL OPTIONS(description="PK = carrier_id canónico"),
  transportista_name STRING NOT NULL,
  service_level STRING NOT NULL
)
OPTIONS(
  description="Dim transportista / 3PL. Lab KORA."
);

-- ========== HECHOS (integer-range + clustering) ==========

CREATE TABLE IF NOT EXISTS `gold_kora.f_movimientos_bodega` (
  fecha_id INT64 NOT NULL OPTIONS(description="FK d_fecha — partición YYYYMMDD"),
  bodega_id STRING NOT NULL OPTIONS(description="FK d_bodega"),
  sku_id STRING NOT NULL OPTIONS(description="FK d_sku"),
  movement_type STRING NOT NULL OPTIONS(description="IN | OUT | ADJ"),
  qty INT64 NOT NULL OPTIONS(description="Suma qty en el grano"),
  qty_ordered INT64 OPTIONS(description="Suma qty_ordered cuando aplica"),
  n_movimientos INT64 NOT NULL OPTIONS(description="Conteo de movimientos en el grano")
)
PARTITION BY RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261101, 100))
CLUSTER BY bodega_id, sku_id
OPTIONS(
  description="Fact: warehouse×sku×movement_type×día. Integer-range mensual en fecha_id."
);

CREATE TABLE IF NOT EXISTS `gold_kora.f_stock_diario` (
  fecha_id INT64 NOT NULL OPTIONS(description="FK d_fecha — partición"),
  bodega_id STRING NOT NULL,
  sku_id STRING NOT NULL,
  on_hand_qty INT64 NOT NULL,
  reserved_qty INT64 NOT NULL,
  usable_qty INT64 NOT NULL OPTIONS(description="on_hand - reserved")
)
PARTITION BY RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261101, 100))
CLUSTER BY bodega_id, sku_id
OPTIONS(
  description="Fact: stock diario warehouse×sku×día. Integer-range mensual en fecha_id."
);

CREATE TABLE IF NOT EXISTS `gold_kora.f_entregas` (
  fecha_id INT64 NOT NULL OPTIONS(description="FK d_fecha = delivery_date YYYYMMDD"),
  shipment_id STRING NOT NULL,
  order_id STRING NOT NULL,
  bodega_id STRING NOT NULL,
  transportista_id STRING NOT NULL OPTIONS(description="FK d_transportista"),
  comuna_id STRING NOT NULL,
  comuna_name STRING NOT NULL,
  ship_fecha_id INT64 NOT NULL OPTIONS(description="ship_date como YYYYMMDD"),
  eta_ts DATETIME NOT NULL,
  ata_ts DATETIME,
  status STRING NOT NULL OPTIONS(description="delivered | late_delivered | failed | in_transit"),
  qty_delivered INT64 NOT NULL,
  on_time BOOL NOT NULL OPTIONS(description="ATA IS NOT NULL AND ATA <= ETA"),
  in_full BOOL NOT NULL OPTIONS(description="qty_delivered >= qty_ordered WMS OUT (shipment_id); FALSE si sin puente")
)
PARTITION BY RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261101, 100))
CLUSTER BY bodega_id, transportista_id
OPTIONS(
  description="Fact: 1 shipment × día entrega/intento. Integer-range — cluster transportista."
);
