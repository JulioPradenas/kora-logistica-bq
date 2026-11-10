-- Drill Looker §10.4: comuna / SKU / transportista.
--
-- Comuna: SÍ existe en gold (f_entregas.comuna_id / comuna_name).
--   GAP: levantamiento §7 nombra d_comuna_destino — NO materializada;
--   comuna vive desnormalizada en el fact (sin inventar dim).
-- SKU: f_entregas NO tiene sku_id; se obtiene del OUT WMS vía shipment_id.
--   Grano: shipment × sku (fan-out si un shipment tiene varios SKU).
-- Transportista: f_entregas.transportista_id + d_transportista.
CREATE OR REPLACE VIEW `gold_kora.v_entregas_drill` AS
WITH sku_by_shipment AS (
  SELECT
    shipment_id,
    sku_id,
    SUM(qty) AS qty_out,
    SUM(qty_ordered) AS qty_ordered
  FROM `silver_kora.sap_wms_movimientos`
  WHERE movement_type = 'OUT'
    AND shipment_id IS NOT NULL
    AND shipment_id != ''
  GROUP BY shipment_id, sku_id
)
SELECT
  e.fecha_id,
  d.fecha,
  e.shipment_id,
  e.order_id,
  e.bodega_id,
  b.bodega_name,
  e.transportista_id,
  t.transportista_name,
  t.service_level,
  e.comuna_id,
  e.comuna_name,
  s.sku_id,
  sk.sku_name,
  sk.category,
  sk.brand,
  e.status,
  e.on_time,
  e.in_full,
  e.qty_delivered,
  s.qty_out,
  s.qty_ordered,
  e.ship_fecha_id,
  e.eta_ts,
  e.ata_ts
FROM `gold_kora.f_entregas` e
INNER JOIN `gold_kora.d_fecha` d ON e.fecha_id = d.fecha_id
INNER JOIN `gold_kora.d_bodega` b ON e.bodega_id = b.bodega_id
INNER JOIN `gold_kora.d_transportista` t ON e.transportista_id = t.transportista_id
LEFT JOIN sku_by_shipment s ON e.shipment_id = s.shipment_id
LEFT JOIN `gold_kora.d_sku` sk ON s.sku_id = sk.sku_id;
