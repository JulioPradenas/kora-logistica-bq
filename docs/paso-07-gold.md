# Paso 7 — Gold datamart `dm_logistica_retail`

**Proyecto:** KORA Deportes · Logística (WMS + last mile)  
**Prerrequisito:** Paso 6 mergeado (silver limpio + quarantine)  
**Siguiente:** Looker Studio / demo de bytes (pasos posteriores)

---

## Meta de esta sesión

Al terminar tendrás:

1. Star schema poblado en `gold_kora`: dims `d_fecha`, `d_bodega`, `d_sku`, `d_transportista` + facts `f_movimientos_bodega`, `f_stock_diario`, `f_entregas`.
2. Vistas KPI: `v_otif_diario`, `v_cycle_time`, `v_stock_salud`.
3. CTAS en `sql/gold/*.sql` (re-declaran `PARTITION BY RANGE_BUCKET` + `CLUSTER BY`).
4. `scripts/build_gold.py` con `--dry-run` (`QueryJobConfig(dry_run=True)`).
5. `make run` stage 6 = `build_gold`; verify con conteos gold esperados (seed 42).

**Fuera de alcance:** Looker Studio, MERGE incremental, credenciales GCP en CI, SLAs no documentados.

---

## Objetivo

Construir el **datamart único** `dm_logistica_retail` (conceptual) como tablas/vistas en `gold_kora`, desde silver. Sandbox: solo **CTAS / CREATE OR REPLACE VIEW** (sin DML/MERGE).

---

## Incidente — orphan `ent_fecha` (delivery lag vs calendario)

**Síntoma (Julio, `make run` real):** `build_gold` OK; post-check `ORPHAN ent_fecha: 79`. Reconcile, counts (salvo `d_fecha`), partitions y demás FKs OK.

**Causa confirmada (seed 42):** `f_entregas.fecha_id` = `delivery_date` YYYYMMDD. El generador pone tránsito **0–4 días** sobre `ship_date` ≤ `2026-10-31` → **79** shipments con `delivery_date` ∈ **2026-11-01..04**. `d_fecha` solo iba hasta `2026-10-31` (579 días) → FK huérfana. Movimientos/stock no se pasan del horizonte WMS.

**Decisión (diff mínimo):** ampliar `d_fecha` a **`2025-04-01`→`2026-11-04`** (583 días). Se mantiene el grano del levantamiento (día de entrega/intento), no se redefine a `ship_date`.  
`PARTITION BY RANGE_BUCKET(..., GENERATE_ARRAY(20250401, 20261101, 100))` **sin cambio**: `fecha_id ≥ 20261101` cae en el último bucket (documentado; Nov corto no exige nuevo `GENERATE_ARRAY`).

**No elegido:** cambiar `fecha_id` a `ship_date` — rompería el grano acordado (§7) y el OTIF “por día de entrega”.

---

## Incidente — DDL gold como contrato vs schema BQ existente

**Síntoma (Julio, `make run` real):** stage `[6] build_gold.py --dry-run` falló en:

- `08_v_otif_diario.sql` — `Name on_time not found inside f`
- `09_v_cycle_time.sql` — `Name on_time not found inside e`

SQL `01`–`07` y `10` pasaron dry-run.

**Causa:** `gold_kora.f_entregas` ya existía desde Paso 4 **sin** `on_time`/`in_full`.  
`CREATE TABLE IF NOT EXISTS` no altera tablas existentes. El dry-run de **vistas** valida contra el schema BQ **actual**, no contra el CTAS `07_f_entregas` que aún no se aplicó. El archivo `ddl_gold.sql` ya declaraba las columnas, pero BQ seguía en el shape viejo → drift DDL↔tabla.

**Fix (opción 1 — DDL = contrato):**

1. Mantener `sql/ddl_gold.sql` alineado a cada CTAS gold (incl. `on_time`/`in_full` BOOL).
2. `apply_ddl.py` — tras CREATE, **`ALTER TABLE … ADD COLUMN IF NOT EXISTS`** en gold existente (idempotente; DDL OK en sandbox). Así el dry-run de vistas ve los nombres antes de materializar el CTAS 07.
3. Pytest: columnas referenciadas por vistas `08`–`10` ⊆ columnas de `ddl_gold.sql`; sync emite ADD para schema legacy.

**Por qué dry-run lo cazó sin writes:** `QueryJobConfig(dry_run=True)` en `CREATE OR REPLACE VIEW` resuelve referencias contra tablas reales; no simula el CTAS pendiente. Fallar en dry-run (antes del apply) evita un half-apply de gold.

**Alternativa descartada:** diferir dry-run de vistas hasta después de facts — enmascara el contrato DDL y complica `make run` (stages asumen dry-run→apply por script).

---

## Grano (star)

| Objeto | Grano (1 fila =) | Origen |
|---|---|---|
| `d_fecha` | 1 día calendario | Generado `2025-04-01`→`2026-11-04` (WMS + lag entrega ≤4d) |
| `d_bodega` / `d_sku` / `d_transportista` | 1 master | Silver masters |
| `f_movimientos_bodega` | bodega × SKU × movement_type × día | Agregación silver movimientos |
| `f_stock_diario` | bodega × SKU × día | Silver stock (1:1) |
| `f_entregas` | 1 shipment (día entrega/intento) | Silver entregas + flags |

