-- Dim bodega desde silver master (PK canónica warehouse_id → bodega_id).
CREATE OR REPLACE TABLE `gold_kora.d_bodega` AS
SELECT
  warehouse_id AS bodega_id,
  warehouse_name AS bodega_name,
  region_code,
  city,
  is_active
FROM `silver_kora.md_bodega`;
