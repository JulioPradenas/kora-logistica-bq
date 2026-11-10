-- Heatmap bodega × semana (MVP Looker §10.2).
-- Métricas reales: late_rate (f_entregas) + lead time pick (silver OUT con pick_*).
-- Semana = ISO week (lunes) vía d_fecha; sin inventar dims.
CREATE OR REPLACE VIEW `gold_kora.v_heatmap_bodega_semana` AS
WITH pick_by_shipment AS (
  SELECT
    shipment_id,
    warehouse_id AS bodega_id,
    MIN(pick_start_ts) AS pick_start_ts,
    MAX(pick_end_ts) AS pick_end_ts
  FROM `silver_kora.sap_wms_movimientos`
  WHERE movement_type = 'OUT'
    AND shipment_id IS NOT NULL
    AND shipment_id != ''
    AND pick_start_ts IS NOT NULL
  GROUP BY shipment_id, warehouse_id
),
cerradas AS (
  SELECT
    e.bodega_id,
    e.fecha_id,
    e.shipment_id,
    e.status,
    e.eta_ts,
    e.ata_ts,
    IF(e.ata_ts IS NOT NULL AND e.ata_ts > e.eta_ts, TRUE, FALSE) AS is_late,
    p.pick_start_ts,
    p.pick_end_ts,
    IF(
      p.pick_start_ts IS NOT NULL AND p.pick_end_ts IS NOT NULL,
      DATETIME_DIFF(p.pick_end_ts, p.pick_start_ts, MINUTE),
      NULL
    ) AS pick_lead_minutes
  FROM `gold_kora.f_entregas` e
  LEFT JOIN pick_by_shipment p
    ON e.shipment_id = p.shipment_id
   AND e.bodega_id = p.bodega_id
  WHERE e.status IN ('delivered', 'late_delivered', 'failed')
)
SELECT
  c.bodega_id,
  b.bodega_name,
  d.anio,
  EXTRACT(ISOWEEK FROM d.fecha) AS semana_iso,
  DATE_TRUNC(d.fecha, WEEK(MONDAY)) AS semana_inicio,
  COUNT(*) AS n_cerradas,
  COUNTIF(c.is_late) AS n_late,
  SAFE_DIVIDE(COUNTIF(c.is_late), COUNT(*)) AS late_rate,
  COUNTIF(c.pick_lead_minutes IS NOT NULL) AS n_con_pick,
  AVG(c.pick_lead_minutes) AS avg_pick_lead_minutes,
  APPROX_QUANTILES(c.pick_lead_minutes, 100)[OFFSET(50)] AS p50_pick_lead_minutes
FROM cerradas c
INNER JOIN `gold_kora.d_fecha` d ON c.fecha_id = d.fecha_id
INNER JOIN `gold_kora.d_bodega` b ON c.bodega_id = b.bodega_id
GROUP BY
  c.bodega_id,
  b.bodega_name,
  d.anio,
  semana_iso,
  semana_inicio;
