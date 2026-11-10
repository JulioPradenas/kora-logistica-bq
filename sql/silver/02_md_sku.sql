-- Silver master SKU: trim + dedup por sku_id.
CREATE OR REPLACE TABLE `silver_kora.md_sku` AS
SELECT
  sku_id,
  sku_name,
  category,
  brand,
  unit_cost_clp
FROM (
  SELECT
    TRIM(sku_id) AS sku_id,
    TRIM(sku_name) AS sku_name,
    UPPER(TRIM(category)) AS category,
    TRIM(brand) AS brand,
    CAST(unit_cost_clp AS INT64) AS unit_cost_clp,
    ROW_NUMBER() OVER (
      PARTITION BY TRIM(sku_id)
      ORDER BY TRIM(sku_name)
    ) AS rn
  FROM `bronze_kora.md_sku`
  WHERE sku_id IS NOT NULL AND TRIM(sku_id) != ''
)
WHERE rn = 1;
