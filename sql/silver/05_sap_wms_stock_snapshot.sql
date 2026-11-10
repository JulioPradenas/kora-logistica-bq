-- Silver stock: dedup (snapshot_date, warehouse_id, sku_id), usable_qty, FK vs silver.
CREATE OR REPLACE TABLE `silver_kora.sap_wms_stock_snapshot`
PARTITION BY RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261101, 100))
CLUSTER BY warehouse_id, sku_id
AS
WITH deduped AS (
  SELECT * EXCEPT (rn)
  FROM (
    SELECT
      SAFE.PARSE_DATE('%Y-%m-%d', CAST(snapshot_date AS STRING)) AS snapshot_date,
      TRIM(warehouse_id) AS warehouse_id,
      TRIM(sku_id) AS sku_id,
      CAST(on_hand_qty AS INT64) AS on_hand_qty,
      CAST(reserved_qty AS INT64) AS reserved_qty,
      ROW_NUMBER() OVER (
        PARTITION BY
          TRIM(warehouse_id),
          TRIM(sku_id),
          CAST(snapshot_date AS STRING)
        ORDER BY CAST(on_hand_qty AS INT64)
      ) AS rn
    FROM `bronze_kora.sap_wms_stock_snapshot`
  )
  WHERE rn = 1
)
SELECT
  CAST(FORMAT_DATE('%Y%m%d', d.snapshot_date) AS INT64) AS fecha_id,
  d.snapshot_date,
  d.warehouse_id,
  d.sku_id,
  d.on_hand_qty,
  LEAST(d.reserved_qty, d.on_hand_qty) AS reserved_qty,
  d.on_hand_qty - LEAST(d.reserved_qty, d.on_hand_qty) AS usable_qty
FROM deduped d
INNER JOIN `silver_kora.md_bodega` w ON d.warehouse_id = w.warehouse_id
INNER JOIN `silver_kora.md_sku` s ON d.sku_id = s.sku_id
WHERE d.snapshot_date IS NOT NULL
  AND d.warehouse_id IS NOT NULL
  AND d.warehouse_id != ''
  AND d.sku_id IS NOT NULL
  AND d.sku_id != ''
  AND d.on_hand_qty >= 0
  AND d.reserved_qty >= 0;
