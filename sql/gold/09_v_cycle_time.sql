-- Vista cycle time: despacho/pick → entrega (ATA).
-- KPI §5: cycle time E2E ≈ ATA_entrega − pick_start (mismo order_id / shipment_id).
-- Grano reporte: bodega × semana (aquí: fila por shipment con métricas; Looker agrega).
-- Excluye in_transit (sin ATA) y pedidos sin pick_start (fuera de cycle time E2E).
CREATE OR REPLACE VIEW `gold_kora.v_cycle_time` AS
WITH pick AS (
  SELECT
    shipment_id,
    order_id,
    warehouse_id AS bodega_id,
    MIN(pick_start_ts) AS pick_start_ts,
    MAX(pick_end_ts) AS pick_end_ts
  FROM `silver_kora.sap_wms_movimientos`
  WHERE movement_type = 'OUT'
    AND shipment_id IS NOT NULL
    AND shipment_id != ''
    AND pick_start_ts IS NOT NULL
  GROUP BY shipment_id, order_id, warehouse_id
)
SELECT
  e.fecha_id,
  e.shipment_id,
  e.order_id,
  e.bodega_id,
  e.transportista_id,
  e.status,
  p.pick_start_ts,
  p.pick_end_ts,
  e.ship_fecha_id,
  e.eta_ts,
  e.ata_ts,
  DATETIME_DIFF(e.ata_ts, p.pick_start_ts, HOUR) AS cycle_hours_pick_to_ata,
  DATETIME_DIFF(e.ata_ts, p.pick_end_ts, HOUR) AS cycle_hours_dispatch_to_ata,
  e.on_time,
  e.in_full
FROM `gold_kora.f_entregas` e
INNER JOIN pick p
  ON e.shipment_id = p.shipment_id
 AND e.order_id = p.order_id
 AND e.bodega_id = p.bodega_id
WHERE e.ata_ts IS NOT NULL
  AND e.status IN ('delivered', 'late_delivered', 'failed');
