# Paso 6 — Transform bronze → silver + quarantine

**Proyecto:** KORA Deportes · Logística (WMS + last mile)  
**Prerrequisito:** Paso 5 mergeado (CSV seed 42 cargados en `bronze_kora`)  
**Siguiente:** Paso 7 — gold / mart KPIs

---

## Meta de esta sesión

Al terminar tendrás:

1. CTAS por entidad en `sql/silver/*.sql` (dedup, status canónico, tipado, FK vs silver masters).
2. Tabla `silver_kora.quarantine_rows` (source, key, reason, payload) — sin drop silencioso.
3. `scripts/transform_silver.py` con `--dry-run` (BQ job dry_run), orden masters→facts→quarantine, reconciliación.
4. `make run` incluye `transform_silver` tras `load_bronze`; verify muestra silver + quarantine by reason.
5. Anomalías sintéticas documentadas (seed 42) en el generador.

**Fuera de alcance:** gold KPIs, Looker, DML/MERGE, credenciales GCP reales en CI.

---

## Objetivo

Limpiar `bronze_kora` → `silver_kora` con reglas de calidad observables. Filas malas van a **quarantine**; duplicados eliminados se contabilizan aparte. Sandbox: solo **CTAS** (no MERGE/DML).

---

## Incidente — landing permisivo vs silver estricto

**Síntoma (Julio, `make run` real):** stage `[4] load_bronze` rechazó facts con  
`Required column value is missing` en `event_date`/`warehouse_id` (movimientos ~20),  
`snapshot_date`/`warehouse_id` (stock ~10), `delivery_date`/`order_id` (entregas ~15).

**Causa:** DDL Paso 4 dejó columnas bronze **REQUIRED**. `CREATE TABLE IF NOT EXISTS` no altera tablas ya creadas; `load_bronze` tomaba el schema de la tabla (`max_bad_records=0`). Las anomalías null de Paso 6 **nunca entraban a bronze**.

**Fix (elegido):**

1. `sql/ddl_bronze.sql` — **todas** las columnas NULLABLE (tipos iguales).
2. `apply_ddl.py` — tras CREATE, **`ALTER TABLE … ALTER COLUMN … DROP NOT NULL`** en columnas REQUIRED de bronze existentes (idempotente; DDL OK en sandbox). Esto es lo que desbloquea el laptop de Julio.
3. `load_bronze.py` — `schema_as_nullable()` en el LoadJobConfig (no re-impone REQUIRED). No se usa `ALLOW_FIELD_RELAXATION` con `WRITE_TRUNCATE` en tablas no particionadas (poco fiable).

**Por qué no solo cambiar el DDL:** Julio ya tiene tablas; IF NOT EXISTS no relaja nullability.  
**Por qué no solo el load:** el table schema REQUIRED sigue rechazando nulls aunque el job declare NULLABLE.  
**Silver:** sigue estricto (filtros + quarantine `null_required_*`).

---

## Conteos esperados (seed 42)

### Bronze (tras anomalías)

| Tabla | Filas |
|---|---:|
| `md_bodega` | **8** |
| `md_sku` | **250** |
| `md_transportista` | **12** |
| `sap_wms_movimientos` | **60 285** |
| `sap_wms_stock_snapshot` | **208 555** |
| `sap_tm_entregas` | **29 923** |

**8 / 250 / 12 / 60285 / 208555 / 29923**

### Silver (núcleo limpio)

| Tabla | Filas |
|---|---:|
| `md_bodega` | **8** |
| `md_sku` | **250** |
| `md_transportista` | **12** |
| `sap_wms_movimientos` | **60 120** |
| `sap_wms_stock_snapshot` | **208 440** |
| `sap_tm_entregas` | **29 803** |

**8 / 250 / 12 / 60120 / 208440 / 29803**

### Quarantine by reason

| reason | n |
|---|---:|
| `missing_fk_warehouse` | 45 |
| `missing_fk_sku` | 50 |
| `missing_fk_carrier` | 20 |
| `null_required_date` | 23 |
| `null_required_key` | 22 |
| **Total** | **160** |

### Eliminated dupes (no quarantine)

| Entidad | n |
|---|---:|
| `sap_wms_movimientos` | 90 |
| `sap_wms_stock_snapshot` | 80 |
| `sap_tm_entregas` | 70 |

### Ecuación

```
bronze = silver + quarantine + eliminated_dupes
```

Ejemplo movimientos: `60285 = 60120 + 75 + 90`.  
Status casing (80 entregas): se **normaliza** a silver; no suma quarantine ni elim.

Mismatch → `transform_silver.py` exit 1.

---

## Reglas de calidad

| Regla | Acción |
|---|---|
| Dedup `ROW_NUMBER()` sobre clave natural | rn=1 sigue; rn>1 = **eliminated_dupes** |
| Status / movement_type | `UPPER`/`TRIM` + map canónico → silver (no drop) |
| FK vs `silver_kora` masters | fail → quarantine `missing_fk_*` |
| Null en fecha/clave requerida | quarantine `null_required_*` |
| Tipado + `fecha_id` YYYYMMDD | CTAS |
| `usable_qty` | `on_hand - LEAST(reserved, on_hand)` |

---

## CTAS vs MERGE (sandbox)

| | CTAS `CREATE OR REPLACE … AS SELECT` | MERGE / DML |
|---|---|---|
| Sandbox free-tier | ✅ | ❌ |
| Este paso | **Sí** | No |
| Partición/clustering | **Re-declarar** en el CTAS | N/A |

Con billing, un MERGE incremental sobre partición del día reduce bytes scanned; aquí el lab reescribe silver completo (idempotente, demo).

Citas: [sandbox limitations](https://cloud.google.com/bigquery/docs/sandbox) · [create clustered tables](https://cloud.google.com/bigquery/docs/creating-clustered-tables).

---

## Comandos

```bash
cp -n .env.example .env
make setup
make run DRY=1              # dry-run incluye transform_silver
make run                    # apply + verify (bronze + silver + quarantine by reason)
make test                   # pytest mocks (sin GCP)
```

Manual:

```bash
source .venv/bin/activate
python scripts/transform_silver.py --dry-run
python scripts/transform_silver.py
```

---

## Makefile / STAGES

1. `setup_bigquery.py`
2. `apply_ddl.py` (incluye shape `quarantine_rows`)
3. `generate_synthetic.py` (siempre; + anomalías)
4. `load_bronze.py`
5. **`transform_silver.py`** ← este paso

Verify final: `num_rows` bronze + silver + `SELECT reason, COUNT(*) … quarantine_rows`.

---

## Q&A corto

**¿Por qué quarantine y no drop?**  
Observabilidad: el analista ve *cuántas* y *por qué*; drop silencioso oculta defectos del export.

**¿Por qué CTAS y no MERGE?**  
Sandbox sin DML. Con billing, MERGE incremental es la vía natural.

**¿Status raro se pierde?**  
No — se normaliza si mapea; si no, `invalid_status` → quarantine.

---

## Límites

- Sin billing → table expiration ~60d.
- Este doc no asume ejecución GCP en el agente cloud.
- Marca KORA ficticia; sin conexión SAP real ni claims Esparta.
