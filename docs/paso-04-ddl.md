# Paso 4 — DDL vacío medallón (bronze / silver / gold)

**Proyecto:** KORA Deportes · Logística (WMS + last mile)  
**Prerrequisito:** Paso 3 mergeado (`bronze_kora` / `silver_kora` / `gold_kora` en `southamerica-west1`)  
**Siguiente:** Paso 5 — carga de CSV (load jobs); **fuera de alcance de este paso**

---

## Meta de esta sesión

Al terminar tendrás:

1. Tablas vacías tipadas en `bronze_kora` (6) alineadas al [data contract](../data/README.md).
2. Tablas vacías en `silver_kora` (mismas entidades, claves/estados canónicos + `fecha_id` en hechos).
3. Star schema vacío en `gold_kora`: dims `d_sku`, `d_bodega`, `d_transportista`, `d_fecha` + facts `f_movimientos_bodega`, `f_stock_diario`, `f_entregas`.
4. Script idempotente `scripts/apply_ddl.py` (+ `--dry-run` con BigQuery job `dry_run`).
5. Decisión de partición **integer-range** documentada y citada.

**Fuera de alcance:** carga de datos, transforms, quarantine tables (Paso 6), KPIs Looker.

---

## Objetivo (por qué no DATE partition en este lab)

