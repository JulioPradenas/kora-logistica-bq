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
| Transforms silver + quarantine | ⏳ |
| Demo partition/cluster (bytes) | ⏳ |
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

`make run` ejecuta en orden la lista `STAGES` del Makefile (hoy: datasets → DDL → **generate CSV (siempre)** → **load bronze**; futuro: transforms = una línea nueva). Cada etapa es dry-run → apply idempotente; si falla, para. Al final: `bq ls` + **conteos bronze** + `bq show --schema` de un fact gold + summary.  
`GCP_PROJECT` se lee de `.env` (default `kora-sports`); el Makefile no imprime secretos ni SA keys.

`data/raw/*.csv` está gitignored → `make run` regenera siempre con seed 42 (determinista) antes del load.

Detalle: [docs/paso-03-bigquery-setup.md](docs/paso-03-bigquery-setup.md) · [docs/paso-04-ddl.md](docs/paso-04-ddl.md) · [docs/paso-05-load-bronze.md](docs/paso-05-load-bronze.md).  
CSV: `python scripts/generate_synthetic.py` (Paso 2); ver [data/README.md](data/README.md).

## Docs

- [Levantamiento de requerimientos](docs/levantamiento-requerimientos.md)
- [Data contract (`data/README.md`)](data/README.md)
- [Paso 3 — setup BigQuery datasets](docs/paso-03-bigquery-setup.md)
- [Paso 4 — DDL vacío medallón](docs/paso-04-ddl.md)
- [Paso 5 — carga bronze (load jobs)](docs/paso-05-load-bronze.md)
- Transforms silver/gold y demo de bytes: TBD en pasos siguientes

## Disclaimer

ELT operable en sandbox: datos sintéticos, carga Python desde laptop y transforms SQL hasta un gold que alimenta Looker Studio. Con presupuesto cloud, el mismo contrato de datos se engancha a Scheduler/Functions o Composer.
