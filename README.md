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
| Carga Python + transforms | ⏳ |
| Demo partition/cluster (bytes) | ⏳ |
| Looker Studio + one-page | ⏳ |

## Setup rápido (se completa en pasos posteriores)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/generate_synthetic.py   # regenera data/raw (seed 42, HORIZON_END fijo 2026-10-31)
cp .env.example .env
gcloud auth application-default login
gcloud config set project kora-sports
python scripts/setup_bigquery.py --dry-run
python scripts/setup_bigquery.py       # datasets bronze_kora / silver_kora / gold_kora
python scripts/apply_ddl.py --dry-run
python scripts/apply_ddl.py            # DDL vacío bronze/silver/gold (IF NOT EXISTS)
```

Los CSV en `data/raw/` no se versionan; ver [data/README.md](data/README.md) (contrato + regeneración).

Datasets BigQuery: location **`southamerica-west1`** (Santiago). Detalle y límites sandbox en [docs/paso-03-bigquery-setup.md](docs/paso-03-bigquery-setup.md).  
DDL (partición integer-range, sin DML, sin carga): [docs/paso-04-ddl.md](docs/paso-04-ddl.md).

Para tests locales (contrato/generador + setup/DDL BQ con mocks):

```bash
pip install -r requirements-dev.txt
pytest
```

## Docs

- [Levantamiento de requerimientos](docs/levantamiento-requerimientos.md)
- [Data contract (`data/README.md`)](data/README.md)
- [Paso 3 — setup BigQuery datasets](docs/paso-03-bigquery-setup.md)
- [Paso 4 — DDL vacío medallón](docs/paso-04-ddl.md)
- Carga, transforms y demo de bytes: TBD en pasos siguientes

## Disclaimer

ELT operable en sandbox: datos sintéticos, carga Python desde laptop y transforms SQL hasta un gold que alimenta Looker Studio. Con presupuesto cloud, el mismo contrato de datos se engancha a Scheduler/Functions o Composer.
