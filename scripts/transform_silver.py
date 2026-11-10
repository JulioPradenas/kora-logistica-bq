#!/usr/bin/env python3
"""
KORA Deportes — Paso 6: transform bronze_kora → silver_kora + quarantine.

Uso (Mac / laptop de Julio, con ADC ya configurado):
  python scripts/transform_silver.py --dry-run
  python scripts/transform_silver.py

Auth: Application Default Credentials (ADC). Sin SA keys en repo.

Sandbox:
  - CREATE OR REPLACE TABLE … AS SELECT (query job) — permitido.
  - Sin DML (no INSERT/UPDATE/DELETE/MERGE).
  - CTAS debe re-declarar PARTITION BY / CLUSTER BY (si no, se pierden).

Orden: maestros → hechos → quarantine.
Reconciliación por entidad:
  bronze = silver + quarantine + eliminated_dupes
  (exit 1 si mismatch). Status casing se NORMALIZA a silver (no quarantine).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql" / "silver"

BQ_LOCATION = "southamerica-west1"
BRONZE_DATASET = "bronze_kora"
SILVER_DATASET = "silver_kora"
QUARANTINE_TABLE = "quarantine_rows"

# Orden estricto: masters antes de facts; quarantine al final (FK vs silver).
TRANSFORM_FILES: tuple[str, ...] = (
    "01_md_bodega.sql",
    "02_md_sku.sql",
    "03_md_transportista.sql",
    "04_sap_wms_movimientos.sql",
    "05_sap_wms_stock_snapshot.sql",
    "06_sap_tm_entregas.sql",
    "07_quarantine_rows.sql",
)

# Entidades con ecuación de reconciliación (masters + facts).
RECONCILE_ENTITIES: tuple[str, ...] = (
    "md_bodega",
    "md_sku",
    "md_transportista",
    "sap_wms_movimientos",
    "sap_wms_stock_snapshot",
    "sap_tm_entregas",
)

# Natural keys / dedup partitions (para contar eliminated_dupes en BQ).
DEDUP_KEYS: dict[str, tuple[str, ...]] = {
    "md_bodega": ("warehouse_id",),
    "md_sku": ("sku_id",),
    "md_transportista": ("carrier_id",),
    "sap_wms_movimientos": ("movement_id",),
    "sap_wms_stock_snapshot": ("snapshot_date", "warehouse_id", "sku_id"),
    "sap_tm_entregas": ("shipment_id",),
}

# Conteos esperados seed 42 + anomalías documentadas (Paso 6).
# clean 60120/208440/29803 + anomaly extras (status_casing no suma filas).
EXPECTED_BRONZE_COUNTS: dict[str, int] = {
    "md_bodega": 8,
    "md_sku": 250,
    "md_transportista": 12,
    "sap_wms_movimientos": 60_285,
    "sap_wms_stock_snapshot": 208_555,
    "sap_tm_entregas": 29_923,
}

# Tras transform (dedup + quarantine FK/null): silver ≈ núcleo limpio.
EXPECTED_SILVER_COUNTS: dict[str, int] = {
    "md_bodega": 8,
    "md_sku": 250,
    "md_transportista": 12,
    "sap_wms_movimientos": 60_120,
    "sap_wms_stock_snapshot": 208_440,
    "sap_tm_entregas": 29_803,
}

# Quarantine por reason (seed 42) — documentado en docs/paso-06-silver.md.
# null splits: mov 20→10/10; stock 10→5/5; ent 15→8 date / 7 key (i%2).
EXPECTED_QUARANTINE_BY_REASON: dict[str, int] = {
    "missing_fk_warehouse": 45,  # 30 mov + 15 entregas
    "missing_fk_sku": 50,  # 25 mov + 25 stock
    "missing_fk_carrier": 20,
    "null_required_date": 23,  # 10 mov + 5 stock + 8 entregas
    "null_required_key": 22,  # 10 mov + 5 stock + 7 entregas
}

EXPECTED_ELIMINATED_DUPES: dict[str, int] = {
    "md_bodega": 0,
    "md_sku": 0,
    "md_transportista": 0,
    "sap_wms_movimientos": 90,  # 50 exact + 40 key
    "sap_wms_stock_snapshot": 80,  # 50 exact + 30 key
    "sap_tm_entregas": 70,  # 40 exact + 30 key
}

# Map canónico de status (testable sin BQ).
STATUS_CANONICAL_MAP: dict[str, str] = {
    "delivered": "delivered",
    "late_delivered": "late_delivered",
    "latedelivered": "late_delivered",
    "failed": "failed",
    "in_transit": "in_transit",
    "intransit": "in_transit",
}


@dataclass(frozen=True)
class TransformResult:
    file_name: str
    action: str  # applied | would_apply | error
    detail: str = ""


@dataclass(frozen=True)
class ReconcileRow:
    entity: str
    bronze: int
    silver: int
    quarantine: int
    eliminated_dupes: int

    @property
    def ok(self) -> bool:
        return self.bronze == self.silver + self.quarantine + self.eliminated_dupes


def load_project_id(env_file: Path | None = None) -> str:
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
    from google.cloud import bigquery

    return bigquery.Client(project=project_id, location=BQ_LOCATION)


def list_transform_files(sql_dir: Path | None = None) -> list[Path]:
    base = sql_dir if sql_dir is not None else SQL_DIR
    files: list[Path] = []
    for name in TRANSFORM_FILES:
        path = base / name
        if not path.is_file():
            raise FileNotFoundError(f"Falta SQL silver: {path}")
        files.append(path)
    return files


def read_sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def strip_sql_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if "--" in line:
            line = line[: line.index("--")]
        lines.append(line)
    return "\n".join(lines).strip().rstrip(";").strip()


def render_sql(sql_text: str, *, project_id: str | None = None) -> str:
    """
    Normaliza SQL CTAS. Opcionalmente prefixa project si el template usa {{project}}.
    """
    sql = strip_sql_comments(sql_text)
    if project_id and "{{project}}" in sql:
        sql = sql.replace("{{project}}", project_id)
    return sql


def canonical_status(raw: str | None) -> str | None:
    """Normaliza status delivery (UPPER/TRIM + map). None → invalid."""
    if raw is None:
        return None
    key = raw.strip().lower().replace(" ", "")
    if not key:
        return None
    return STATUS_CANONICAL_MAP.get(key)


def reconciliation_equation_holds(
    *, bronze: int, silver: int, quarantine: int, eliminated_dupes: int
) -> bool:
    """bronze = silver + quarantine + eliminated_dupes"""
    return bronze == silver + quarantine + eliminated_dupes


def eliminated_dupes_sql(entity: str) -> str:
    """Cuenta filas rn>1 (perdedores de dedup) en bronze."""
    keys = DEDUP_KEYS[entity]
    # TRIM on string-ish keys; snapshot_date kept as cast string for partition.
    parts = []
    for k in keys:
        if k.endswith("_date") or k.endswith("_id") or k in {"movement_id", "shipment_id"}:
            parts.append(f"TRIM(CAST({k} AS STRING))")
        else:
            parts.append(f"TRIM(CAST({k} AS STRING))")
    partition = ", ".join(parts)
    return f"""
