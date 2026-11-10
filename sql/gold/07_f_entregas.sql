-- Fact entregas: 1 fila por shipment (día de entrega/intento).
-- on_time / in_full: flags BOOL NOT NULL (DDL gold). Asunciones en docs/paso-07-gold.md.
-- CTAS re-declara PARTITION / CLUSTER.
CREATE OR REPLACE TABLE `gold_kora.f_entregas`
PARTITION BY RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261201, 100))
CLUSTER BY bodega_id, transportista_id
AS
WITH out_by_shipment AS (
  SELECT
    shipment_id,
    SUM(qty) AS qty_picked,
    SUM(qty_ordered) AS qty_ordered
  FROM `silver_kora.sap_wms_movimientos`
  WHERE movement_type = 'OUT'
    AND shipment_id IS NOT NULL
    AND shipment_id != ''
  GROUP BY shipment_id
)
SELECT
  e.fecha_id,
  e.shipment_id,
  e.order_id,
  e.warehouse_id AS bodega_id,
  e.carrier_id AS transportista_id,
  e.comuna_id,
  e.comuna_name,
  CAST(FORMAT_DATE('%Y%m%d', e.ship_date) AS INT64) AS ship_fecha_id,
  e.eta_ts,
  e.ata_ts,
  e.status,
  e.qty_delivered,
  -- on_time: ATA presente y ATA <= ETA (FALSE si late / failed sin ATA a tiempo / in_transit).
  IF(e.ata_ts IS NOT NULL AND e.ata_ts <= e.eta_ts, TRUE, FALSE) AS on_time,
  -- in_full: qty_delivered >= qty_ordered del OUT WMS (puente shipment_id).
  -- Sin puente o sin qty_ordered → FALSE (no inventamos SLA; asunción documentada).
  IF(
    o.qty_ordered IS NOT NULL AND e.qty_delivered >= o.qty_ordered,
    TRUE,
    FALSE
  ) AS in_full
FROM `silver_kora.sap_tm_entregas` e
LEFT JOIN out_by_shipment o ON e.shipment_id = o.shipment_id;
