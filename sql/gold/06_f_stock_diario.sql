-- Fact stock diario: grano día × bodega × SKU (1:1 con silver snapshot limpio).
-- CTAS re-declara PARTITION / CLUSTER.
CREATE OR REPLACE TABLE `gold_kora.f_stock_diario`
PARTITION BY RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261101, 100))
CLUSTER BY bodega_id, sku_id
AS
SELECT
  fecha_id,
  warehouse_id AS bodega_id,
  sku_id,
  on_hand_qty,
  reserved_qty,
  usable_qty
FROM `silver_kora.sap_wms_stock_snapshot`;