---

## Conteos esperados (seed 42)

| Tabla | Filas |
|---|---:|
| `d_fecha` | **583** |
| `d_bodega` | **8** |
| `d_sku` | **250** |
| `d_transportista` | **12** |
| `f_movimientos_bodega` | **55 548** |
| `f_stock_diario` | **208 440** |
| `f_entregas` | **29 803** |

**583 / 8 / 250 / 12 / 55548 / 208440 / 29803**

### Reconciliación gold ↔ silver

| Check | Esperado |
|---|---|
| `SUM(f_movimientos_bodega.qty)` | = `SUM(silver.sap_wms_movimientos.qty)` |
| `SUM(f_movimientos_bodega.n_movimientos)` | = `COUNT(*)` silver movimientos (**60120**) |
| `COUNT(*)` `f_stock_diario` | = silver stock (**208440**) |
| `COUNT(*)` `f_entregas` | = silver entregas (**29803**) |
| Orphan FKs vs dims | **0** |
| Facts `rangePartitioning.field` | `fecha_id` |

---

## KPIs y asunciones explícitas

Fuente de verdad: [`levantamiento-requerimientos.md`](levantamiento-requerimientos.md) §5.  
**No se inventan SLAs** de negocio fuera de ese doc.

| KPI / flag | Definición usada | Nota |
|---|---|---|
| **on_time** | `ATA IS NOT NULL AND ATA ≤ ETA` → BOOL NOT NULL (`FALSE` si no) | Fact `f_entregas` |
| **in_full** | `qty_delivered ≥ qty_ordered` del OUT WMS (`shipment_id`); sin puente → `FALSE` | **Asunción lab:** el export TM no trae qty_ordered; se toma del puente WMS. No hay SLA de fill-rate de calle en el workshop. |
| **OTIF %** (`v_otif_diario`) | `status = delivered` AND on_time / intentadas con status ∈ {delivered, late_delivered, failed} | Doc §5; excluye `in_transit` |
| **Late** | `ATA > ETA` | Doc §5 |
| **Cycle time** | `ATA − pick_start_ts` (y variante despacho `pick_end`) vía `shipment_id`/`order_id` | Doc §5 E2E; excluye sin ATA / sin pick |
| **DOH / stockout** | usable / avg OUT 7d calendario; stockout = `usable_qty = 0` | Doc §5 |
| **Overstock** | `doh_7d > 45` | **Asunción lab de demo** — el workshop **no** fijó umbral de overstock. No presentar como SLA stakeholder. |

---

## CTAS vs MERGE (sandbox)

| | CTAS | MERGE / DML |
|---|---|---|
| Sandbox free-tier | ✅ | ❌ |
| Este paso | **Reescribe gold** idempotente | No |
| Partición/clustering | **Re-declarar** en cada CTAS de fact | N/A |

Lección Paso 6 aplicada a gold: columnas **REQUIRED** del DDL deben mapear a expresiones CTAS no-nulas (p. ej. `IF(..., TRUE, FALSE)` para `on_time`/`in_full`). Test: `test_ddl_required_modes_match_ctas_outputs`.

Citas: [sandbox limitations](https://cloud.google.com/bigquery/docs/sandbox) · [clustered tables](https://cloud.google.com/bigquery/docs/creating-clustered-tables).

---

## Comandos

```bash
cp -n .env.example .env
make setup
make run DRY=1              # dry-run incluye build_gold
make run                    # apply + verify (bronze/silver/gold + quarantine)
make test                   # pytest mocks (sin GCP)
```

Manual:

```bash
source .venv/bin/activate
python scripts/build_gold.py --dry-run
python scripts/build_gold.py
```

---

## Makefile / STAGES

1. `setup_bigquery.py`
2. **`apply_ddl.py`** — CREATE IF NOT EXISTS + bronze DROP NOT NULL + **gold ADD COLUMN IF NOT EXISTS**
3. `generate_synthetic.py`
4. `load_bronze.py`
5. `transform_silver.py`
6. **`build_gold.py`** ← este paso (dry-run de vistas ya ve schema gold sincronizado)

Verify final también imprime `num_rows` gold (expect arriba) + `rangePartitioning` de `f_entregas`.

---

## Q&A corto

**¿Fact vs vista?**  
Facts = grano físico materializado (SSoT). Vistas = KPI derivados / joins listos para Looker sin duplicar lógica en el dashboard.

**¿Por qué un solo datamart?**  
Mismos owners, preguntas cruzadas bodega↔calle, mismo refresh batch — ver levantamiento §8. Split cuando ownership/SLA/seguridad divergen.

**¿Por qué no MERGE?**  
Sandbox sin DML. Con billing, MERGE por partición del día es el patrón incremental.

---

## Límites

- Sin billing → table expiration ~60d.
- Este doc no asume ejecución GCP en el agente cloud.
- Marca KORA ficticia; sin conexión SAP real ni claims Esparta.
