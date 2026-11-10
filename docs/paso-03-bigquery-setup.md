# Paso 3 — GCP sandbox + datasets medallón

**Proyecto:** KORA Deportes · Logística (WMS + last mile)  
**Prerrequisito:** Paso 2 mergeado en `main` (datos sintéticos + data contract)  
**Siguiente:** [Paso 4 — DDL vacío medallón](paso-04-ddl.md)

---

## Meta de esta sesión

Al terminar tendrás:

1. Datasets `bronze_kora`, `silver_kora`, `gold_kora` en el proyecto `kora-sports`.
2. Misma **location** en los tres: `southamerica-west1` (Santiago).
3. Script idempotente `scripts/setup_bigquery.py` (+ `--dry-run`).
4. Verificación por consola / `bq ls`.

**Fuera de alcance:** DDL de tablas, loaders, carga de CSV, transforms silver/gold.

---

## Location elegida: `southamerica-west1`

| Criterio | Decisión |
|---|---|
| Latencia Chile | Región Santiago = menor RTT vs `US` / `EU` desde laptop en Chile |
| Residencia / narrativa retail CL | Datos del lab “viven” en la misma geografía del negocio ficticio |
| Looker Studio | Conector BigQuery del mismo proyecto/usuario; no exige multi-región `US` |
| Sandbox | Los límites (10 GiB lifetime, 1 TiB query/mes, expiración 60d) **no** dependen de la región |
| Consistencia | Los **tres** datasets usan la misma location — joins locales en pasos posteriores |

**Descartado:** multi-región `US` (más tutoriales, peor latencia Chile).  
Fuente regiones: [BigQuery locations](https://cloud.google.com/bigquery/docs/locations).

---

## Límites sandbox (oficiales)

Fuente: [Try BigQuery using the sandbox](https://cloud.google.com/bigquery/docs/sandbox) (Limitations).

| Límite | Valor oficial |
|---|---|
| Storage lifetime | **10 GiB** (no se “devuelve” al borrar datos) |
| Query (análisis) | **1 TiB**/mes (mismo free tier) |
| Default table expiration | **~60 días** — tablas, vistas y particiones expiran |
| Features no soportadas | Streaming, **DML**, BigQuery Data Transfer Service |
| Quotas generales | Aplican todos los [quotas and limits](https://cloud.google.com/bigquery/quotas) de BigQuery |

Free tier (billing account; referencia): [BigQuery pricing — Free usage tier](https://cloud.google.com/bigquery/pricing#free-tier) — 10 GiB storage/mes y 1 TiB query/mes en free tier; en **sandbox** el storage es lifetime 10 GiB según la doc de sandbox.

**Implicación lab:** re-cargar bronze tras ~60 días (o upgrade + quitar expiration). No asumir tablas eternas en sandbox.

Nota datasets: si el proyecto no tiene billing, BigQuery fija default table expiration; puedes acortarla, no alargarla — [Create datasets](https://cloud.google.com/bigquery/docs/datasets).

---

## Prerrequisitos en tu Mac (ya verificados por Julio)

- `gcloud` SDK OK; proyecto default `kora-sports`
- ADC: `gcloud auth application-default login` + quota project `kora-sports`
- APIs: `bigquery.googleapis.com`, `bigquerystorage.googleapis.com`
- Sandbox, sin billing

---

## Comandos exactos (Mac)

Desde la raíz del repo:

```bash
cd ~/projects/kora-logistica-bq   # ajusta tu ruta

git checkout main
git pull origin main

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
pip install -r requirements-dev.txt   # para pytest local

cp -n .env.example .env               # no pisa si ya existe
# Confirma: GCP_PROJECT=kora-sports

gcloud config get-value project        # → kora-sports
gcloud auth application-default print-access-token >/dev/null && echo "ADC OK"
```

### Dry-run (sin crear)

```bash
python scripts/setup_bigquery.py --dry-run
```

**Salida esperada (aprox.):**

```
project=kora-sports
location=southamerica-west1
dry_run=True
  would_create  bronze_kora  (southamerica-west1)
  would_create  silver_kora  (southamerica-west1)
  would_create  gold_kora  (southamerica-west1)
dry-run: no se crearon recursos. Verificación omitida (sin writes).
Datasets planificados: bronze_kora, silver_kora, gold_kora
```

(Si ya existen: `would_skip` en lugar de `would_create`.)

### Crear / re-run idempotente

```bash
python scripts/setup_bigquery.py
python scripts/setup_bigquery.py    # segunda vez: action=exists, exit 0
```

**Salida esperada (primera vez):**

```
project=kora-sports
location=southamerica-west1
dry_run=False
  created       bronze_kora  (southamerica-west1)
  created       silver_kora  (southamerica-west1)
  created       gold_kora  (southamerica-west1)
verify:
  kora-sports.bronze_kora  location=southamerica-west1
  kora-sports.gold_kora  location=southamerica-west1
  kora-sports.silver_kora  location=southamerica-west1
OK: bronze_kora / silver_kora / gold_kora presentes en southamerica-west1
```

**Segunda vez:** `exists` en las tres líneas; mismo `OK`.

---

## Verificar en consola y CLI

### Cloud Console

1. https://console.cloud.google.com/bigquery?project=kora-sports  
2. Explorer → proyecto `kora-sports` → datasets `bronze_kora`, `silver_kora`, `gold_kora`.  
3. Cada dataset → Details → Location = `southamerica-west1`.

### `bq ls`

```bash
bq ls --project_id=kora-sports
bq show --format=prettyjson kora-sports:bronze_kora | head
bq show --format=prettyjson kora-sports:silver_kora | head
bq show --format=prettyjson kora-sports:gold_kora | head
```

Esperado: los tres dataset IDs listados; `"location": "southamerica-west1"`.

---

## Tests (sin GCP)

```bash
pytest tests/test_setup_bigquery.py -q
# o suite completa:
pytest -q
```

Los tests mockean el cliente BigQuery; CI no necesita secretos.

---

## Auth: ADC vs service account key

- **Este lab:** ADC (`gcloud auth application-default login`). El script **no** pide ni escribe JSON keys.
- `.env.example` menciona `GOOGLE_APPLICATION_CREDENTIALS` solo como escape hatch local opcional — **nunca** commitear la key (`.gitignore`).

---

## Checklist

- [ ] `--dry-run` OK  
- [ ] `python scripts/setup_bigquery.py` crea los 3 datasets  
- [ ] Re-run sin error  
- [ ] `bq ls` / consola muestran location `southamerica-west1`  
- [ ] `pytest -q` verde  
- [ ] **Aún no** hay tablas DDL ni carga (Paso 4+)

Cuando esté ✅ → Paso 4 (DDL bronze / loaders) en un PR aparte.
