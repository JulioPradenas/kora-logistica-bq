-- Silver master bodegas: trim + dedup por warehouse_id (CTAS; sandbox sin DML).
CREATE OR REPLACE TABLE `silver_kora.md_bodega` AS
SELECT
  warehouse_id,
  warehouse_name,
  region_code,
  city,
  is_active
FROM (
  SELECT
    TRIM(warehouse_id) AS warehouse_id,
    TRIM(warehouse_name) AS warehouse_name,
    TRIM(region_code) AS region_code,
    TRIM(city) AS city,
    is_active,
    ROW_NUMBER() OVER (
      PARTITION BY TRIM(warehouse_id)
      ORDER BY TRIM(warehouse_name)
    ) AS rn
  FROM `bronze_kora.md_bodega`
  WHERE warehouse_id IS NOT NULL AND TRIM(warehouse_id) != ''
)
WHERE rn = 1;
