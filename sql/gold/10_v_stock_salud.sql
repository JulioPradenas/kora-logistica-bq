-- Vista stock salud: stockouts / cobertura (DOH 7d) / overstock heurístico.
-- KPI §5: on-hand/reserved/usable; DOH = usable / avg salida diaria 7d.
-- ASUNCIÓN LAB (no hay SLA de overstock en el workshop): is_overstock = doh_7d > 45.
-- No inventar umbral de negocio; ver docs/paso-07-gold.md.
CREATE OR REPLACE VIEW `gold_kora.v_stock_salud` AS
WITH out_daily AS (
  SELECT
    fecha_id,
    bodega_id,
    sku_id,
    SUM(IF(movement_type = 'OUT', qty, 0)) AS out_qty
  FROM `gold_kora.f_movimientos_bodega`
  GROUP BY fecha_id, bodega_id, sku_id
),
stock AS (
  SELECT
    s.fecha_id,
    s.bodega_id,
    s.sku_id,
    s.on_hand_qty,
    s.reserved_qty,
    s.usable_qty,
    d.fecha
  FROM `gold_kora.f_stock_diario` s
  INNER JOIN `gold_kora.d_fecha` d ON s.fecha_id = d.fecha_id
),
avg7 AS (
  SELECT
    st.fecha_id,
    st.bodega_id,
    st.sku_id,
    -- Promedio sobre 7 días de calendario (ceros si no hubo OUT).
    SUM(COALESCE(o.out_qty, 0)) / 7.0 AS avg_out_7d
  FROM stock st
  INNER JOIN `gold_kora.d_fecha` dwin
    ON dwin.fecha BETWEEN DATE_SUB(st.fecha, INTERVAL 6 DAY) AND st.fecha
  LEFT JOIN out_daily o
    ON o.fecha_id = dwin.fecha_id
   AND o.bodega_id = st.bodega_id
   AND o.sku_id = st.sku_id
  GROUP BY st.fecha_id, st.bodega_id, st.sku_id
)
SELECT
  st.fecha_id,
  st.fecha,
  st.bodega_id,
  st.sku_id,
  st.on_hand_qty,
  st.reserved_qty,
  st.usable_qty,
  (st.usable_qty = 0) AS is_stockout,
  a.avg_out_7d,
  SAFE_DIVIDE(st.usable_qty, NULLIF(a.avg_out_7d, 0)) AS doh_7d,
  -- Heurística de demo (ASUNCIÓN; no SLA stakeholder): DOH > 45 días.
  IF(
    SAFE_DIVIDE(st.usable_qty, NULLIF(a.avg_out_7d, 0)) > 45,
    TRUE,
    FALSE
  ) AS is_overstock
FROM stock st
LEFT JOIN avg7 a
  ON st.fecha_id = a.fecha_id
 AND st.bodega_id = a.bodega_id
 AND st.sku_id = a.sku_id;