Julio: el sandbox aplica ~**60 días** de expiración por defecto a tablas/vistas/**particiones** ([sandbox limitations](https://cloud.google.com/bigquery/docs/sandbox)). Con partición por columna `DATE`/`TIMESTAMP`, la expiración de partición se calcula desde la **fecha de la partición**; la historia Apr-2025–Oct-2026 quedaría vacía al crear/cargar.

**Decisión:** facts gold (y hechos silver particionados) usan **`fecha_id` INT64 YYYYMMDD** con:

```sql
PARTITION BY RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261101, 100))
```

### Citas oficiales (verificación)

| Tema | Fuente | Qué dice |
|---|---|---|
| Sandbox: tablas/vistas/**particiones** expiran ~60d | [Try BigQuery using the sandbox — Limitations](https://cloud.google.com/bigquery/docs/sandbox) | “all tables, views, and partitions automatically expire after 60 days” |
| Partition expiration solo time-unit / ingestion | [Manage partitioned tables — Set the partition expiration](https://cloud.google.com/bigquery/docs/managing-partitioned-tables) | Expiration se define al crear tablas particionadas por ingestion time o time-unit column; se calcula por partition time |
| **Integer-range no soporta partition expiration** | Misma página (nota) | “Integer-range partitioned tables don't support partition expiration times.” |
| Integer-range + `RANGE_BUCKET` / `GENERATE_ARRAY` | [Create partitioned tables](https://cloud.google.com/bigquery/docs/creating-partitioned-tables) · [Partitioned tables intro](https://cloud.google.com/bigquery/docs/partitioned-tables) | API de partición por rango entero |
| Máx. **10 000** partitions / tabla | [Quotas — Partitioned tables](https://cloud.google.com/bigquery/quotas#partitioned_tables) | “Number of partitions per partitioned table — 10,000” |
| Sandbox **sin DML** | [Sandbox limitations](https://cloud.google.com/bigquery/docs/sandbox) | No DML → DDL con `CREATE TABLE IF NOT EXISTS`; loads posteriores vía load / `CREATE OR REPLACE TABLE AS SELECT` |

**Conclusión verificada:** no improvisar. Integer-range evita el wipe por *partition expiration* ligado a fechas históricas. Sigue aplicando la **table expiration** ~60d del sandbox a la tabla entera (hay que re-aplicar DDL / re-cargar en el lab, o upgradear billing y quitar expiration).

### Daily vs monthly (intervalo)

| Opción | `GENERATE_ARRAY` | ≈ partitions | ¿OK vs 10k? |
|---|---|---|---|
| Daily `interval=1` sobre YYYYMMDD | `(20250401, 20261101, 1)` | ~110 700 buckets en el espacio entero | **No** (>10 000) |
| Monthly-ish `interval=100` | `(20250401, 20261101, 100)` | **107** | **Sí** |

**Elegido: intervalo 100** (espaciado de mes en encoding `YYYYMMDD`). El span entero `YYYYMMDD` de 20250401→20261101 es 10 700 (daily >10k); `/100` = 107 buckets. Cubre el horizonte del contract (`2025-04-01`→`2026-10-31`); end exclusivo `20261101`.

**Prod con billing:** partición por `DATE` (`PARTITION BY event_date` / `delivery_date`) + `partition_expiration_days` NULL o retención larga; clustering igual (`bodega_id, sku_id`; entregas + `transportista_id`).

---

## Sandbox: sin DML

- Este paso solo emite **`CREATE TABLE IF NOT EXISTS`**.
- No `INSERT` / `UPDATE` / `DELETE` / `MERGE`.
- Paso 5+: load jobs o `CREATE OR REPLACE TABLE AS SELECT` (reemplazo de tabla, no DML fila a fila).

---

## Quarantine

Tablas de rechazo / quarantine **no** se crean aquí. Encajan mejor en Paso 6 (reglas de calidad al transformar bronze→silver). Silver de este paso = shape limpio vacío.

---

## Prerrequisitos en tu Mac

- ADC: `gcloud auth application-default login`
- `GCP_PROJECT=kora-sports` en `.env`
- APIs BigQuery habilitadas

---

## Un comando (recomendado)

Desde la raíz del repo:

```bash
cp -n .env.example .env    # GCP_PROJECT=kora-sports
make setup                 # .venv + deps (idempotente)
make run                   # STAGES en orden: setup_bigquery → apply_ddl (dry-run→apply) + verify
make run DRY=1             # solo dry-run de cada etapa
make test                  # pytest sin GCP
```

`make run` es el **entrypoint estable**: lee `STAGES` en el Makefile (un solo lugar extensible), usa `PY=.venv/bin/python`, `GCP_PROJECT` desde `.env` (default `kora-sports`), y **para** si un dry-run/apply falla. No imprime secretos.  
Re-run es seguro (scripts idempotentes). Futuro load/silver/gold = añadir una línea a `STAGES`.  
Equivalente manual abajo.

---

## Comandos exactos (Mac, manual)

```bash
cd ~/projects/kora-logistica-bq   # ajusta tu ruta

git checkout main
git pull origin main

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt

cp -n .env.example .env
# GCP_PROJECT=kora-sports
```

### Dry-run (valida DDL sin crear)

```bash
python scripts/apply_ddl.py --dry-run
# o: make -n run     # solo imprime el plan (útil en CI sin credenciales)
# o: make run DRY=1  # dry-run real de cada stage (sí llama BQ dry_run; no crea)
```

**Salida esperada (aprox., primera vez):**

```
project=kora-sports
location=southamerica-west1
dry_run=True
partition_range=RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261101, 100))
partition_buckets=107 (limit 10000)
  would_create  bronze_kora.md_bodega  ok
  ...
  would_create  gold_kora.f_entregas  ok
summary: tables=19 created_or_planned=19 skipped=0 errors=0
dry-run: validación BigQuery job dry_run; no se crearon tablas.
```

(Si ya existen: `would_skip`.)

### Apply + re-run idempotente

```bash
python scripts/apply_ddl.py
python scripts/apply_ddl.py    # segunda vez: action=exists, exit 0
```

**Primera vez:** `created` × 19 + `OK: DDL bronze/silver/gold aplicado...`  
**Segunda vez:** `exists` × 19 + mismo `OK`.

---

## Verificar schema (CLI / consola)

```bash
bq show --schema --format=prettyjson kora-sports:bronze_kora.sap_wms_movimientos
bq show --schema --format=prettyjson kora-sports:gold_kora.f_movimientos_bodega
bq show --format=prettyjson kora-sports:gold_kora.f_entregas | head -n 80
```

Consola: https://console.cloud.google.com/bigquery?project=kora-sports  
→ `gold_kora.f_*` → Details: partitioning `fecha_id` RANGE, clustering columns visibles.

Esperado en facts: `rangePartitioning` / integer range sobre `fecha_id`; clustering `bodega_id` (+ `sku_id` o `transportista_id`).

---

## Tests (sin GCP)

```bash
make test
# o:
pytest tests/test_apply_ddl.py -q
pytest -q
```

Mocks: contrato bronze vs DDL; facts gold con integer-range + cluster; runner dry-run / idempotencia.  
CI corre `pytest` + `make -n run` (plan only; **sin** llamadas GCP).

---

## Checklist

- [ ] `make run` OK (o dry-run + apply manual de cada stage)  
- [ ] Re-run `make run` sin error (`exists`)  
- [ ] `bq show --schema` coincide con contract / star  
- [ ] Facts: integer-range + clustering  
- [ ] `make test` / `pytest -q` verde  
- [ ] **Aún no** hay filas cargadas (Paso 5)

Cuando esté ✅ → Paso 5 (load) en un PR aparte.
