# Paso 8 — Partition prune demo + fix `__UNPARTITIONED__`

**Proyecto:** KORA Deportes · Logística (WMS + last mile)  
**Prerrequisito:** Paso 7 mergeado (gold star + vistas)  
**Siguiente:** Looker Studio + one-page (Paso 9)

---

## Meta de esta sesión

1. Ampliar el rango integer-range a **108 buckets**:  
   `RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261201, 100))`  
   (end **exclusivo**; antes `20261101` → 107 buckets).
2. `apply_ddl`: detectar cambio de partition-spec y **recrear** tablas (DROP+CREATE) de forma idempotente.
3. Post-run: **0 filas** en `__UNPARTITIONED__` / `__NULL__` (`INFORMATION_SCHEMA.PARTITIONS`); fallar si hay.
4. Demo de bytes: `sql/demo_partition_prune.sql` + stage 7 `scripts/demo_partition_prune.py`.
5. Documentar resultados reales de `make run` (Julio) en README / este doc / briefing.

**Fuera de alcance:** Looker publish, credenciales GCP en CI/agente, merge a `main` por el agente.

---

## Por qué (Nov → `__UNPARTITIONED__`)

`GENERATE_ARRAY(start, end, step)` es **end exclusivo**. Con:

```sql
GENERATE_ARRAY(20250401, 20261101, 100)  -- 107 buckets
```

los `fecha_id ≥ 20261101` (entregas Nov por lag 0–4 días del generador, seed 42) **no** caen en el último rango `[…, 20261101)`; BigQuery los manda a la partición especial **`__UNPARTITIONED__`**.

Eso rompe el prune: un filtro `fecha_id` de Oct/Nov no evita escanear esas filas “fuera de rango”, y el post-check de salud de partición falla.

**Fix:** end exclusivo `20261201` → 108 buckets; Nov 2026-11-01..04 queda en `[20261101, 20261201)`.

| `GENERATE_ARRAY` | Buckets | Nov `fecha_id` |
|---|---|---|
| `(20250401, 20261101, 100)` | **107** | `__UNPARTITIONED__` ❌ |
| `(20250401, 20261201, 100)` | **108** | in-range ✅ |

Cita: [Create partitioned tables — integer range](https://cloud.google.com/bigquery/docs/creating-partitioned-tables) · valores fuera de los rangos → `__UNPARTITIONED__`.

---

## Recreate idempotente (`apply_ddl`)

BigQuery **no** permite `ALTER` del partition spec. Si la tabla ya existe con otro `range` (p. ej. end `20261101`):

1. Comparar `table.range_partitioning.range_` vs `GENERATE_ARRAY` del DDL.
2. Si difiere → `DROP TABLE IF EXISTS` + `CREATE TABLE IF NOT EXISTS` (action=`recreated` / dry-run `would_recreate`).
3. Si coincide → `exists` / `would_skip` (como Paso 4).

**¿Es seguro borrar?** Sí en este lab: `make run` regenera todo — CSV (seed 42) → load bronze (`WRITE_TRUNCATE`) → silver/gold **CTAS** (`CREATE OR REPLACE`). No hay estado incremental que preservar. En prod con billing haría falta un cutover/backfill explícito.

---

## Post-run: cero `__UNPARTITIONED__` / `__NULL__`

Tras cargar facts (silver + gold), se consulta:

```sql
SELECT table_schema, table_name, partition_id, total_rows
FROM `…INFORMATION_SCHEMA.PARTITIONS`
WHERE partition_id IN ('__UNPARTITIONED__', '__NULL__')
  AND total_rows > 0
```

Implementado en `apply_ddl.check_no_unpartitioned_rows` y llamado desde:

- `build_gold.post_run_checks`
- `demo_partition_prune.py` (stage 7)
- verify del `Makefile` (impresión)

Exit ≠ 0 si aparece cualquier fila.

---

## Demo prune (3 queries)

Archivo: [`sql/demo_partition_prune.sql`](../sql/demo_partition_prune.sql) sobre `gold_kora.f_stock_diario`:

| Query | Filtro | Qué demuestra |
|---|---|---|
| **(a)** | ninguno | baseline bytes |
| **(b)** | un mes `fecha_id` | partition prune |
| **(c)** | mes + `bodega_id` | prune + clustering (en job real) |

Stage 7: `scripts/demo_partition_prune.py` — **dry_run** de las 3, imprime `totalBytesProcessed` y **% vs (a)**. Compatible con `make run DRY=1`.

### Resultados reales (`make run`, Julio)

| Query | Filtro | `totalBytesProcessed` (dry_run) | % vs (a) |
|---|---|---|---|
| **(a)** | ninguno | 1 667 520 B (1.59 MiB) | 100% |
| **(b)** | un mes `fecha_id` | 178 560 B (174.38 KiB) | **10.7%** |
| **(c)** | mes + `bodega_id` | 256 680 B (250.66 KiB) | **15.4%** |

Mismo run: **6 tablas** recreadas a 108 buckets; `f_entregas` range end=`20261201`; **0** filas `__UNPARTITIONED__`/`__NULL__`; gold counts **583/8/250/12/55548/208440/29803**.

### Por qué (c) > (b) en dry_run (honestidad)

1. BigQuery cobra por **columnas leídas**. (c) filtra/proyecta `bodega_id` además de las de (b) → la estimación dry_run sube.
2. El dry_run estima a **nivel de partición**; **no acredita** el skip de bloques de clustering.
3. El ahorro de `CLUSTER BY` solo se ve en **bytes facturados de un job real** (`python scripts/demo_partition_prune.py --real-cluster` para (c)).
4. Jobs reales tienen **mínimo ~10 MB** facturados por query — en este lab el dataset es chico, así que el job real de (c) puede redondear al mínimo y no ser comparable 1:1 con el dry_run.

Conclusión de entrevista: (b) vs (a) sí demuestra **partition prune** (~10.7% del baseline). (c) en dry_run **no** demuestra clustering; afirmar lo contrario sería mentir con la herramienta.

---

## Un comando

```bash
make setup
make run          # incluye stage 7 demo_partition_prune
make run DRY=1    # dry-run de las 7 stages (sin writes)
make test         # pytest sin GCP
```

Salida esperada de `apply_ddl` (extracto):

```
partition_range=RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261201, 100))
partition_buckets=108 (limit 10000)
  recreated     gold_kora.f_entregas  partition-spec change: bq=(…, 20261101, …) ddl=(…, 20261201, …); …
```

(Primera vez tras el cambio; re-runs posteriores: `exists`.)

---

## Pytest / CI

- `partition_buckets == 108`; falla si el end vuelve a `20261101` (107).
- Detecta mismatch → `recreated` / `would_recreate`.
- `check_no_unpartitioned_rows` falla con filas mock en `__UNPARTITIONED__`.
- Demo: 3 queries parseadas; Makefile incluye el stage.
- CI: `pytest` + `make -n run` (sin credenciales GCP).

---

## Referencias

- [docs/paso-04-ddl.md](paso-04-ddl.md) — decisión integer-range / sandbox
- [docs/paso-07-gold.md](paso-07-gold.md) — Nov lag deliveries / `d_fecha`
- Briefing entrevista: store `docs/kora/briefing-paso-08.md`
