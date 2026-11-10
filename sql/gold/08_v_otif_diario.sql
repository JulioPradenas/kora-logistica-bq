-- Vista OTIF diario (KPI §5 levantamiento): on-time delivered / intentadas cerradas.
-- Denominador: excluye in_transit (OTIF cerrado del día).
-- Numerador doc: status = delivered AND ATA <= ETA (equiv. on_time en este lab).
-- in_full se expone aparte; NO se inventa umbral SLA adicional.
CREATE OR REPLACE VIEW `gold_kora.v_otif_diario` AS
SELECT
  f.fecha_id,
  d.fecha,
  f.bodega_id,
  f.transportista_id,
  f.comuna_id,
  COUNT(*) AS n_intentadas_cerradas,
  COUNTIF(f.status = 'delivered' AND f.on_time) AS n_otif,
  SAFE_DIVIDE(
    COUNTIF(f.status = 'delivered' AND f.on_time),
    COUNT(*)
  ) AS otif_pct,
  COUNTIF(f.on_time AND f.in_full AND f.status IN ('delivered', 'late_delivered')) AS n_otif_con_in_full,
  COUNTIF(f.ata_ts IS NOT NULL AND f.ata_ts > f.eta_ts) AS n_late,
  COUNTIF(f.in_full) AS n_in_full
FROM `gold_kora.f_entregas` f
INNER JOIN `gold_kora.d_fecha` d ON f.fecha_id = d.fecha_id
WHERE f.status IN ('delivered', 'late_delivered', 'failed')
GROUP BY f.fecha_id, d.fecha, f.bodega_id, f.transportista_id, f.comuna_id;
