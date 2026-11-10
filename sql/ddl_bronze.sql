-- KORA Deportes — Paso 4: DDL bronze (landing tipado, vacío).
-- Proyecto: kora-sports · dataset: bronze_kora · location: southamerica-west1
-- Sandbox: CREATE TABLE IF NOT EXISTS (sin DML). Carga de datos = Paso 5.
-- Columnas = data/README.md + scripts/generate_synthetic.py (contrato CSV).
-- Sin partición: el contrato bronze usa DATE/DATETIME nativos; partición
-- integer-range vive en silver/gold (fecha_id). Ver docs/paso-04-ddl.md.

CREATE TABLE IF NOT EXISTS `bronze_kora.md_bodega` (
  warehouse_id STRING NOT NULL OPTIONS(description="PK lógica WH-##"),
  warehouse_name STRING NOT NULL,
  region_code STRING NOT NULL,
  city STRING NOT NULL,
  is_active BOOL NOT NULL
)
OPTIONS(
  description="Bronze: maestro bodegas / CDs (export WMS-like). Lab KORA — sin PII real."
);

CREATE TABLE IF NOT EXISTS `bronze_kora.md_sku` (
  sku_id STRING NOT NULL OPTIONS(description="PK lógica SKU-#####"),
  sku_name STRING NOT NULL,
  category STRING NOT NULL,
  brand STRING NOT NULL,
  unit_cost_clp INT64 NOT NULL
)
OPTIONS(
  description="Bronze: maestro materiales / SKU. Lab KORA — sin PII real."
);

CREATE TABLE IF NOT EXISTS `bronze_kora.md_transportista` (
  carrier_id STRING NOT NULL OPTIONS(description="PK lógica CAR-##"),
  carrier_name STRING NOT NULL,
  service_level STRING NOT NULL OPTIONS(description="STD | EXP")
)
OPTIONS(
  description="Bronze: maestro transportistas / 3PL. Lab KORA — sin PII real."
);

CREATE TABLE IF NOT EXISTS `bronze_kora.sap_wms_movimientos` (
  movement_id STRING NOT NULL OPTIONS(description="PK surrogada MOV-########"),
  event_date DATE NOT NULL,
  warehouse_id STRING NOT NULL,
  sku_id STRING NOT NULL,
  movement_code STRING NOT NULL OPTIONS(description="SAP-like: 101, 601, 701, 311, 312"),
  movement_type STRING NOT NULL OPTIONS(description="IN | OUT | ADJ"),
  qty INT64 NOT NULL,
  qty_ordered INT64 OPTIONS(description="Nullable — OUT con pedido"),
  order_id STRING OPTIONS(description="Puente a entrega"),
  shipment_id STRING OPTIONS(description="Puente a entrega"),
  pick_start_ts DATETIME,
  pick_end_ts DATETIME
)
OPTIONS(
  description="Bronze: movimientos WMS (grano 1 movimiento). Lab KORA — sin PII real."
);

CREATE TABLE IF NOT EXISTS `bronze_kora.sap_wms_stock_snapshot` (
  snapshot_date DATE NOT NULL,
  warehouse_id STRING NOT NULL,
  sku_id STRING NOT NULL,
  on_hand_qty INT64 NOT NULL,
  reserved_qty INT64 NOT NULL
)
OPTIONS(
  description="Bronze: snapshot stock diario warehouse×sku. Lab KORA — sin PII real."
);

CREATE TABLE IF NOT EXISTS `bronze_kora.sap_tm_entregas` (
  shipment_id STRING NOT NULL,
  order_id STRING NOT NULL,
  warehouse_id STRING NOT NULL,
  carrier_id STRING NOT NULL,
  comuna_id STRING NOT NULL,
  comuna_name STRING NOT NULL,
  ship_date DATE NOT NULL,
  delivery_date DATE NOT NULL,
  eta_ts DATETIME NOT NULL,
  ata_ts DATETIME OPTIONS(description="Nullable si in_transit"),
  status STRING NOT NULL OPTIONS(description="delivered | late_delivered | failed | in_transit"),
  qty_delivered INT64 NOT NULL
)
OPTIONS(
  description="Bronze: entregas last-mile / TM (1 shipment). Lab KORA — sin PII real."
);
