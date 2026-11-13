#!/usr/bin/env python3
"""
KORA Deportes — Paso 3: crear datasets medallón en BigQuery (idempotente).

Uso (Mac / laptop de Julio, con ADC ya configurado):
  cp .env.example .env          # si aún no existe
  python scripts/setup_bigquery.py --dry-run
  python scripts/setup_bigquery.py
  python scripts/setup_bigquery.py   # re-run: no falla ni duplica

Auth: solo Application Default Credentials (ADC). Nunca hardcodea credenciales
ni usa llaves JSON de service account:
  gcloud auth application-default login

Proyecto: leído de .env / entorno — GCP_PROJECT o GOOGLE_CLOUD_PROJECT.
Nunca hardcodear el project id en llamadas de producción del script;
el default documentado del lab es kora-sports (vía .env.example).

Location (constante del lab): southamerica-west1 (Santiago)
Justificación:
  - Negocio Chile (bodegas / last-mile en America/Santiago) → menor latencia
    de control plane y jobs desde laptop en Chile vs US/EU.
  - Región BQ oficial con presencia en Santiago
    (https://cloud.google.com/bigquery/docs/locations).
  - Looker Studio se conecta a datasets regionales vía el conector BigQuery
    del mismo proyecto; no exige multi-región US para este lab.
  - Sandbox: la location no cambia los límites de storage/query; sí fija
    residencia. TODOS los datasets del medallón deben compartir la MISMA
    location — joins cross-location no son locales y complican el lab.
  - Alternativa descartada: US multi-región (ecosistema tutorial más amplio,
    peor latencia Chile y peor narrativa de residencia).

Sandbox limits (oficiales; ver docs/paso-03-bigquery-setup.md):
  https://cloud.google.com/bigquery/docs/sandbox
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

# Región única del lab — no cambiar por dataset.
BQ_LOCATION = "southamerica-west1"

DATASET_SPECS: tuple[dict[str, object], ...] = (
    {
        "dataset_id": "bronze_kora",
        "description": (
            "KORA logística — bronze: exports SAP-like crudos (landing tipado). "
            "Lab portafolio; sin PII real."
        ),
        "labels": {
            "project": "kora-logistica",
            "layer": "bronze",
            "env": "sandbox",
            "owner": "julio-pradenas",
        },
    },
    {
        "dataset_id": "silver_kora",
        "description": (
            "KORA logística — silver: limpio / tipado / claves normalizadas. "
            "Lab portafolio; sin PII real."
        ),
        "labels": {
            "project": "kora-logistica",
            "layer": "silver",
            "env": "sandbox",
            "owner": "julio-pradenas",
        },
    },
    {
        "dataset_id": "gold_kora",
        "description": (
            "KORA logística — gold: datamart dm_logistica_retail (WMS + last-mile). "
            "Fuente Looker Studio. Lab portafolio; sin PII real."
        ),
        "labels": {
            "project": "kora-logistica",
            "layer": "gold",
            "env": "sandbox",
            "owner": "julio-pradenas",
        },
    },
)


@dataclass(frozen=True)
class EnsureResult:
    dataset_id: str
    action: str  # "created" | "exists" | "would_create" | "would_skip"
    location: str


def load_project_id(env_file: Path | None = None) -> str:
    """Carga GCP_PROJECT / GOOGLE_CLOUD_PROJECT desde .env + entorno."""
    dotenv_path = env_file if env_file is not None else ROOT / ".env"
    if dotenv_path.is_file():
        load_dotenv(dotenv_path)
    else:
        # .env.example no se carga automáticamente; solo documenta defaults.
        load_dotenv(ROOT / ".env.example")

    project = (
        os.getenv("GCP_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCLOUD_PROJECT")
        or ""
    ).strip()
    if not project:
        raise SystemExit(
            "Falta GCP_PROJECT (o GOOGLE_CLOUD_PROJECT). "
            "Copia .env.example → .env o exporta la variable."
        )
    return project


def build_client(project_id: str):
    """BigQuery client con ADC (sin path de key hardcodeado)."""
    from google.cloud import bigquery

    return bigquery.Client(project=project_id)


def ensure_dataset(
    client,
    *,
    project_id: str,
    dataset_id: str,
    description: str,
    labels: dict[str, str],
    location: str = BQ_LOCATION,
    dry_run: bool = False,
) -> EnsureResult:
    """
    Crea el dataset si no existe. Si existe, no falla (idempotente).
    En dry-run no llama create; solo reporta intención.
    """
    from google.api_core.exceptions import NotFound
    from google.cloud import bigquery

    full_id = f"{project_id}.{dataset_id}"
    try:
        existing = client.get_dataset(full_id)
    except NotFound:
        existing = None

    if existing is not None:
        # Asegura metadata mínima sin recrear (idempotente).
        if not dry_run:
            dirty = False
            if (existing.description or "") != description:
                existing.description = description
                dirty = True
            # Labels: merge; no borrar labels ajenas.
            merged = dict(existing.labels or {})
            for k, v in labels.items():
                if merged.get(k) != v:
                    merged[k] = v
                    dirty = True
            if dirty:
                existing.labels = merged
                client.update_dataset(existing, ["description", "labels"])
        return EnsureResult(dataset_id=dataset_id, action="exists" if not dry_run else "would_skip", location=existing.location)

    if dry_run:
        return EnsureResult(dataset_id=dataset_id, action="would_create", location=location)

    ds = bigquery.Dataset(full_id)
    ds.location = location
    ds.description = description
    ds.labels = labels
    created = client.create_dataset(ds, exists_ok=True)
    return EnsureResult(dataset_id=dataset_id, action="created", location=created.location)


def list_lab_datasets(client, project_id: str) -> list[dict[str, str]]:
    """Lista datasets del lab presentes en el proyecto."""
    wanted = {spec["dataset_id"] for spec in DATASET_SPECS}
    found: list[dict[str, str]] = []
    for ds in client.list_datasets(project=project_id):
        if ds.dataset_id in wanted:
            full = client.get_dataset(ds.dataset_id)
            found.append(
                {
                    "dataset_id": full.dataset_id,
                    "location": full.location,
                    "full_id": f"{full.project}.{full.dataset_id}",
                    "description": (full.description or "")[:80],
                }
            )
    return sorted(found, key=lambda r: r["dataset_id"])


def verify_datasets(found: list[dict[str, str]], location: str = BQ_LOCATION) -> list[str]:
    """Devuelve lista de errores de verificación (vacía = OK)."""
    errors: list[str] = []
    wanted = [spec["dataset_id"] for spec in DATASET_SPECS]
    by_id = {r["dataset_id"]: r for r in found}
    for ds_id in wanted:
        if ds_id not in by_id:
            errors.append(f"missing:{ds_id}")
            continue
        if by_id[ds_id]["location"] != location:
            errors.append(
                f"location_mismatch:{ds_id}={by_id[ds_id]['location']} expected={location}"
            )
    return errors


def run(*, dry_run: bool = False, project_id: str | None = None, client=None) -> int:
    project = project_id or load_project_id()
    print(f"project={project}")
    print(f"location={BQ_LOCATION}")
    print(f"dry_run={dry_run}")

    bq_client = client if client is not None else build_client(project)

    results: list[EnsureResult] = []
    for spec in DATASET_SPECS:
        result = ensure_dataset(
            bq_client,
            project_id=project,
            dataset_id=str(spec["dataset_id"]),
            description=str(spec["description"]),
            labels=dict(spec["labels"]),  # type: ignore[arg-type]
            location=BQ_LOCATION,
            dry_run=dry_run,
        )
        results.append(result)
        print(f"  {result.action:12}  {result.dataset_id}  ({result.location})")

    if dry_run:
        print("dry-run: no se crearon recursos. Verificación omitida (sin writes).")
        print("Datasets planificados:", ", ".join(str(s["dataset_id"]) for s in DATASET_SPECS))
        return 0

    found = list_lab_datasets(bq_client, project)
    print("verify:")
    for row in found:
        print(f"  {row['full_id']}  location={row['location']}")
    errors = verify_datasets(found, BQ_LOCATION)
    if errors:
        print("VERIFY FAILED:", "; ".join(errors), file=sys.stderr)
        return 1
    print("OK: bronze_kora / silver_kora / gold_kora presentes en", BQ_LOCATION)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crea datasets medallón KORA en BigQuery (idempotente)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra qué se crearía sin llamar create_dataset.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
