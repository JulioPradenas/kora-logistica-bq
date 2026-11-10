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
| Looker Studio + one-page | ⏳ |

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

Placeholders: rellenar con la salida real de `make run` / `python scripts/demo_partition_prune.py` en la laptop (ADC). **No inventar números.**

| Query | Filtro | `totalBytesProcessed` (dry_run) | % vs (a) |
|---|---|---|---|
| (a) | ninguno | _TBD_ | 100% |
| (b) | un mes `fecha_id` | _TBD_ | _TBD_ |
| (c) | mes + `bodega_id` | _TBD_ | _TBD_ |
| (c) real (opcional `--real-cluster`) | mes + `bodega_id` | _TBD_ | _TBD_ |

**Nota:** dry_run estima; el ahorro de clustering a menudo **no** aparece en dry_run (granularidad por partición / mínimo ~10 MB en jobs reales). Ver [docs/paso-08-pruning.md](docs/paso-08-pruning.md).

Detalle: [docs/paso-03-bigquery-setup.md](docs/paso-03-bigquery-setup.md) · [docs/paso-04-ddl.md](docs/paso-04-ddl.md) · [docs/paso-05-load-bronze.md](docs/paso-05-load-bronze.md) · [docs/paso-06-silver.md](docs/paso-06-silver.md) · [docs/paso-07-gold.md](docs/paso-07-gold.md) · [docs/paso-08-pruning.md](docs/paso-08-pruning.md).  
CSV: `python scripts/generate_synthetic.py` (Paso 2); ver [data/README.md](data/README.md).

## Docs

- [Levantamiento de requerimientos](docs/levantamiento-requerimientos.md)
- [Data contract (`data/README.md`)](data/README.md)
- [Paso 3 — setup BigQuery datasets](docs/paso-03-bigquery-setup.md)
- [Paso 4 — DDL vacío medallón](docs/paso-04-ddl.md)
- [Paso 5 — carga bronze (load jobs)](docs/paso-05-load-bronze.md)
- [Paso 6 — transform silver + quarantine](docs/paso-06-silver.md)
- [Paso 7 — gold datamart](docs/paso-07-gold.md)
- [Paso 8 — partition prune + 108 buckets](docs/paso-08-pruning.md)
- Looker Studio + one-page: TBD Paso 9

## Disclaimer

ELT operable en sandbox: datos sintéticos, carga Python desde laptop y transforms SQL hasta un gold que alimenta Looker Studio. Con presupuesto cloud, el mismo contrato de datos se engancha a Scheduler/Functions o Composer.
