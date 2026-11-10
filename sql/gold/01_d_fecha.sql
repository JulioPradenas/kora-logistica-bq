-- Dim fecha: calendario generado (horizonte lab 2025-04-01 → 2026-10-31).
-- No depende de silver; PK fecha_id YYYYMMDD. Alineado a ddl_gold (NOT NULL).
CREATE OR REPLACE TABLE `gold_kora.d_fecha` AS
SELECT
  CAST(FORMAT_DATE('%Y%m%d', d) AS INT64) AS fecha_id,
  d AS fecha,
  EXTRACT(YEAR FROM d) AS anio,
  EXTRACT(MONTH FROM d) AS mes,
  EXTRACT(DAY FROM d) AS dia,
  -- BigQuery DAYOFWEEK: 1=domingo … 7=sábado → ISO lab: 1=lunes … 7=domingo.
  CASE EXTRACT(DAYOFWEEK FROM d)
    WHEN 1 THEN 7
    ELSE EXTRACT(DAYOFWEEK FROM d) - 1
  END AS dia_semana,
  CASE EXTRACT(MONTH FROM d)
    WHEN 1 THEN 'enero'
    WHEN 2 THEN 'febrero'
    WHEN 3 THEN 'marzo'
    WHEN 4 THEN 'abril'
    WHEN 5 THEN 'mayo'
    WHEN 6 THEN 'junio'
    WHEN 7 THEN 'julio'
    WHEN 8 THEN 'agosto'
    WHEN 9 THEN 'septiembre'
    WHEN 10 THEN 'octubre'
    WHEN 11 THEN 'noviembre'
    WHEN 12 THEN 'diciembre'
  END AS nombre_mes,
  EXTRACT(DAYOFWEEK FROM d) IN (1, 7) AS es_fin_de_semana
FROM UNNEST(GENERATE_DATE_ARRAY(DATE '2025-04-01', DATE '2026-10-31', INTERVAL 1 DAY)) AS d;
