-- Silver movimientos: dedup movement_id, tipado, status/type canónico, FK vs silver masters.
-- Re-declara PARTITION/CLUSTER (CTAS los perdería si no).
CREATE OR REPLACE TABLE `silver_kora.sap_wms_movimientos`
PARTITION BY RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261201, 100))
CLUSTER BY warehouse_id, sku_id
AS
WITH deduped AS (
  SELECT * EXCEPT (rn)
  FROM (
    SELECT
      TRIM(movement_id) AS movement_id,
      SAFE.PARSE_DATE('%Y-%m-%d', CAST(event_date AS STRING)) AS event_date,
      TRIM(warehouse_id) AS warehouse_id,
      TRIM(sku_id) AS sku_id,
      TRIM(CAST(movement_code AS STRING)) AS movement_code,
      CASE UPPER(TRIM(CAST(movement_type AS STRING)))
        WHEN 'IN' THEN 'IN'
        WHEN 'OUT' THEN 'OUT'
        WHEN 'ADJ' THEN 'ADJ'
        ELSE UPPER(TRIM(CAST(movement_type AS STRING)))
      END AS movement_type,
      CAST(qty AS INT64) AS qty,
      SAFE_CAST(NULLIF(TRIM(CAST(qty_ordered AS STRING)), '') AS INT64) AS qty_ordered,
      NULLIF(TRIM(CAST(order_id AS STRING)), '') AS order_id,
      NULLIF(TRIM(CAST(shipment_id AS STRING)), '') AS shipment_id,
      SAFE.PARSE_DATETIME('%Y-%m-%d %H:%M:%S', NULLIF(TRIM(CAST(pick_start_ts AS STRING)), '')) AS pick_start_ts,
      SAFE.PARSE_DATETIME('%Y-%m-%d %H:%M:%S', NULLIF(TRIM(CAST(pick_end_ts AS STRING)), '')) AS pick_end_ts,
      ROW_NUMBER() OVER (
        PARTITION BY TRIM(movement_id)
        ORDER BY
          SAFE.PARSE_DATE('%Y-%m-%d', CAST(event_date AS STRING)) NULLS LAST,
          CAST(qty AS INT64)
      ) AS rn
    FROM `bronze_kora.sap_wms_movimientos`
  )
  WHERE rn = 1
)
SELECT
  d.movement_id,
  CAST(FORMAT_DATE('%Y%m%d', d.event_date) AS INT64) AS fecha_id,
  d.event_date,
  d.warehouse_id,
  d.sku_id,
  d.movement_code,
  d.movement_type,
  d.qty,
  d.qty_ordered,
  d.order_id,
  d.shipment_id,
  d.pick_start_ts,
  d.pick_end_ts
FROM deduped d
INNER JOIN `silver_kora.md_bodega` w ON d.warehouse_id = w.warehouse_id
INNER JOIN `silver_kora.md_sku` s ON d.sku_id = s.sku_id
WHERE d.movement_id IS NOT NULL
  AND d.movement_id != ''
  AND d.event_date IS NOT NULL
  AND d.warehouse_id IS NOT NULL
  AND d.warehouse_id != ''
  AND d.sku_id IS NOT NULL
  AND d.sku_id != ''
  AND d.movement_type IN ('IN', 'OUT', 'ADJ');
