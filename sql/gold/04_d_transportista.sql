-- Dim transportista desde silver master (carrier_id → transportista_id).
CREATE OR REPLACE TABLE `gold_kora.d_transportista` AS
SELECT
  carrier_id AS transportista_id,
  carrier_name AS transportista_name,
  service_level
FROM `silver_kora.md_transportista`;
