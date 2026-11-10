-- Paso 8 — demo partition / cluster pruning on gold_kora.f_stock_diario
-- Tres queries para comparar bytes (dry_run via scripts/demo_partition_prune.py).
-- (a) sin filtro de partición  (b) un mes fecha_id  (c) mes + bodega_id (clustering)
--
-- Nota: dry_run estima por partición (mín. ~10 MB facturado en jobs reales pequeños);
-- el ahorro de CLUSTER BY suele NO verse en dry_run. Opcional: job real solo para (c).

-- === QUERY_A: full scan (baseline; sin filtro fecha_id) ===
SELECT
  COUNT(*) AS n_rows,
  SUM(on_hand_qty) AS sum_on_hand
FROM `gold_kora.f_stock_diario`;

-- === QUERY_B: partition prune — un mes (YYYYMMDD range ≈ Oct 2026) ===
SELECT
  COUNT(*) AS n_rows,
  SUM(on_hand_qty) AS sum_on_hand
FROM `gold_kora.f_stock_diario`
WHERE fecha_id >= 20261001
  AND fecha_id < 20261101;

-- === QUERY_C: partition prune + cluster filter (bodega_id) ===
SELECT
  COUNT(*) AS n_rows,
  SUM(on_hand_qty) AS sum_on_hand
FROM `gold_kora.f_stock_diario`
WHERE fecha_id >= 20261001
  AND fecha_id < 20261101
  AND bodega_id = 'WH-SCL-01';
