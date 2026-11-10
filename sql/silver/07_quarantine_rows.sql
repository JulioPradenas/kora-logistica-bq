-- Quarantine única: filas deduplicadas (rn=1) que fallan calidad / FK.
-- Los perdedores de dedup (rn>1) NO van aquí → cuentan como eliminated_dupes.
-- Ecuación: bronze = silver + quarantine + eliminated_dupes
CREATE OR REPLACE TABLE `silver_kora.quarantine_rows` AS
WITH
mov_ranked AS (
  SELECT
    TRIM(movement_id) AS natural_key,
    TRIM(warehouse_id) AS warehouse_id,
    TRIM(sku_id) AS sku_id,
    SAFE.PARSE_DATE('%Y-%m-%d', CAST(event_date AS STRING)) AS event_date,
    CASE UPPER(TRIM(CAST(movement_type AS STRING)))
      WHEN 'IN' THEN 'IN' WHEN 'OUT' THEN 'OUT' WHEN 'ADJ' THEN 'ADJ'
      ELSE UPPER(TRIM(CAST(movement_type AS STRING)))
    END AS movement_type,
    TO_JSON_STRING(t) AS payload,
    ROW_NUMBER() OVER (
      PARTITION BY TRIM(movement_id)
      ORDER BY SAFE.PARSE_DATE('%Y-%m-%d', CAST(event_date AS STRING)) NULLS LAST, CAST(qty AS INT64)
    ) AS rn
  FROM `bronze_kora.sap_wms_movimientos` AS t
),
mov_q AS (
  SELECT
    'sap_wms_movimientos' AS source_table,
    m.natural_key,
    CASE
      WHEN m.natural_key IS NULL OR m.natural_key = '' THEN 'null_required_key'
      WHEN m.event_date IS NULL THEN 'null_required_date'
      WHEN m.warehouse_id IS NULL OR m.warehouse_id = '' THEN 'null_required_key'
      WHEN m.sku_id IS NULL OR m.sku_id = '' THEN 'null_required_key'
      WHEN w.warehouse_id IS NULL THEN 'missing_fk_warehouse'
      WHEN s.sku_id IS NULL THEN 'missing_fk_sku'
      WHEN m.movement_type NOT IN ('IN', 'OUT', 'ADJ') THEN 'invalid_movement_type'
      ELSE 'quality_other'
    END AS reason,
    m.payload,
    CURRENT_TIMESTAMP() AS quarantine_ts
  FROM mov_ranked m
  LEFT JOIN `silver_kora.md_bodega` w ON m.warehouse_id = w.warehouse_id
  LEFT JOIN `silver_kora.md_sku` s ON m.sku_id = s.sku_id
  WHERE m.rn = 1
    AND (
      m.natural_key IS NULL OR m.natural_key = ''
      OR m.event_date IS NULL
      OR m.warehouse_id IS NULL OR m.warehouse_id = ''
      OR m.sku_id IS NULL OR m.sku_id = ''
      OR w.warehouse_id IS NULL
      OR s.sku_id IS NULL
      OR m.movement_type NOT IN ('IN', 'OUT', 'ADJ')
    )
),
stk_ranked AS (
  SELECT
    CONCAT(
      CAST(snapshot_date AS STRING), '|',
      TRIM(warehouse_id), '|',
      TRIM(sku_id)
    ) AS natural_key,
    TRIM(warehouse_id) AS warehouse_id,
    TRIM(sku_id) AS sku_id,
    SAFE.PARSE_DATE('%Y-%m-%d', CAST(snapshot_date AS STRING)) AS snapshot_date,
    CAST(on_hand_qty AS INT64) AS on_hand_qty,
    CAST(reserved_qty AS INT64) AS reserved_qty,
    TO_JSON_STRING(t) AS payload,
    ROW_NUMBER() OVER (
      PARTITION BY TRIM(warehouse_id), TRIM(sku_id), CAST(snapshot_date AS STRING)
      ORDER BY CAST(on_hand_qty AS INT64)
    ) AS rn
  FROM `bronze_kora.sap_wms_stock_snapshot` AS t
),
stk_q AS (
  SELECT
    'sap_wms_stock_snapshot' AS source_table,
    s.natural_key,
    CASE
      WHEN s.snapshot_date IS NULL THEN 'null_required_date'
      WHEN s.warehouse_id IS NULL OR s.warehouse_id = '' THEN 'null_required_key'
      WHEN s.sku_id IS NULL OR s.sku_id = '' THEN 'null_required_key'
      WHEN w.warehouse_id IS NULL THEN 'missing_fk_warehouse'
      WHEN sk.sku_id IS NULL THEN 'missing_fk_sku'
      WHEN s.on_hand_qty < 0 OR s.reserved_qty < 0 THEN 'invalid_qty'
      ELSE 'quality_other'
    END AS reason,
    s.payload,
    CURRENT_TIMESTAMP() AS quarantine_ts
  FROM stk_ranked s
  LEFT JOIN `silver_kora.md_bodega` w ON s.warehouse_id = w.warehouse_id
  LEFT JOIN `silver_kora.md_sku` sk ON s.sku_id = sk.sku_id
  WHERE s.rn = 1
    AND (
      s.snapshot_date IS NULL
      OR s.warehouse_id IS NULL OR s.warehouse_id = ''
      OR s.sku_id IS NULL OR s.sku_id = ''
      OR w.warehouse_id IS NULL
      OR sk.sku_id IS NULL
      OR s.on_hand_qty < 0
      OR s.reserved_qty < 0
    )
),
ent_ranked AS (
  SELECT
    TRIM(shipment_id) AS natural_key,
    TRIM(order_id) AS order_id,
    TRIM(warehouse_id) AS warehouse_id,
    TRIM(carrier_id) AS carrier_id,
    SAFE.PARSE_DATE('%Y-%m-%d', CAST(delivery_date AS STRING)) AS delivery_date,
    SAFE.PARSE_DATE('%Y-%m-%d', CAST(ship_date AS STRING)) AS ship_date,
    SAFE.PARSE_DATETIME('%Y-%m-%d %H:%M:%S', NULLIF(TRIM(CAST(eta_ts AS STRING)), '')) AS eta_ts,
    CASE LOWER(REPLACE(TRIM(CAST(status AS STRING)), ' ', ''))
      WHEN 'delivered' THEN 'delivered'
      WHEN 'late_delivered' THEN 'late_delivered'
      WHEN 'latedelivered' THEN 'late_delivered'
      WHEN 'failed' THEN 'failed'
      WHEN 'in_transit' THEN 'in_transit'
      WHEN 'intransit' THEN 'in_transit'
      ELSE NULL
    END AS status,
    TO_JSON_STRING(t) AS payload,
    ROW_NUMBER() OVER (
      PARTITION BY TRIM(shipment_id)
      ORDER BY SAFE.PARSE_DATE('%Y-%m-%d', CAST(delivery_date AS STRING)) NULLS LAST,
               CAST(qty_delivered AS INT64)
    ) AS rn
  FROM `bronze_kora.sap_tm_entregas` AS t
),
ent_q AS (
  SELECT
    'sap_tm_entregas' AS source_table,
    e.natural_key,
    CASE
      WHEN e.natural_key IS NULL OR e.natural_key = '' THEN 'null_required_key'
      WHEN e.order_id IS NULL OR e.order_id = '' THEN 'null_required_key'
      WHEN e.delivery_date IS NULL THEN 'null_required_date'
      WHEN e.ship_date IS NULL THEN 'null_required_date'
      WHEN e.eta_ts IS NULL THEN 'null_required_date'
      WHEN e.status IS NULL THEN 'invalid_status'
      WHEN w.warehouse_id IS NULL THEN 'missing_fk_warehouse'
      WHEN c.carrier_id IS NULL THEN 'missing_fk_carrier'
      ELSE 'quality_other'
    END AS reason,
    e.payload,
    CURRENT_TIMESTAMP() AS quarantine_ts
  FROM ent_ranked e
  LEFT JOIN `silver_kora.md_bodega` w ON e.warehouse_id = w.warehouse_id
  LEFT JOIN `silver_kora.md_transportista` c ON e.carrier_id = c.carrier_id
  WHERE e.rn = 1
    AND (
      e.natural_key IS NULL OR e.natural_key = ''
      OR e.order_id IS NULL OR e.order_id = ''
      OR e.delivery_date IS NULL
      OR e.ship_date IS NULL
      OR e.eta_ts IS NULL
      OR e.status IS NULL
      OR w.warehouse_id IS NULL
      OR c.carrier_id IS NULL
    )
)
SELECT * FROM mov_q
UNION ALL
SELECT * FROM stk_q
UNION ALL
SELECT * FROM ent_q;
