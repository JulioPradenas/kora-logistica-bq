# Paso 5 — Carga bronze (`load` jobs CSV → `bronze_kora`)

**Proyecto:** KORA Deportes · Logística (WMS + last mile)  
**Prerrequisito:** Paso 4 mergeado (tablas vacías en `bronze_kora` / `silver_kora` / `gold_kora`)  
**Siguiente:** Paso 6 — transforms bronze→silver + quarantine

---

## Meta de esta sesión

Al terminar tendrás:

1. Seis CSV sintéticos regenerados (`seed=42`, horizonte fijo) en `data/raw/`.
2. Las seis tablas `bronze_kora.*` cargadas vía **load job** (`load_table_from_file`).
3. Verificación `num_rows` == filas CSV (exit 1 si mismatch).
4. `make run` que incluye generate + load; `--dry-run` valida headers sin escribir a BQ.

**Fuera de alcance:** transforms silver/gold, quarantine, Looker, DML, streaming.

---

## Objetivo

Poblar el **data contract** bronze con el export sintético SAP-like, sin DML (sandbox) y sin autodetect de schema (el shape ya lo fijó el DDL del Paso 4).

---

## Conteos esperados (seed 42 + horizonte fijo)

| Tabla | Filas |
|---|---:|
| `md_bodega` | **8** |
| `md_sku` | **250** |
| `md_transportista` | **12** |
| `sap_wms_movimientos` | **60 120** |
| `sap_wms_stock_snapshot` | **208 440** |
| `sap_tm_entregas` | **29 803** |

Resumen: **8 / 250 / 12 / 60120 / 208440 / 29803**.

---

## Load job vs INSERT / streaming

| Mecanismo | ¿Sandbox? | Uso en este lab |
|---|---|---|
| **Load job** (`load_table_from_file` / `bq load`) | ✅ Permitido | **Este paso** — batch CSV → tabla |
| `INSERT` / `UPDATE` / `DELETE` / `MERGE` (DML) | ❌ No soportado | No usar |
| Streaming insert | ❌ No soportado | No usar |
| `CREATE OR REPLACE TABLE AS SELECT` | ✅ (query job) | Pasos posteriores (silver/gold) |

