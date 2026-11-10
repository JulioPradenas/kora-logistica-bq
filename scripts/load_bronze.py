#!/usr/bin/env python3
"""
KORA Deportes — Paso 5: cargar CSV sintéticos en bronze_kora (load jobs).

Uso (Mac / laptop de Julio, con ADC ya configurado):
  python scripts/load_bronze.py --dry-run
  python scripts/load_bronze.py
  python scripts/load_bronze.py          # re-run: WRITE_TRUNCATE → mismos conteos

Auth: Application Default Credentials (ADC). Nunca hardcodea credenciales
ni espera un JSON de service account en el repo.

Proyecto: .env / entorno — GCP_PROJECT o GOOGLE_CLOUD_PROJECT (default lab: kora-sports).
Location: southamerica-west1 (fija; misma de datasets/DDL).

Sandbox:
  - Load jobs OK; sin DML (no INSERT/UPDATE/DELETE/MERGE).
  - WRITE_TRUNCATE sobre tablas ya creadas (Paso 4) reemplaza filas.
  - Bronze de este lab no está particionado; en tablas particionadas/clustered,
    un load WRITE_TRUNCATE a tabla existente borra datos y escribe de nuevo
    sin DROP TABLE (definición de partición/clustering permanece en el objeto).
    Citas: docs/paso-05-load-bronze.md.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"

BQ_LOCATION = "southamerica-west1"
BRONZE_DATASET = "bronze_kora"

# Orden = maestros primero, luego hechos (estable para logs / Makefile verify).
# CSV stem == table_id (contrato data/README.md).
BRONZE_TABLES: tuple[str, ...] = (
    "md_bodega",
    "md_sku",
    "md_transportista",
    "sap_wms_movimientos",
    "sap_wms_stock_snapshot",
    "sap_tm_entregas",
)

# Conteos esperados con seed 42 + horizonte fijo + anomalías Paso 6.
# Núcleo limpio 60120/208440/29803 + extras documentados (data/README.md).
EXPECTED_COUNTS: dict[str, int] = {
    "md_bodega": 8,
    "md_sku": 250,
    "md_transportista": 12,
    "sap_wms_movimientos": 60_285,
    "sap_wms_stock_snapshot": 208_555,
    "sap_tm_entregas": 29_923,
}


@dataclass(frozen=True)
class LoadResult:
    table_id: str
    action: str  # loaded | would_load | error | mismatch
    csv_rows: int = 0
    bq_rows: int | None = None
    detail: str = ""


def csv_path_for(table_id: str, raw_dir: Path = RAW_DIR) -> Path:
    """Mapa 1:1 CSV → tabla bronze."""
    return raw_dir / f"{table_id}.csv"


def load_project_id(env_file: Path | None = None) -> str:
    """Carga GCP_PROJECT / GOOGLE_CLOUD_PROJECT desde .env + entorno."""
    dotenv_path = env_file if env_file is not None else ROOT / ".env"
    if dotenv_path.is_file():
        load_dotenv(dotenv_path)
    else:
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
    """BigQuery client con ADC; location fija del lab."""
    from google.cloud import bigquery

    return bigquery.Client(project=project_id, location=BQ_LOCATION)


def read_csv_header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(f"CSV vacío (sin header): {path}") from exc
    return header


def count_csv_data_rows(path: Path) -> int:
    """Cuenta filas de datos (excluye header)."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        try:
            next(reader)  # header
        except StopIteration as exc:
            raise ValueError(f"CSV vacío (sin header): {path}") from exc
        return sum(1 for _ in reader)


def schema_field_names(schema) -> list[str]:
    return [field.name for field in schema]


def validate_csv_against_table(
    path: Path,
    schema,
) -> tuple[list[str], int]:
    """
    Valida que el header CSV coincida en nombre y orden con el schema bronze.
    Retorna (header, csv_rows).
    """
    header = read_csv_header(path)
    expected = schema_field_names(schema)
    if header != expected:
        raise ValueError(
            f"Header mismatch en {path.name}:\n"
            f"  csv={header}\n"
            f"  bq ={expected}"
        )
    rows = count_csv_data_rows(path)
    return header, rows


def build_load_job_config(schema):
    """
    LoadJobConfig: schema explícito (tabla existente), WRITE_TRUNCATE,
    skip_leading_rows=1, sin autodetect.
    """
    from google.cloud import bigquery

    return bigquery.LoadJobConfig(
        schema=list(schema),
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        autodetect=False,
    )


