-- Funnel logístico Looker §10.3 (picking → despacho → entrega).
--
-- GAP DE SCHEMA (no inventar columnas):
--   - No existe stage / estado_proceso / funnel_stage en silver ni gold.
--   - No hay tabla de eventos de etapa; solo timestamps y status canónicos.
--
-- Funnel HONESTO (funnel_kind = shipment_e2e), columnas reales:
--   1_picking  = OUT WMS con shipment_id + pick_start_ts
--   2_despacho = f_entregas (ship_fecha_id siempre NOT NULL en contrato)
--   3_entrega  = status IN (delivered, late_delivered)
--
-- Alternativa volume (funnel_kind = movement_type | status):
--   conteos por movement_type (IN/OUT/ADJ) y por status TM — no es conversión
--   secuencial E2E; sirve si el bridge shipment no alcanza o se quiere volumen.
CREATE OR REPLACE VIEW `gold_kora.v_funnel_logistico` AS
WITH shipment_e2e AS (
  SELECT
    'shipment_e2e' AS funnel_kind,
    stage_code,
    stage_label,
    n_shipments
  FROM (
    SELECT
      '1_picking' AS stage_code,
      'picking' AS stage_label,
      COUNT(DISTINCT shipment_id) AS n_shipments
    FROM `silver_kora.sap_wms_movimientos`
    WHERE movement_type = 'OUT'
      AND shipment_id IS NOT NULL
      AND shipment_id != ''
      AND pick_start_ts IS NOT NULL

    UNION ALL

    SELECT
      '2_despacho',
      'despacho',
      COUNT(DISTINCT shipment_id)
    FROM `gold_kora.f_entregas`

    UNION ALL

    SELECT
      '3_entrega',
      'entrega',
      COUNT(DISTINCT shipment_id)
    FROM `gold_kora.f_entregas`
    WHERE status IN ('delivered', 'late_delivered')
  )
),
by_movement_type AS (
  SELECT
    'movement_type' AS funnel_kind,
    CONCAT('m_', LOWER(movement_type)) AS stage_code,
    movement_type AS stage_label,
    SUM(n_movimientos) AS n_shipments
  FROM `gold_kora.f_movimientos_bodega`
  GROUP BY movement_type
),
by_status AS (
  SELECT
    'status' AS funnel_kind,
    CONCAT('s_', status) AS stage_code,
    status AS stage_label,
    COUNT(*) AS n_shipments
  FROM `gold_kora.f_entregas`
  GROUP BY status
)
SELECT funnel_kind, stage_code, stage_label, n_shipments FROM shipment_e2e
UNION ALL
SELECT funnel_kind, stage_code, stage_label, n_shipments FROM by_movement_type
UNION ALL
SELECT funnel_kind, stage_code, stage_label, n_shipments FROM by_status;
