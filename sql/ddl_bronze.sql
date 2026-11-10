-- KORA Deportes — Paso 4/6: DDL bronze (landing tipado, vacío).
-- Proyecto: kora-sports · dataset: bronze_kora · location: southamerica-west1
-- Sandbox: CREATE TABLE IF NOT EXISTS (sin DML). Carga de datos = Paso 5.
-- Columnas = data/README.md + scripts/generate_synthetic.py (contrato CSV).
-- Sin partición: el contrato bronze usa DATE/DATETIME nativos; partición
-- integer-range vive en silver/gold (fecha_id). Ver docs/paso-04-ddl.md.
--
-- Paso 6: TODAS las columnas son NULLABLE (landing permisivo).
-- Nulls / vacíos de anomalías SAP-like deben poder cargar; silver + quarantine
-- aplican la estrictitud. Tablas ya creadas con NOT NULL se relajan en
-- scripts/apply_ddl.py vía ALTER COLUMN … DROP NOT NULL (idempotente).

CREATE TABLE IF NOT EXISTS `bronze_kora.md_bodega` (
  warehouse_id STRING OPTIONS(description="PK lógica WH-##"),
  warehouse_name STRING,
  region_code STRING,
  city STRING,
  is_active BOOL
)
OPTIONS(
  description="Bronze: maestro bodegas / CDs (export WMS-like). Lab KORA — landing NULLABLE."
);

CREATE TABLE IF NOT EXISTS `bronze_kora.md_sku` (
  sku_id STRING OPTIONS(description="PK lógica SKU-#####"),
  sku_name STRING,
  category STRING,
  brand STRING,
  unit_cost_clp INT64
)
OPTIONS(
  description="Bronze: maestro materiales / SKU. Lab KORA — landing NULLABLE."
);

CREATE TABLE IF NOT EXISTS `bronze_kora.md_transportista` (
  carrier_id STRING OPTIONS(description="PK lógica CAR-##"),
  carrier_name STRING,
  service_level STRING OPTIONS(description="STD | EXP")
)
OPTIONS(
  description="Bronze: maestro transportistas / 3PL. Lab KORA — landing NULLABLE."
);

CREATE TABLE IF NOT EXISTS `bronze_kora.sap_wms_movimientos` (
  movement_id STRING OPTIONS(description="PK surrogada MOV-########"),
  event_date DATE,
  warehouse_id STRING,
  sku_id STRING,
  movement_code STRING OPTIONS(description="SAP-like: 101, 601, 701, 311, 312"),
  movement_type STRING OPTIONS(description="IN | OUT | ADJ"),
  qty INT64,
  qty_ordered INT64 OPTIONS(description="Nullable — OUT con pedido"),
  order_id STRING OPTIONS(description="Puente a entrega"),
  shipment_id STRING OPTIONS(description="Puente a entrega"),
  pick_start_ts DATETIME,
  pick_end_ts DATETIME
)
OPTIONS(
  description="Bronze: movimientos WMS (grano 1 movimiento). Landing NULLABLE — nulls → quarantine silver."
);

CREATE TABLE IF NOT EXISTS `bronze_kora.sap_wms_stock_snapshot` (
  snapshot_date DATE,
  warehouse_id STRING,
  sku_id STRING,
  on_hand_qty INT64,
  reserved_qty INT64
)
OPTIONS(
  description="Bronze: snapshot stock diario warehouse×sku. Landing NULLABLE — nulls → quarantine silver."
);

CREATE TABLE IF NOT EXISTS `bronze_kora.sap_tm_entregas` (
  shipment_id STRING,
  order_id STRING,
  warehouse_id STRING,
  carrier_id STRING,
  comuna_id STRING,
  comuna_name STRING,
  ship_date DATE,
  delivery_date DATE,
  eta_ts DATETIME,
  ata_ts DATETIME OPTIONS(description="Nullable si in_transit"),
  status STRING OPTIONS(description="delivered | late_delivered | failed | in_transit"),
  qty_delivered INT64
)
OPTIONS(
  description="Bronze: entregas last-mile / TM (1 shipment). Landing NULLABLE — nulls → quarantine silver."
);
