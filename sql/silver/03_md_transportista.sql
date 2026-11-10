-- Silver master transportista: trim + service_level canónico + dedup.
CREATE OR REPLACE TABLE `silver_kora.md_transportista` AS
SELECT
  carrier_id,
  carrier_name,
  service_level
FROM (
  SELECT
    TRIM(carrier_id) AS carrier_id,
    TRIM(carrier_name) AS carrier_name,
    CASE UPPER(TRIM(service_level))
      WHEN 'STD' THEN 'STD'
      WHEN 'EXP' THEN 'EXP'
      ELSE UPPER(TRIM(service_level))
    END AS service_level,
    ROW_NUMBER() OVER (
      PARTITION BY TRIM(carrier_id)
      ORDER BY TRIM(carrier_name)
    ) AS rn
  FROM `bronze_kora.md_transportista`
  WHERE carrier_id IS NOT NULL AND TRIM(carrier_id) != ''
)
WHERE rn = 1;
