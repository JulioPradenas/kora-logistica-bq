# KORA Deportes — Logística (WMS + last mile) en BigQuery

> **Lab de portafolio / migración demostrativa.** Marca ficticia con fines ilustrativos.  
> No es un entregable de empleo ni un claim sobre una empresa real.

Analista: [Julio Pradenas](https://github.com/JulioPradenas)

## Problema

KORA no une movimientos/stock de bodega con cumplimiento de entrega last-mile.  
OTIF, lead time picking→door y salud de stock viven en Excels distintos; no hay **SSoT**.

## Solución (sandbox)

CSV sintéticos (SAP-like) → Python (laptop) → BigQuery bronze → silver → gold
                                                              ↓
                                                        Looker Studio


- **Proyecto GCP:** `kora-sports` (sin billing; free-tier conscious)
- **Datasets:** `bronze_kora` · `silver_kora` · `gold_kora`
- **Datamart:** uno solo (`dm_logistica_retail` conceptual) — WMS + last-mile
- **Auth:** Application Default Credentials (`gcloud auth application-default login`)
- **Dashboard (Looker Studio):** [viewer](https://datastudio.google.com/reporting/172aa667-fb63-4c36-84d4-4585c8ad89ba)
- **One-pager:** [`site/index.html`](site/index.html)

Cloud Scheduler / Cloud Functions / Composer: **solo narrados** en docs; no implementados en este lab.

## Estado del lab

| Fase | Estado |
|---|---|
| Levantamiento de requerimientos | ✅ (este repo) |
| Datos sintéticos + contrato | ✅ |
| Datasets BQ medallón (`bronze`/`silver`/`gold`) | ✅ — `southamerica-west1` |
| DDL vacío (shapes + star; integer-range) | ✅ — sin carga |
| Carga bronze (CSV → load jobs) | ✅ — `WRITE_TRUNCATE` |
| Transforms silver + quarantine | ✅ — CTAS + `quarantine_rows` |
| Gold datamart (`dm_logistica_retail`) | ✅ — star + vistas OTIF/cycle/stock |
| Demo partition/cluster (bytes) | ✅ — 108 buckets; prune dry_run stage 7 |
| Looker Studio (vistas gold + guía) | ✅ — [viewer](https://datastudio.google.com/reporting/172aa667-fb63-4c36-84d4-4585c8ad89ba) |
| One-page portafolio | ✅ — [`site/index.html`](site/index.html) |

## Setup rápido (Mac — un entrypoint)

```bash
cp -n .env.example .env                 # GCP_PROJECT=kora-sports
gcloud auth application-default login
gcloud config set project kora-sports

make setup                              # .venv + deps (idempotente)
make run                                # todas las etapas GCP (dry-run → apply) + verify
make run DRY=1                          # solo dry-run de cada etapa (sin writes / sin bq)
make test                               # pytest (mocks; sin GCP)
```

`make run` ejecuta en orden la lista `STAGES` del Makefile (hoy: datasets → DDL → **generate CSV (siempre)** → **load bronze** → **transform silver** → **build gold** → **demo partition prune**). Cada etapa es dry-run → apply idempotente; si falla, para. Al final: `bq ls` + **conteos bronze/silver/gold** + **quarantine by reason** + schema/partition + assert **0 filas** `__UNPARTITIONED__`/`__NULL__` + summary.  
`GCP_PROJECT` se lee de `.env` (default `kora-sports`); el Makefile no imprime secretos ni SA keys.

`data/raw/*.csv` está gitignored → `make run` regenera siempre con seed 42 (determinista; + anomalías Paso 6) antes del load. Si el partition-spec del DDL cambió, `apply_ddl` hace **DROP+CREATE** (seguro: los datos se regeneran en el mismo `make run`).

### Demo prune — resultados (`f_stock_diario`)

Números reales de `make run` (Julio, ADC laptop; dry_run stage 7). **No inventados.**

| Query | Filtro | `totalBytesProcessed` (dry_run) | % vs (a) |
|---|---|---|---|
| (a) | ninguno | 1 667 520 B (1.59 MiB) | 100% |
| (b) | un mes `fecha_id` | 178 560 B (174.38 KiB) | 10.7% |
| (c) | mes + `bodega_id` | 256 680 B (250.66 KiB) | 15.4% |
| (c) real (opcional `--real-cluster`) | mes + `bodega_id` | _(correr en laptop; mín. ~10 MB facturados)_ | — |

**Post-run del mismo `make run`:** 6 tablas recreadas a 108 buckets; `f_entregas` range end=`20261201`; **0** filas en `__UNPARTITIONED__`/`__NULL__`; gold counts 583/8/250/12/55548/208440/29803.

**Por qué (c) > (b) en dry_run:** BigQuery factura **columnas leídas**. (c) añade `bodega_id` al SELECT/filtro → más bytes estimados. El dry_run estima a **nivel de partición** y **no acredita** el skip de bloques de clustering. El ahorro de `CLUSTER BY` solo aparece en bytes facturados de un **job real** (`--real-cluster`); además hay **mínimo ~10 MB** por query. Ver [docs/paso-08-pruning.md](docs/paso-08-pruning.md).

Detalle: [docs/paso-03-bigquery-setup.md](docs/paso-03-bigquery-setup.md) · [docs/paso-04-ddl.md](docs/paso-04-ddl.md) · [docs/paso-05-load-bronze.md](docs/paso-05-load-bronze.md) · [docs/paso-06-silver.md](docs/paso-06-silver.md) · [docs/paso-07-gold.md](docs/paso-07-gold.md) · [docs/paso-08-pruning.md](docs/paso-08-pruning.md).  
CSV: `python scripts/generate_synthetic.py` (Paso 2); ver [data/README.md](data/README.md).

## Un datamart (y cuándo lo partiría)

Hoy OTIF, cycle time y stock viven en **un** datamart (`dm_logistica_retail` en `gold_kora`): mismos owners, preguntas cruzadas bodega↔calle y mismo refresh batch.

Lo partiría cuando ownership, grano, SLA de refresh o seguridad diverjan (p. ej. stock subhorario vs OTIF diario, o IAM distinto para costo de flete). Criterio completo: [levantamiento §8](docs/levantamiento-requerimientos.md#8-decisión-un-datamart-vs-varios) · [paso-07](docs/paso-07-gold.md).

## Definition of Done

| # | Ítem | Estado | Evidencia |
|---|---|---|---|
| 1 | Repo GitHub con esqueleto canónico | ✅ | este repo · [estructura](.) |
| 2 | `docs/levantamiento-requerimientos.md` completo | ✅ | [docs/levantamiento-requerimientos.md](docs/levantamiento-requerimientos.md) |
| 3 | Generador sintético ≥12 meses / ≥~30k fact rows | ✅ | [data/README.md](data/README.md) · `scripts/generate_synthetic.py` |
| 4 | Datasets BQ `bronze_kora` / `silver_kora` / `gold_kora` en `kora-sports` | ✅ | [docs/paso-03-bigquery-setup.md](docs/paso-03-bigquery-setup.md) |
| 5 | Gold DDL `PARTITION BY` + `CLUSTER BY` documentados | ✅ | [sql/ddl_gold.sql](sql/ddl_gold.sql) · [docs/paso-04-ddl.md](docs/paso-04-ddl.md) |
| 6 | Python carga bronze y silver→gold vía ADC (sin key en git) | ✅ | [scripts/load_bronze.py](scripts/load_bronze.py) · [scripts/transform_silver.py](scripts/transform_silver.py) · [scripts/build_gold.py](scripts/build_gold.py) |
| 7 | `sql/demo_partition_prune.sql` + Bytes processed | ✅ | [sql/demo_partition_prune.sql](sql/demo_partition_prune.sql) · [docs/paso-08-pruning.md](docs/paso-08-pruning.md) |
| 8 | Looker Studio sobre gold; link en README | ✅ | [viewer](https://datastudio.google.com/reporting/172aa667-fb63-4c36-84d4-4585c8ad89ba) · [looker/README.md](looker/README.md) |
| 9 | `site/index.html` one-page ES (hero=KORA) + disclaimer lab | ✅ | [site/index.html](site/index.html) |
| 10 | README: ADC, orden de comandos, límites sandbox; Scheduler/CF narrados | ✅ | Setup rápido (arriba) · Disclaimer (abajo) |
| 11 | Sección: por qué un datamart y cuándo partirlo | ✅ | [Un datamart](#un-datamart-y-cuándo-lo-partiría) · [levantamiento §8](docs/levantamiento-requerimientos.md#8-decisión-un-datamart-vs-varios) |

## Docs

- [**Guía de estudio — entrevista BI**](docs/guia-estudio.md) (pitch, pasos 1–10, métricas, Q&A)
- [Levantamiento de requerimientos](docs/levantamiento-requerimientos.md)
- [Data contract (`data/README.md`)](data/README.md)
- [Paso 3 — setup BigQuery datasets](docs/paso-03-bigquery-setup.md)
- [Paso 4 — DDL vacío medallón](docs/paso-04-ddl.md)
- [Paso 5 — carga bronze (load jobs)](docs/paso-05-load-bronze.md)
- [Paso 6 — transform silver + quarantine](docs/paso-06-silver.md)
- [Paso 7 — gold datamart](docs/paso-07-gold.md)
- [Paso 8 — partition prune + 108 buckets](docs/paso-08-pruning.md)
- [Paso 9 — Looker Studio + vistas gold](docs/paso-09-looker.md)
- [Paso 11 — Overview pulido (Looker)](docs/paso-11-overview-pulido.md)
- [Guía Looker Studio (`looker/README.md`)](looker/README.md)
- [One-page portafolio (`site/index.html`)](site/index.html)

### GitHub Pages (opcional, manual)

Para publicar el one-pager sin tocar el pipeline:

1. Repo → **Settings** → **Pages**.
2. Source: **Deploy from a branch**.
3. Branch: `main` · folder: `/` (root del repo).
4. Save. El one-pager queda en `…/site/` (p. ej. `https://juliopradenas.github.io/kora-logistica-bq/site/`).

No activar Pages desde CI ni desde este lab: es un paso opcional en la cuenta de Julio.

## Disclaimer

ELT operable en sandbox: datos sintéticos, carga Python desde laptop y transforms SQL hasta un gold que alimenta Looker Studio. Con presupuesto cloud, el mismo contrato de datos se engancha a Scheduler/Functions o Composer. No hay conexión real a SAP/WMS ni requisitos atribuidos a un empleador.
