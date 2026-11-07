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
| Datos sintéticos + contrato | ⏳ Paso 2 |
| DDL + carga Python + transforms | ⏳ |
| Demo partition/cluster | ⏳ |
| Looker Studio + one-page | ⏳ |

## Setup rápido (se completa en pasos posteriores)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
gcloud auth application-default login
gcloud config set project kora-sports

## Docs

- [Levantamiento de requerimientos](docs/levantamiento-requerimientos.md)
- Arquitectura, modelo dimensional y demo de bytes: TBD en pasos siguientes

## Disclaimer

ELT operable en sandbox: datos sintéticos, carga Python desde laptop y transforms SQL hasta un gold que alimenta Looker Studio. Con presupuesto cloud, el mismo contrato de datos se engancha a Scheduler/Functions o Composer.