SELECT COUNT(*) AS n FROM (
  SELECT 1
  FROM `{BRONZE_DATASET}.{entity}`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY {partition} ORDER BY 1) > 1
)
""".strip()


def quarantine_count_sql(entity: str) -> str:
    return f"""
SELECT COUNT(*) AS n
FROM `{SILVER_DATASET}.{QUARANTINE_TABLE}`
WHERE source_table = '{entity}'
""".strip()


def quarantine_by_reason_sql() -> str:
    return f"""
SELECT reason, COUNT(*) AS n
FROM `{SILVER_DATASET}.{QUARANTINE_TABLE}`
GROUP BY reason
ORDER BY reason
""".strip()


def table_count_sql(dataset: str, table: str) -> str:
    return f"SELECT COUNT(*) AS n FROM `{dataset}.{table}`"


def run_query(client, sql: str, *, dry_run: bool = False):
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(dry_run=dry_run, use_legacy_sql=False)
    job = client.query(sql, job_config=job_config, location=BQ_LOCATION)
    if dry_run:
        _ = job.total_bytes_processed
        return None
    return job.result()


def scalar_count(client, sql: str) -> int:
    rows = list(run_query(client, sql, dry_run=False))
    return int(rows[0]["n"])


def apply_transform_file(
    client,
    path: Path,
    *,
    dry_run: bool = False,
) -> TransformResult:
    try:
        sql = render_sql(read_sql(path))
        run_query(client, sql, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        return TransformResult(file_name=path.name, action="error", detail=str(exc))
    return TransformResult(
        file_name=path.name,
        action="would_apply" if dry_run else "applied",
        detail="ok",
    )


def reconcile_entity(client, entity: str) -> ReconcileRow:
    bronze = scalar_count(client, table_count_sql(BRONZE_DATASET, entity))
    silver = scalar_count(client, table_count_sql(SILVER_DATASET, entity))
    # Masters no escriben quarantine rows en 07_*; quarantine=0.
    if entity.startswith("md_"):
        quarantine = 0
    else:
        quarantine = scalar_count(client, quarantine_count_sql(entity))
    eliminated = scalar_count(client, eliminated_dupes_sql(entity))
    return ReconcileRow(
        entity=entity,
        bronze=bronze,
        silver=silver,
        quarantine=quarantine,
        eliminated_dupes=eliminated,
    )


def print_quarantine_summary(client) -> dict[str, int]:
    rows = list(run_query(client, quarantine_by_reason_sql(), dry_run=False))
    by_reason = {str(r["reason"]): int(r["n"]) for r in rows}
    print("quarantine by reason:")
    if not by_reason:
        print("  (empty)")
    for reason, n in sorted(by_reason.items()):
        print(f"  {reason}: {n}")
    return by_reason


def assert_ctas_redeclarations(sql_text: str, table: str) -> list[str]:
    """Verifica que CTAS de facts re-declaren PARTITION/CLUSTER (testable)."""
    errors: list[str] = []
    upper = sql_text.upper()
    if "CREATE OR REPLACE TABLE" not in upper:
        errors.append(f"{table}: missing CREATE OR REPLACE TABLE")
    if table in {
        "sap_wms_movimientos",
        "sap_wms_stock_snapshot",
        "sap_tm_entregas",
    }:
        if "PARTITION BY" not in upper or "RANGE_BUCKET" not in upper:
            errors.append(f"{table}: CTAS must re-declare PARTITION BY RANGE_BUCKET")
        if "CLUSTER BY" not in upper:
            errors.append(f"{table}: CTAS must re-declare CLUSTER BY")
    return errors


def quality_rules_sql_snippets() -> dict[str, str]:
    """Fragmentos de reglas de calidad expuestos a tests (sin GCP)."""
    return {
        "dedup_row_number": "ROW_NUMBER() OVER (",
        "status_canonical": "CASE LOWER(REPLACE(TRIM(CAST(status AS STRING)), ' ', ''))",
        "movement_type_canonical": "CASE UPPER(TRIM(CAST(movement_type AS STRING)))",
        "fk_join_bodega": "INNER JOIN `silver_kora.md_bodega`",
        "fk_join_sku": "INNER JOIN `silver_kora.md_sku`",
        "fk_join_carrier": "INNER JOIN `silver_kora.md_transportista`",
        "usable_qty": "AS usable_qty",
        "reconcile_equation": "bronze = silver + quarantine + eliminated_dupes",
    }


def run(
    *,
    dry_run: bool = False,
    project_id: str | None = None,
    client=None,
    sql_dir: Path | None = None,
    skip_reconcile: bool = False,
) -> int:
    project = project_id or load_project_id()
    print(f"project={project}")
    print(f"location={BQ_LOCATION}")
    print(f"dry_run={dry_run}")
    print(f"sql_dir={sql_dir or SQL_DIR}")
    print(
        "reconcile_equation: bronze = silver + quarantine + eliminated_dupes"
    )

    bq_client = client if client is not None else build_client(project)
    results: list[TransformResult] = []
    failures = 0

    for path in list_transform_files(sql_dir):
        result = apply_transform_file(bq_client, path, dry_run=dry_run)
        results.append(result)
        print(f"  {result.action:12}  {result.file_name}  {result.detail}")
        if result.action == "error":
            failures += 1

    if dry_run:
        print("dry-run: QueryJobConfig(dry_run=True); no CTAS writes.")
        return 1 if failures else 0

    if failures:
        print("TRANSFORM FAILED: uno o más CTAS fallaron.", file=sys.stderr)
        return 1

    if skip_reconcile:
        print("OK: silver CTAS aplicados (reconcile skipped).")
        return 0

    print("==> reconcile (bronze = silver + quarantine + eliminated_dupes)")
    reconcile_rows: list[ReconcileRow] = []
    for entity in RECONCILE_ENTITIES:
        row = reconcile_entity(bq_client, entity)
        reconcile_rows.append(row)
        flag = "OK" if row.ok else "MISMATCH"
        print(
            f"  {flag:8}  {entity}: bronze={row.bronze} "
            f"silver={row.silver} quarantine={row.quarantine} "
            f"elim_dupes={row.eliminated_dupes} "
            f"sum={row.silver + row.quarantine + row.eliminated_dupes}"
        )
        if not row.ok:
            failures += 1

    print_quarantine_summary(bq_client)

    if failures:
        print(
            "RECONCILE FAILED: bronze != silver + quarantine + eliminated_dupes",
            file=sys.stderr,
        )
        return 1

    print("OK: silver_kora transformado; reconciliación OK.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform bronze→silver + quarantine (CTAS, sandbox)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida cada CTAS con QueryJobConfig(dry_run=True); no escribe.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
