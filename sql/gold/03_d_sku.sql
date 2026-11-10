-- Dim SKU desde silver master.
CREATE OR REPLACE TABLE `gold_kora.d_sku` AS
SELECT
  sku_id,
  sku_name,
  category,
  brand,
  unit_cost_clp
FROM `silver_kora.md_sku`;