def verify_row_count(*, csv_rows: int, bq_rows: int, table_id: str) -> None:
    """Exit path: mismatch → ValueError (caller → exit 1)."""
    if csv_rows != bq_rows:
        raise ValueError(
            f"Row count mismatch {table_id}: csv={csv_rows} bq={bq_rows}"
        )


def load_one_table(
    client,
    *,
    project_id: str,
    table_id: str,
    raw_dir: Path = RAW_DIR,
    dry_run: bool = False,
) -> LoadResult:
    """Carga un CSV → bronze_kora.<table_id> o valida en dry-run."""
    path = csv_path_for(table_id, raw_dir)
    if not path.is_file():
        return LoadResult(
            table_id=table_id,
            action="error",
            detail=f"CSV missing: {path}",
        )

    table_ref = f"{project_id}.{BRONZE_DATASET}.{table_id}"
    try:
        table = client.get_table(table_ref)
    except Exception as exc:  # noqa: BLE001
        return LoadResult(
            table_id=table_id,
            action="error",
            detail=f"get_table failed: {exc}",
        )

    try:
        _header, csv_rows = validate_csv_against_table(path, table.schema)
    except ValueError as exc:
        return LoadResult(
            table_id=table_id,
            action="error",
            csv_rows=0,
            detail=str(exc),
        )

    if dry_run:
        expected = EXPECTED_COUNTS.get(table_id)
        detail = f"header_ok csv_rows={csv_rows}"
        if expected is not None:
            detail += f" expected={expected}"
        return LoadResult(
            table_id=table_id,
            action="would_load",
            csv_rows=csv_rows,
            detail=detail,
        )

    job_config = build_load_job_config(table.schema)
    try:
        with path.open("rb") as fh:
            job = client.load_table_from_file(
                fh,
                table_ref,
                job_config=job_config,
                location=BQ_LOCATION,
            )
            job.result()
        refreshed = client.get_table(table_ref)
        bq_rows = int(refreshed.num_rows or 0)
        verify_row_count(csv_rows=csv_rows, bq_rows=bq_rows, table_id=table_id)
    except ValueError as exc:
        return LoadResult(
            table_id=table_id,
            action="mismatch",
            csv_rows=csv_rows,
            bq_rows=None,
            detail=str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        return LoadResult(
            table_id=table_id,
            action="error",
            csv_rows=csv_rows,
            detail=str(exc),
        )

    return LoadResult(
        table_id=table_id,
        action="loaded",
        csv_rows=csv_rows,
        bq_rows=bq_rows,
        detail="ok",
    )


def run(
    *,
    dry_run: bool = False,
    project_id: str | None = None,
    client=None,
    raw_dir: Path = RAW_DIR,
    tables: tuple[str, ...] = BRONZE_TABLES,
) -> int:
    project = project_id or load_project_id()
    print(f"project={project}")
    print(f"location={BQ_LOCATION}")
    print(f"dataset={BRONZE_DATASET}")
    print(f"dry_run={dry_run}")
    print(f"raw_dir={raw_dir}")

    bq_client = client if client is not None else build_client(project)
    results: list[LoadResult] = []
    failures = 0

    for table_id in tables:
        result = load_one_table(
            bq_client,
            project_id=project,
            table_id=table_id,
            raw_dir=raw_dir,
            dry_run=dry_run,
        )
        results.append(result)
        bq_part = "" if result.bq_rows is None else f" bq_rows={result.bq_rows}"
        print(
            f"  {result.action:12}  {BRONZE_DATASET}.{table_id}  "
            f"csv_rows={result.csv_rows}{bq_part}  {result.detail}"
        )
        if result.action in {"error", "mismatch"}:
            failures += 1

    loaded = sum(1 for r in results if r.action in {"loaded", "would_load"})
    print(
        f"summary: tables={len(results)} ok_or_planned={loaded} failures={failures}"
    )

    if dry_run:
        print("dry-run: headers validados + filas contadas; no se ejecutó load.")
        return 1 if failures else 0

    if failures:
        print(
            "LOAD FAILED: mismatch o error en una o más tablas.",
            file=sys.stderr,
        )
        return 1

    print("OK: bronze_kora cargado (load jobs WRITE_TRUNCATE); conteos OK.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Carga CSV data/raw → bronze_kora (load jobs, WRITE_TRUNCATE)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida headers vs schema bronze y cuenta filas; no carga.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
