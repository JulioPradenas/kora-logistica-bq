-- Silver entregas: dedup shipment_id, status canónico (UPPER/TRIM + map), FK vs silver.
CREATE OR REPLACE TABLE `silver_kora.sap_tm_entregas`
PARTITION BY RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261101, 100))
CLUSTER BY warehouse_id, carrier_id
AS
WITH deduped AS (
  SELECT * EXCEPT (rn)
  FROM (
    SELECT
      TRIM(shipment_id) AS shipment_id,
      TRIM(order_id) AS order_id,
      TRIM(warehouse_id) AS warehouse_id,
      TRIM(carrier_id) AS carrier_id,
      TRIM(comuna_id) AS comuna_id,
      TRIM(comuna_name) AS comuna_name,
      SAFE.PARSE_DATE('%Y-%m-%d', CAST(ship_date AS STRING)) AS ship_date,
      SAFE.PARSE_DATE('%Y-%m-%d', CAST(delivery_date AS STRING)) AS delivery_date,
      SAFE.PARSE_DATETIME('%Y-%m-%d %H:%M:%S', NULLIF(TRIM(CAST(eta_ts AS STRING)), '')) AS eta_ts,
      SAFE.PARSE_DATETIME('%Y-%m-%d %H:%M:%S', NULLIF(TRIM(CAST(ata_ts AS STRING)), '')) AS ata_ts,
      -- Canonical status: normalize casing/whitespace; do NOT drop.
      CASE LOWER(REPLACE(TRIM(CAST(status AS STRING)), ' ', ''))
        WHEN 'delivered' THEN 'delivered'
        WHEN 'late_delivered' THEN 'late_delivered'
        WHEN 'latedelivered' THEN 'late_delivered'
        WHEN 'failed' THEN 'failed'
        WHEN 'in_transit' THEN 'in_transit'
        WHEN 'intransit' THEN 'in_transit'
        ELSE NULL
      END AS status,
      CAST(qty_delivered AS INT64) AS qty_delivered,
      ROW_NUMBER() OVER (
        PARTITION BY TRIM(shipment_id)
        ORDER BY
          SAFE.PARSE_DATE('%Y-%m-%d', CAST(delivery_date AS STRING)) NULLS LAST,
          CAST(qty_delivered AS INT64)
      ) AS rn
    FROM `bronze_kora.sap_tm_entregas`
  )
  WHERE rn = 1
)
SELECT
  d.shipment_id,
  d.order_id,
  CAST(FORMAT_DATE('%Y%m%d', d.delivery_date) AS INT64) AS fecha_id,
  d.warehouse_id,
  d.carrier_id,
  d.comuna_id,
  d.comuna_name,
  d.ship_date,
  d.delivery_date,
  d.eta_ts,
  d.ata_ts,
  d.status,
  d.qty_delivered
FROM deduped d
INNER JOIN `silver_kora.md_bodega` w ON d.warehouse_id = w.warehouse_id
INNER JOIN `silver_kora.md_transportista` c ON d.carrier_id = c.carrier_id
WHERE d.shipment_id IS NOT NULL
  AND d.shipment_id != ''
  AND d.order_id IS NOT NULL
  AND d.order_id != ''
  AND d.delivery_date IS NOT NULL
  AND d.ship_date IS NOT NULL
  AND d.eta_ts IS NOT NULL
  AND d.status IS NOT NULL
  AND d.status IN ('delivered', 'late_delivered', 'failed', 'in_transit');