Fuente sandbox: [Try BigQuery using the sandbox — Limitations](https://cloud.google.com/bigquery/docs/sandbox) (no DML, no streaming).  
Load batch: [Batch load data](https://cloud.google.com/bigquery/docs/batch-loading-data).

---

## Configuración del load

Por cada CSV en `data/raw/<table>.csv` → `bronze_kora.<table>`:

| Opción | Valor | Por qué |
|---|---|---|
| API | `client.load_table_from_file` | Archivo local → load job |
| Schema | **Explícito** desde `get_table(...).schema` | Sin autodetect; respeta DDL Paso 4 |
| `write_disposition` | `WRITE_TRUNCATE` | Re-run idempotente (reemplaza filas) |
| `skip_leading_rows` | `1` | Header CSV |
| `source_format` | `CSV` | Contrato UTF-8 `,` |
| `autodetect` | `False` | No inventar tipos |
| `location` | `southamerica-west1` | Misma región datasets |

Post-load: `get_table` → comparar `num_rows` vs filas CSV (sin header). Mismatch → exit 1.

`--dry-run`: valida que el header CSV = columnas bronze **en orden**; cuenta filas; **no** llama `load_table_from_file`. CSV ausente → error claro.

---

## `WRITE_TRUNCATE` y partición / clustering

Las tablas **bronze** de este lab **no** están particionadas ni clustered (partición integer-range vive en silver/gold). Aun así, la regla general del lab:

- `WRITE_TRUNCATE` en un **load job** a una tabla **ya existente** **borra los datos y escribe de nuevo**; no es un `DROP TABLE`.
- La definición de partición/clustering se establece al **crear** la tabla (`CREATE TABLE … PARTITION BY … CLUSTER BY …`). Un load overwrite a esa tabla no exige re-declarar `PARTITION BY` / `CLUSTER BY` (a diferencia de un `CREATE OR REPLACE TABLE AS SELECT`, que sí debe restatarlos).
- Citas oficiales:
  - [Batch load data — Write disposition](https://cloud.google.com/bigquery/docs/batch-loading-data): `WRITE_TRUNCATE` = “Erases all existing data in a table before writing the new data.”
  - [Create partitioned tables](https://cloud.google.com/bigquery/docs/creating-partitioned-tables) / [Create clustered tables](https://cloud.google.com/bigquery/docs/creating-clustered-tables): partición y clustering son propiedades del objeto tabla al crearlo.
  - Nota API: existe también `WRITE_TRUNCATE_DATA` ([audit WriteDisposition](https://cloud.google.com/bigquery/docs/reference/auditlogs/rest/Shared.Types/BigQueryAuditMetadata.WriteDisposition)) pensado para truncar datos preservando más metadata; en este paso usamos `WRITE_TRUNCATE` + schema explícito tomado de la tabla existente (contrato DDL).

---

## Por qué `make run` regenera CSV **siempre**

`data/raw/*.csv` está en `.gitignore` → no hay artefacto en el clone. Opciones:

| Política | Pros | Contras |
|---|---|---|
| **Siempre** (elegida) | Pipeline autocontenido; seed 42 ⇒ mismos conteos; sin CSV stale | ~segundos de CPU |
| Solo si falta | Re-runs más cortos | Riesgo de CSV viejo tras cambiar generador |

**Decisión:** siempre ejecutar `scripts/generate_synthetic.py` antes de `load_bronze.py`. El generador acepta `--dry-run` (reporta seed/horizonte/paths sin escribir) para encajar en el patrón dry-run → apply del Makefile.

---

## Prerrequisitos en tu Mac

- ADC: `gcloud auth application-default login`
- `GCP_PROJECT=kora-sports` en `.env`
- Paso 4 aplicado (tablas bronze existen)
- APIs BigQuery habilitadas

---

## Comandos

```bash
cp -n .env.example .env
make setup
make run DRY=1              # dry-run de todas las etapas (incluye generate + load)
make run                    # apply + verify (bq ls + num_rows bronze)
make test                   # pytest mocks (sin GCP)
```

Manual equivalente:

```bash
source .venv/bin/activate
python scripts/generate_synthetic.py --dry-run && python scripts/generate_synthetic.py
python scripts/load_bronze.py --dry-run && python scripts/load_bronze.py
bq show --format=prettyjson kora-sports:bronze_kora.sap_wms_movimientos | jq .numRows
```

Consola: https://console.cloud.google.com/bigquery?project=kora-sports

---

## Makefile / verify

`STAGES` (orden):

1. `scripts/setup_bigquery.py`
2. `scripts/apply_ddl.py`
3. `scripts/generate_synthetic.py` ← siempre
4. `scripts/load_bronze.py`

Tras apply (si `DRY!=1`): `bq ls` datasets + **num_rows** de las 6 tablas bronze + schema de un fact gold.

---

## Q&A corto

**¿Por qué load job y no INSERT?**  
Sandbox sin DML; load batch es el camino permitido.

**¿Por qué schema explícito?**  
El DDL ya tipó bronze; autodetect podría divergir (BOOL/`True`, DATETIME, nulos).

**¿Es idempotente?**  
Sí vía `WRITE_TRUNCATE` + re-verify de conteos.

**¿Hay datos en silver/gold?**  
No — solo shapes vacíos. Transforms = Paso 6+.

---

## Límites

- Sin billing → table expiration ~60d; hay que re-cargar en el lab.
- Este doc no asume que el agente cloud ejecutó `gcloud`/BQ (puede no haber ADC en la VM).
- Marca KORA ficticia; sin conexión SAP real.
