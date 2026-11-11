# Guía de estudio — entrevista BI (caso KORA / BigQuery)

**Para:** Julio Pradenas  
**Repo:** [JulioPradenas/kora-logistica-bq](https://github.com/JulioPradenas/kora-logistica-bq)  
**Uso:** preparar la defensa oral del portafolio (BI, migración de datos, pipelines).  
**Alcance:** lab de portafolio con marca **KORA Deportes** (ficticia). Contexto de entrevista tipo Esparta (BI / migración / pipelines) — **no** atribuir a Esparta requisitos, SLAs ni datos no documentados en este repo.

Números citados abajo salen de `README.md`, `data/README.md`, `looker/README.md`, `docs/paso-*` y demos SQL del repo (seed 42). **No inventar.**

---

## 1) El proyecto en 2 minutos (pitch hablado)

> “KORA Deportes es un retail ficticio: hoy stock/WMS y OTIF last-mile viven en Excels distintos, sin SSoT. Construí un lab ELT en BigQuery sandbox (`kora-sports`, región Santiago): CSV sintéticos SAP-like → bronze → silver con quarantine → gold star + vistas KPI → Looker Studio. Un solo datamart conceptual `dm_logistica_retail` une bodega y calle vía `shipment_id`/`order_id`. Particiono facts por `fecha_id` integer-range (no DATE: el sandbox expira particiones por fecha ~60d), clusterizo, demuestro partition prune con bytes reales, y el dashboard muestra OTIF ponderado 58,06%, cycle ~51,6 h y funnel 40.823 / 29.803 / 21.325. Auth solo con ADC — sin keys en git. Orquestación cloud (Scheduler/Functions/Composer) la narro como siguiente escalón; no está implementada.”

Si preguntan “¿es producción?”: *lab operable end-to-end con datos sintéticos; el contrato de columnas es el mismo que engancharías a un export real.*

---

## 2) Recorrido completo paso a paso (1–10)

| Paso | Qué | Por qué | Cómo / artefactos | PR |
|---|---|---|---|---|
| **1** | Repo + levantamiento | Fijar KPIs, grano, un mart vs varios, MVP Looker | [`docs/levantamiento-requerimientos.md`](levantamiento-requerimientos.md), esqueleto repo | commit base `e13ee9d` (pre-PR numerados) |
| **2** | Datos sintéticos + data contract | Contrato columnas/tipos/PKs antes del DDL | [`scripts/generate_synthetic.py`](../scripts/generate_synthetic.py), [`data/README.md`](../data/README.md), seed **42**, horizonte `2025-04-01`→`2026-10-31` | [#1](https://github.com/JulioPradenas/kora-logistica-bq/pull/1) |
| **3** | Datasets medallón | Misma location para joins locales | `bronze_kora` / `silver_kora` / `gold_kora` en `southamerica-west1`; [`scripts/setup_bigquery.py`](../scripts/setup_bigquery.py); [docs/paso-03](paso-03-bigquery-setup.md) | [#2](https://github.com/JulioPradenas/kora-logistica-bq/pull/2) |
| **4** | DDL vacío + star | Shape tipado; partition/cluster sin datos | [`sql/ddl_*.sql`](../sql/), [`scripts/apply_ddl.py`](../scripts/apply_ddl.py); integer-range en `fecha_id`; [docs/paso-04](paso-04-ddl.md) | [#3](https://github.com/JulioPradenas/kora-logistica-bq/pull/3) |
| **5** | Load bronze | Landing sin DML (sandbox) | Load jobs `WRITE_TRUNCATE`; [`scripts/load_bronze.py`](../scripts/load_bronze.py); [docs/paso-05](paso-05-load-bronze.md). Conteos bronze: **8 / 250 / 12 / 60285 / 208555 / 29923** | [#4](https://github.com/JulioPradenas/kora-logistica-bq/pull/4) |
| **6** | Silver + quarantine | Calidad observable; no drop silencioso | CTAS [`sql/silver/`](../sql/silver/); [`scripts/transform_silver.py`](../scripts/transform_silver.py); [docs/paso-06](paso-06-silver.md). Silver limpio **8 / 250 / 12 / 60120 / 208440 / 29803**; quarantine **160** | [#5](https://github.com/JulioPradenas/kora-logistica-bq/pull/5) |
| **7** | Gold datamart | SSoT star + KPIs | CTAS/vistas [`sql/gold/`](../sql/gold/); [`scripts/build_gold.py`](../scripts/build_gold.py); [docs/paso-07](paso-07-gold.md). Gold **583 / 8 / 250 / 12 / 55548 / 208440 / 29803** | [#6](https://github.com/JulioPradenas/kora-logistica-bq/pull/6) |
| **8** | Partition prune + 108 buckets | Nov fuera de rango → `__UNPARTITIONED__`; demo bytes | End exclusivo `20261201`; [`sql/demo_partition_prune.sql`](../sql/demo_partition_prune.sql); [docs/paso-08](paso-08-pruning.md). Prune (b) = **10,7%** del baseline | [#7](https://github.com/JulioPradenas/kora-logistica-bq/pull/7) |
| **9** | Looker + vistas | Dashboard solo sobre gold | Vistas 08–13; [`looker/README.md`](../looker/README.md); [docs/paso-09](paso-09-looker.md); [viewer](https://datastudio.google.com/reporting/172aa667-fb63-4c36-84d4-4585c8ad89ba). Style guide [#10](https://github.com/JulioPradenas/kora-logistica-bq/pull/10) | [#8](https://github.com/JulioPradenas/kora-logistica-bq/pull/8) · cierre [#9](https://github.com/JulioPradenas/kora-logistica-bq/pull/9) |
| **10** | One-pager + DoD | Portafolio público | `site/index.html` — **en curso** (PR abierto aparte; no mezclar con esta guía) | [#11](https://github.com/JulioPradenas/kora-logistica-bq/pull/11) (open) |

**Entry point:** `make run` = datasets → DDL → generate CSV → load bronze → transform silver → build gold → demo prune (cada etapa dry-run → apply). Auth: `gcloud auth application-default login`.

---

## 3) Conceptos clave (explicación simple)

### Medallón bronze / silver / gold
- **Bronze:** landing tipada del export (casi crudo; en este lab columnas NULLABLE para no rechazar basura en el load).
- **Silver:** limpio y canónico (dedup, FK, tipado, `fecha_id`); lo malo va a **quarantine**, no se borra en silencio.
- **Gold:** datamart star + vistas KPI listas para Looker. Looker **solo** lee `gold_kora`.

### Quarantine y reconciliación
Filas con FK rota o null en fecha/clave → `silver_kora.quarantine_rows` (`source`, `key`, `reason`, `payload`).  
Ecuación: `bronze = silver + quarantine + eliminated_dupes` (status casing se normaliza; no suma filas). Mismatch → exit 1.

### Star model y granos
| Objeto | 1 fila = |
|---|---|
| `f_movimientos_bodega` | bodega × SKU × movement_type × día |
| `f_stock_diario` | bodega × SKU × día |
| `f_entregas` | 1 shipment (día entrega/intento) |
| dims | `d_fecha`, `d_bodega`, `d_sku`, `d_transportista` |

### Integer-range partition en `fecha_id` (¿por qué no DATE?)
Sandbox: tablas/**particiones** expiran ~60 días; con partición por DATE la historia Apr-2025–Oct-2026 se vaciaría. Integer-range **no** soporta partition expiration → facts usan `fecha_id` INT64 YYYYMMDD + `RANGE_BUCKET(..., GENERATE_ARRAY(20250401, 20261201, 100))` = **108** buckets. En prod con billing: partición por DATE + retención larga.

### Clustering
Facts: `CLUSTER BY` bodega/sku (y carrier en entregas). Reduce bloques leídos en queries filtradas; el dry_run **no** acredita ese ahorro (solo jobs reales; mínimo ~10 MB facturados).

### Partition pruning
Filtro en la columna de partición → BQ escanea menos particiones. Demo (a)/(b)/(c) sobre `f_stock_diario`: (b) un mes ≈ **10,7%** de bytes vs full scan.

### `make run` e idempotencia
Cada stage: dry-run → apply. Load `WRITE_TRUNCATE`; silver/gold = CTAS `CREATE OR REPLACE`. Si cambia partition-spec → DROP+CREATE (seguro: el mismo `make run` regenera CSV seed 42). Re-run no deja estado a medias a propósito: falla y para.

### ADC sin keys
`gcloud auth application-default login` + quota project. Sin service-account JSON en git ni en CI. CI solo `pytest` + `make -n run`.

---

## 4) Métricas y trampas

### OTIF = `SUM(n_otif)/SUM(n_intentadas_cerradas)` — nunca avg de %
`AVG(otif_pct)` sobre filas día×bodega da un ratio **sin ponderar** (~0,58% en la trampa documentada). El correcto pondera volumen: **58,06%** en el dashboard (seed 42). Denominador: status ∈ {delivered, late_delivered, failed}; excluye `in_transit`.

### Fan-out y `COUNT_DISTINCT(shipment_id)`
Drill une OUT WMS por `shipment_id` → grano **shipment × SKU**. Contar filas o `AVG(on_time)` infla/distorsiona. Usar `COUNT_DISTINCT(shipment_id)` y % a tiempo con `COUNT_DISTINCT(CASE WHEN on_time THEN shipment_id END)/COUNT_DISTINCT(shipment_id)`.

### Dos poblaciones de shipments: **29.803** vs **25.609**
| Población | Vista / origen | Semántica |
|---|---|---|
| **29.803** | `f_entregas` / funnel despacho / drill | Todos los envíos silver limpios (despachados en el mart) |
| **25.609** | `v_cycle_time` | Subconjunto: INNER JOIN con pick OUT + `ata_ts` NOT NULL + status cerrado — **no** es “envíos despachados” |

No mezclar KPIs entre vistas.

### Funnel derivado
Sin columna `stage` en el origen. `funnel_kind = shipment_e2e`: picking (OUT+pick_start) **40.823** → despacho **29.803** → entrega **21.325**. Entrega ⊆ despacho; picking **no** es subconjunto de despacho (traslados, short-pick sin TM, etc.).

---

## 5) Decisiones defendibles

1. **Funnel derivado** — no inventar `stage_*` en gold; proxy con timestamps/status documentado; en prod pedir event log.
2. **Comuna como dim degenerada** — `comuna_id`/`comuna_name` en `f_entregas`; levantamiento nombra `d_comuna_destino` pero no hay maestro → no fabricar dim con `DISTINCT`.
3. **SKU vía bridge `shipment_id`** — el fact de entregas no trae SKU; cruzar OUT WMS; mitigar fan-out con `COUNT DISTINCT`; no meter “SKU principal” inventado.
4. **Un solo datamart** — mismos owners, preguntas cruzadas bodega↔calle, mismo refresh batch ([§8 levantamiento](levantamiento-requerimientos.md)). Partir si ownership/SLA/seguridad divergen.

---

## 6) Números clave reales (seed 42 — scrap del repo)

### Bronze / silver / quarantine
| Capa | Conteos |
|---|---|
| Bronze | **8 / 250 / 12 / 60 285 / 208 555 / 29 923** |
| Silver limpio | **8 / 250 / 12 / 60 120 / 208 440 / 29 803** |
| Quarantine total | **160** = 45 `missing_fk_warehouse` + 50 `missing_fk_sku` + 20 `missing_fk_carrier` + 23 `null_required_date` + 22 `null_required_key` |
| Eliminated dupes | mov 90 · stock 80 · entregas 70 |

### Gold
**583 / 8 / 250 / 12 / 55 548 / 208 440 / 29 803** (`d_fecha` 583 días: `2025-04-01`→`2026-11-04`).  
Reconcile: `SUM(n_movimientos)` gold = 60 120 silver; stock/entregas 1:1 con silver.

### Partition / prune (`f_stock_diario`, dry_run)
| Query | Bytes | % vs (a) |
|---|---|---|
| (a) full | 1 667 520 B (1,59 MiB) | 100% |
| (b) un mes `fecha_id` | 178 560 B | **10,7%** |
| (c) mes + `bodega_id` | 256 680 B | 15,4% (dry_run no acredita clustering) |

Post-run: **108** buckets; end `20261201`; **0** filas `__UNPARTITIONED__`/`__NULL__`.

### Dashboard Looker (2026-11-10)
| Métrica | Valor |
|---|---|
| OTIF % | **58,06%** |
| Entregas atrasadas | **10.718** |
| Cycle pick→entrega | **51,6 h** |
| Funnel e2e | **40.823 / 29.803 / 21.325** |
| Drill % a tiempo (ej. top Santiago / 3PL Partner 10) | **49,82%** |
| Stockouts | **627** |
| DOH 7d avg | **722,67** (artefacto sintético) |
| Overstock filas | **147.867** (heurística lab `doh_7d > 45`, no SLA) |

Fuentes: [`README.md`](../README.md), [`looker/README.md`](../looker/README.md), [`docs/paso-06`](paso-06-silver.md)–[`paso-09`](paso-09-looker.md), [`data/README.md`](../data/README.md).

---

## 7) Migración desde SAP Business One / CSV (narrativa)

Historia tipificada de cutover — **no** hay conexión SAP real en este lab:

1. Acuerdo de **data contract** (columnas, tipos, PKs, puentes `shipment_id`/`order_id`) con ops.
2. Export batch (CSV u objeto storage) desde SAP B1 / WMS / TM → landing.
3. **Bronze** tipado (load jobs; nulls permisivos en landing).
4. **Silver** con reglas + quarantine + reconciliación medible.
5. **Gold** star + vistas KPI; BI solo sobre gold.
6. Cutover: paralelo Excel vs mart → validar OTIF/cycle/stock → apagar planillas.
7. Escalón cloud: mismo contrato + Scheduler/Functions o Composer (narrado; no implementado aquí).

Frase segura: *“Simulo el linaje SAP-like → CSV → BigQuery medallón; el generador reproduce basura típica de exports (dupes, FK rotas, nulls, casing).”*

---

## 8) Preguntas probables (18) + respuestas modelo

1. **¿Qué problema resuelve?** Unir WMS y last-mile en un SSoT para atribuir atraso bodega vs calle.  
2. **¿Por qué medallón?** Separar landing, calidad y consumo; Looker no toca bronze.  
3. **¿Por qué quarantine?** Observabilidad; drop silencioso oculta defectos del export.  
4. **¿CTAS vs MERGE?** Sandbox sin DML → CTAS idempotente; prod = MERGE por partición del día.  
5. **¿Por qué integer-range?** Evitar wipe de historia por partition expiration ~60d del sandbox en DATE partitions.  
6. **¿Qué es partition pruning?** Filtro en partición → menos bytes; demo (b) ≈ 10,7% del full scan.  
7. **¿Clustering en dry_run?** No se acredita; solo job real (+ mínimo ~10 MB).  
8. **¿Cómo calculas OTIF?** `SUM(n_otif)/SUM(n_intentadas_cerradas)`; nunca `AVG(otif_pct)`.  
9. **¿29.803 vs 25.609?** Despachados totales vs cycle (con pick+ATA); poblaciones distintas.  
10. **¿Funnel sin stage?** Derivado de pick/ship/status; picking 40.823 no ⊆ despacho.  
11. **¿Por qué un mart?** Owners y preguntas cruzadas; split si ownership/SLA/seguridad divergen.  
12. **¿Comuna sin dim?** Degenerate en el fact; no inventar maestro.  
13. **¿SKU en entregas?** Bridge OUT por `shipment_id` + `COUNT DISTINCT`.  
14. **¿Cómo evitas keys en git?** Solo ADC en laptop; CI sin GCP.  
15. **¿Idempotencia?** `WRITE_TRUNCATE` + CTAS + regenerate seed 42 en cada `make run`.  
16. **¿Incidente bronze REQUIRED?** Null anomalies rechazadas → DROP NOT NULL + schema nullable en load.  
17. **¿Orphans ent_fecha?** Lag entrega Nov fuera de `d_fecha` → ampliar calendario a 2026-11-04 (583 días).  
18. **¿Qué no está en el lab?** Conexión SAP real, Composer/Scheduler, claims Esparta, one-pager aún abierto en #11.

---

## 9) Incidentes y lecciones (requerido)

### A) Bronze REQUIRED vs anomalías null (Paso 6)
**Síntoma:** load rechazó facts (`Required column value is missing`) — movimientos ~20, stock ~10, entregas ~15.  
**Causa:** DDL Paso 4 con columnas REQUIRED; `IF NOT EXISTS` no altera tablas; load con schema de tabla + `max_bad_records=0`.  
**Fix:** bronze todo NULLABLE + `ALTER … DROP NOT NULL` idempotente + `schema_as_nullable()` en load. Silver sigue estricto vía quarantine.  
**Lección:** landing permisivo / silver estricto; el schema de la tabla manda sobre el del job.

### B) Orphans `ent_fecha` (Paso 7)
**Síntoma:** `ORPHAN ent_fecha: 79` post `build_gold`.  
**Causa:** `delivery_date` Nov 1–4 (lag 0–4d sobre ship ≤ Oct-31); `d_fecha` solo hasta Oct-31.  
**Fix:** `d_fecha` → `2025-04-01`…`2026-11-04` (**583** días). No cambiar grano a `ship_date`.  
**Seguimiento Paso 8:** range end `20261101` dejaba Nov en `__UNPARTITIONED__` → end `20261201` (108 buckets).

### C) Rango de partición / `__UNPARTITIONED__` (Paso 8)
`GENERATE_ARRAY` es **end exclusivo**. End `20261101` = 107 buckets; `fecha_id ≥ 20261101` → `__UNPARTITIONED__` (rompe prune). Fix: `20261201`, assert 0 filas UNPARTITIONED/NULL, recreate idempotente si cambia el spec.

### D) Errores de métrica en dashboard (Paso 9 + Overview pulido)
1. `AVG(otif_pct)` → ~0,58% vs **58,06%** ponderado.  
2. `AVG(on_time)` en drill (grano shipment×SKU) ≠ % a tiempo.  
3. Contar “envíos” desde `v_cycle_time` (**25.609**) en vez de drill/entregas (**29.803**).  
4. Overrides de agregación por chart y cross-filters accidentales entre data sources.  
5. **Dimensión de período ausente** — «Envíos despachados» no respondía al control de fecha porque el scorecard no tenía dimensión de período. Fix canvas: `fecha` de `v_entregas_drill` (mismo eje que OTIF / `fecha_id`, **no** `ship_fecha_id`) + `COUNT_DISTINCT(shipment_id)` por fan-out shipment×SKU; sin tocar SQL. Ver [`docs/paso-11-overview-pulido.md`](paso-11-overview-pulido.md).  
**Lección:** la fórmula vive en el campo calculado / vista; revisar cada scorecard **y** su dimensión de período si debe filtrar con el control de fechas.

---

## 10) Checklist de repaso (día anterior)

- [ ] Pitch 2 min (problema → medallón → un mart → Looker → ADC; lab no prod).
- [ ] Recitar pasos 1–9 y un artefacto por paso; saber que Paso 10 es one-pager aparte.
- [ ] Conteos bronze / silver / gold / quarantine 160 (breakdown) de memoria.
- [ ] OTIF fórmula + trampa del AVG; poblaciones 29.803 vs 25.609.
- [ ] Funnel 40.823 / 29.803 / 21.325 y por qué picking no ⊆ despacho.
- [ ] Por qué integer-range (sandbox 60d) y 108 buckets / end exclusivo.
- [ ] Bytes prune: (a) 1,59 MiB → (b) 10,7%; (c) dry_run ≠ clustering.
- [ ] Cuatro decisiones: funnel, comuna degenerada, bridge SKU, un mart.
- [ ] Cuatro incidentes: bronze NULLABLE, orphans fecha, UNPARTITIONED, métricas Looker.
- [ ] Frase migración SAP/CSV (narrativa; sin claim de conexión real).
- [ ] Abrir viewer Looker y señalar OTIF / late / cycle / funnel con números reales.
- [ ] Límites honestos: sintético, sandbox, sin Composer, sin claims Esparta.

---

## Enlaces rápidos

| Recurso | Link |
|---|---|
| README | [`README.md`](../README.md) |
| Levantamiento | [`docs/levantamiento-requerimientos.md`](levantamiento-requerimientos.md) |
| Data contract | [`data/README.md`](../data/README.md) |
| Looker guía + números | [`looker/README.md`](../looker/README.md) |
| Viewer dashboard | https://datastudio.google.com/reporting/172aa667-fb63-4c36-84d4-4585c8ad89ba |
| Pasos 3–9 | [`docs/paso-03`](paso-03-bigquery-setup.md) … [`paso-09`](paso-09-looker.md) |
