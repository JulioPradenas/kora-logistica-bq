-- Fact movimientos: grano día × bodega × SKU × movement_type.
-- CTAS re-declara PARTITION / CLUSTER (Paso 4). Sandbox: sin DML.
CREATE OR REPLACE TABLE `gold_kora.f_movimientos_bodega`
PARTITION BY RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261101, 100))
CLUSTER BY bodega_id, sku_id
AS
SELECT
  fecha_id,
  warehouse_id AS bodega_id,
  sku_id,
  movement_type,
  SUM(qty) AS qty,
  SUM(qty_ordered) AS qty_ordered,
  COUNT(*) AS n_movimientos
FROM `silver_kora.sap_wms_movimientos`
GROUP BY fecha_id, warehouse_id, sku_id, movement_type;
