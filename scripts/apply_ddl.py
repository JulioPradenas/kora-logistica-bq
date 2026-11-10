#!/usr/bin/env python3
"""
KORA Deportes — Paso 4: aplicar DDL vacío (bronze / silver / gold) en BigQuery.

Uso (Mac / laptop de Julio, con ADC ya configurado):
  python scripts/apply_ddl.py --dry-run
  python scripts/apply_ddl.py
  python scripts/apply_ddl.py          # re-run: IF NOT EXISTS → action=exists

Auth: Application Default Credentials (ADC). Nunca hardcodea credenciales
ni espera un JSON de service account en el repo.

Proyecto: .env / entorno — GCP_PROJECT o GOOGLE_CLOUD_PROJECT (default lab: kora-sports).

Sandbox:
  - Sin DML → CREATE TABLE IF NOT EXISTS (DDL en sql/ddl_*.sql) + ALTER DDL.
  - Paso 6: bronze es landing NULLABLE; tablas ya creadas con NOT NULL se
    relajan con ALTER TABLE … ALTER COLUMN … DROP NOT NULL (idempotente).
  - Cargas posteriores: CREATE OR REPLACE TABLE AS SELECT / load jobs (Pasos 5+).
  - dry-run usa QueryJobConfig(dry_run=True) para validar SQL sin crear.

Partición (facts silver/gold): integer-range en fecha_id YYYYMMDD, intervalo 100
(≈ mensual). Justificación y citas oficiales: docs/paso-04-ddl.md.
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
SQL_DIR = ROOT / "sql"

# Misma región del lab (datasets ya creados en Paso 3).
BQ_LOCATION = "southamerica-west1"

# Horizonte lab (data contract): 2025-04-01 → 2026-10-31.
# GENERATE_ARRAY end es exclusivo; interval 100 ≈ mes en encoding YYYYMMDD.
FECHA_ID_START = 20250401
FECHA_ID_END_EXCLUSIVE = 20261101
FECHA_ID_INTERVAL = 100  # mensual; daily (1) → >10k partitions (cuota BQ)

DDL_FILES: tuple[str, ...] = (
    "ddl_bronze.sql",
    "ddl_silver.sql",
    "ddl_gold.sql",
)

# Contrato bronze: columnas/tipos alineados a data/README.md (tests sin GCP).
BRONZE_CONTRACT: dict[str, list[tuple[str, str]]] = {
    "md_bodega": [
        ("warehouse_id", "STRING"),
        ("warehouse_name", "STRING"),
        ("region_code", "STRING"),
        ("city", "STRING"),
        ("is_active", "BOOL"),
    ],
    "md_sku": [
        ("sku_id", "STRING"),
        ("sku_name", "STRING"),
        ("category", "STRING"),
        ("brand", "STRING"),
        ("unit_cost_clp", "INT64"),
    ],
    "md_transportista": [
        ("carrier_id", "STRING"),
        ("carrier_name", "STRING"),
        ("service_level", "STRING"),
    ],
    "sap_wms_movimientos": [
        ("movement_id", "STRING"),
        ("event_date", "DATE"),
        ("warehouse_id", "STRING"),
        ("sku_id", "STRING"),
        ("movement_code", "STRING"),
        ("movement_type", "STRING"),
        ("qty", "INT64"),
        ("qty_ordered", "INT64"),
        ("order_id", "STRING"),
        ("shipment_id", "STRING"),
        ("pick_start_ts", "DATETIME"),
        ("pick_end_ts", "DATETIME"),
    ],
    "sap_wms_stock_snapshot": [
        ("snapshot_date", "DATE"),
        ("warehouse_id", "STRING"),
        ("sku_id", "STRING"),
        ("on_hand_qty", "INT64"),
        ("reserved_qty", "INT64"),
    ],
    "sap_tm_entregas": [
        ("shipment_id", "STRING"),
        ("order_id", "STRING"),
        ("warehouse_id", "STRING"),
        ("carrier_id", "STRING"),
        ("comuna_id", "STRING"),
        ("comuna_name", "STRING"),
        ("ship_date", "DATE"),
        ("delivery_date", "DATE"),
        ("eta_ts", "DATETIME"),
        ("ata_ts", "DATETIME"),
        ("status", "STRING"),
        ("qty_delivered", "INT64"),
    ],
}

GOLD_FACT_TABLES: tuple[str, ...] = (
    "f_movimientos_bodega",
    "f_stock_diario",
    "f_entregas",
)


@dataclass(frozen=True)
class StatementSpec:
    sql: str
    table_fqn: str  # dataset.table
    layer: str  # bronze | silver | gold


@dataclass(frozen=True)
class ApplyResult:
    table_fqn: str
    action: str  # created | exists | would_create | would_skip | error
    detail: str = ""


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
    """BigQuery client con ADC (sin path de key hardcodeado)."""
    from google.cloud import bigquery

    return bigquery.Client(project=project_id, location=BQ_LOCATION)


def _strip_sql_comments(text: str) -> str:
    """Elimina comentarios -- línea (suficiente para nuestros DDL)."""
    lines: list[str] = []
    for line in text.splitlines():
        if "--" in line:
            # No hay strings con -- en estos DDL; corte simple.
            line = line[: line.index("--")]
        lines.append(line)
    return "\n".join(lines)


def split_sql_statements(sql_text: str) -> list[str]:
    """Parte un archivo DDL en statements terminados en ';' (respeta strings)."""
    cleaned = _strip_sql_comments(sql_text)
    parts: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(cleaned):
        ch = cleaned[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            buf.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            buf.append(ch)
        elif ch == ";" and not in_single and not in_double:
            stmt = "".join(buf).strip()
            if stmt:
                parts.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


_CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+`(?P<fqn>[^`]+)`",
    re.IGNORECASE,
)


def parse_ddl_file(path: Path) -> list[StatementSpec]:
    """Lee un sql/ddl_*.sql y devuelve StatementSpec por CREATE TABLE."""
    layer = path.stem.replace("ddl_", "")  # bronze | silver | gold
    text = path.read_text(encoding="utf-8")
    specs: list[StatementSpec] = []
    for stmt in split_sql_statements(text):
        m = _CREATE_RE.search(stmt)
        if not m:
            continue
        fqn = m.group("fqn")
        # Normaliza a dataset.table (sin project).
        bits = fqn.split(".")
        if len(bits) == 3:
            fqn = f"{bits[1]}.{bits[2]}"
        elif len(bits) != 2:
            raise ValueError(f"FQN inesperado en {path.name}: {fqn}")
        specs.append(StatementSpec(sql=stmt, table_fqn=fqn, layer=layer))
    return specs


def load_all_statements(sql_dir: Path | None = None) -> list[StatementSpec]:
    base = sql_dir if sql_dir is not None else SQL_DIR
    out: list[StatementSpec] = []
    for name in DDL_FILES:
        path = base / name
        if not path.is_file():
            raise FileNotFoundError(f"Falta DDL: {path}")
        out.extend(parse_ddl_file(path))
    return out


def extract_bronze_columns_from_sql(sql_text: str) -> dict[str, list[tuple[str, str]]]:
    """
    Extrae (columna, tipo) por tabla bronze desde DDL.
    Soporta el subset de tipos del contrato (STRING, BOOL, INT64, DATE, DATETIME).
    """
    result: dict[str, list[tuple[str, str]]] = {}
    for stmt in split_sql_statements(sql_text):
        m = _CREATE_RE.search(stmt)
        if not m:
            continue
        fqn = m.group("fqn")
        table = fqn.split(".")[-1]
        # Cuerpo entre primer '(' del CREATE y el ')' que cierra el schema
        # (antes de PARTITION/CLUSTER/OPTIONS a nivel tabla).
        start = stmt.find("(")
        if start < 0:
            continue
        depth = 0
        end = None
        for i, ch in enumerate(stmt[start:], start=start):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            continue
        body = stmt[start + 1 : end]
        cols: list[tuple[str, str]] = []
        for raw_line in body.split("\n"):
            line = raw_line.strip().rstrip(",")
            if not line:
                continue
            # col TYPE [NOT NULL] [OPTIONS(...)]
            cm = re.match(
                r"^([a-zA-Z_][a-zA-Z0-9_]*)\s+(STRING|BOOL|INT64|DATE|DATETIME)\b",
                line,
            )
            if cm:
                cols.append((cm.group(1), cm.group(2)))
        if cols:
            result[table] = cols
    return result


def gold_fact_partition_cluster(sql_text: str) -> dict[str, dict[str, object]]:
    """
    Para cada fact gold: detecta RANGE_BUCKET + GENERATE_ARRAY + CLUSTER BY.
    """
    info: dict[str, dict[str, object]] = {}
    for stmt in split_sql_statements(sql_text):
        m = _CREATE_RE.search(stmt)
        if not m:
            continue
        table = m.group("fqn").split(".")[-1]
        if table not in GOLD_FACT_TABLES:
            continue
        has_range = bool(
            re.search(
                r"PARTITION\s+BY\s+RANGE_BUCKET\s*\(\s*fecha_id\s*,",
                stmt,
                re.IGNORECASE,
            )
        )
        gen = re.search(
            r"GENERATE_ARRAY\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)",
            stmt,
            re.IGNORECASE,
        )
        cluster = re.search(
            r"CLUSTER\s+BY\s+([a-zA-Z0-9_,\s]+?)(?:\s+OPTIONS|\s*$)",
            stmt,
            re.IGNORECASE | re.DOTALL,
        )
        cluster_cols = []
        if cluster:
            cluster_cols = [c.strip() for c in cluster.group(1).split(",") if c.strip()]
        info[table] = {
            "has_integer_range": has_range,
            "generate_array": (
                (int(gen.group(1)), int(gen.group(2)), int(gen.group(3))) if gen else None
            ),
            "cluster_by": cluster_cols,
        }
    return info


def partition_count(start: int, end_exclusive: int, interval: int) -> int:
    """Número de buckets RANGE_BUCKET / GENERATE_ARRAY (sin __UNPARTITIONED__)."""
    if interval <= 0:
        raise ValueError("interval must be > 0")
    if end_exclusive <= start:
        raise ValueError("end must be > start")
    # GENERATE_ARRAY produce floor((end-start-1)/interval)+1 boundaries;
    # nº de rangos = len(array)-1? Actually RANGE_BUCKET with GENERATE_ARRAY(start, end, step)
    # creates partitions for each range [array[i], array[i+1]).
    # Number of partitions = number of steps = ceil((end-start)/interval) when end aligns,
    # else floor((end-start)/interval). Official: ranges of width interval from start until end.
    span = end_exclusive - start
    return (span + interval - 1) // interval if span % interval else span // interval


def table_exists(client, project_id: str, table_fqn: str) -> bool:
    from google.api_core.exceptions import NotFound

    full = f"{project_id}.{table_fqn}"
    try:
        client.get_table(full)
        return True
    except NotFound:
        return False


def run_ddl_job(client, sql: str, *, dry_run: bool) -> None:
    """Ejecuta (o valida con dry_run) un statement DDL."""
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(dry_run=dry_run, use_legacy_sql=False)
    job = client.query(sql, job_config=job_config, location=BQ_LOCATION)
    if dry_run:
        # dry_run completa en submit; no result()
        _ = job.total_bytes_processed
        return
    job.result()


def apply_statement(
    client,
    *,
    project_id: str,
    spec: StatementSpec,
    dry_run: bool = False,
) -> ApplyResult:
    """Aplica un CREATE TABLE IF NOT EXISTS con reporte por tabla."""
    exists = table_exists(client, project_id, spec.table_fqn)
    if exists:
        return ApplyResult(
            table_fqn=spec.table_fqn,
            action="would_skip" if dry_run else "exists",
            detail="already present",
        )

    try:
        run_ddl_job(client, spec.sql, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 — reportar y continuar/fallar en run()
        return ApplyResult(
            table_fqn=spec.table_fqn,
            action="error",
            detail=str(exc),
        )

    return ApplyResult(
        table_fqn=spec.table_fqn,
        action="would_create" if dry_run else "created",
        detail="ok",
    )


def bronze_drop_not_null_sql(project_id: str, table_id: str, column: str) -> str:
    """DDL: relaja REQUIRED → NULLABLE en una columna bronze (sandbox OK)."""
    return (
        f"ALTER TABLE `{project_id}.bronze_kora.{table_id}` "
        f"ALTER COLUMN `{column}` DROP NOT NULL"
    )


def required_field_names(schema) -> list[str]:
    """Nombres de campos con mode REQUIRED (API BigQuery)."""
    out: list[str] = []
    try:
        fields = list(schema) if schema is not None else []
    except TypeError:
        return out
    for field in fields:
        mode = getattr(field, "mode", None) or "NULLABLE"
        if str(mode).upper() == "REQUIRED":
            out.append(field.name)
    return out


def relax_bronze_nullability(
    client,
    *,
    project_id: str,
    dry_run: bool = False,
    tables: tuple[str, ...] | None = None,
) -> list[ApplyResult]:
    """
    Idempotente: para cada tabla bronze existente, DROP NOT NULL en columnas
    REQUIRED. Necesario porque CREATE TABLE IF NOT EXISTS no altera tablas
    ya creadas en el laptop de Julio (Paso 4 con NOT NULL).
    """
    from google.api_core.exceptions import NotFound

    target_tables = tables if tables is not None else tuple(BRONZE_CONTRACT.keys())
    results: list[ApplyResult] = []

    for table_id in target_tables:
        table_fqn = f"bronze_kora.{table_id}"
        full = f"{project_id}.{table_fqn}"
        try:
            table = client.get_table(full)
        except NotFound:
            results.append(
                ApplyResult(
                    table_fqn=table_fqn,
                    action="would_skip" if dry_run else "skipped",
                    detail="table missing — CREATE runs first",
                )
            )
            continue
        except Exception as exc:  # noqa: BLE001
            results.append(
                ApplyResult(table_fqn=table_fqn, action="error", detail=str(exc))
            )
            continue

        required = required_field_names(table.schema)
        if not required:
            results.append(
                ApplyResult(
                    table_fqn=table_fqn,
                    action="would_skip" if dry_run else "nullable_ok",
                    detail="all columns already NULLABLE",
                )
            )
            continue

        altered: list[str] = []
        failed: list[str] = []
        for col in required:
            sql = bronze_drop_not_null_sql(project_id, table_id, col)
            try:
                run_ddl_job(client, sql, dry_run=dry_run)
                altered.append(col)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{col}:{exc}")

        if failed:
            results.append(
                ApplyResult(
                    table_fqn=table_fqn,
                    action="error",
                    detail=f"DROP NOT NULL failed: {'; '.join(failed)}",
                )
            )
        else:
            results.append(
                ApplyResult(
                    table_fqn=table_fqn,
                    action="would_relax" if dry_run else "relaxed",
                    detail=f"DROP NOT NULL → {', '.join(altered)}",
                )
            )
    return results


def bronze_ddl_has_not_null(sql_text: str | None = None) -> bool:
    """True si ddl_bronze.sql declara NOT NULL (regresión landing permisivo)."""
    text = sql_text
    if text is None:
        text = (SQL_DIR / "ddl_bronze.sql").read_text(encoding="utf-8")
    # Ignora comentarios de línea.
    body_lines = []
    for line in text.splitlines():
        if "--" in line:
            line = line[: line.index("--")]
        body_lines.append(line)
    body = "\n".join(body_lines)
    return bool(re.search(r"\bNOT\s+NULL\b", body, flags=re.IGNORECASE))


def run(*, dry_run: bool = False, project_id: str | None = None, client=None) -> int:
    project = project_id or load_project_id()
    print(f"project={project}")
    print(f"location={BQ_LOCATION}")
    print(f"dry_run={dry_run}")
    print(
        f"partition_range=RANGE_BUCKET(fecha_id, GENERATE_ARRAY("
        f"{FECHA_ID_START}, {FECHA_ID_END_EXCLUSIVE}, {FECHA_ID_INTERVAL}))"
    )
    npart = partition_count(FECHA_ID_START, FECHA_ID_END_EXCLUSIVE, FECHA_ID_INTERVAL)
    print(f"partition_buckets={npart} (limit 10000)")

    bq_client = client if client is not None else build_client(project)
    statements = load_all_statements()
    results: list[ApplyResult] = []
    errors = 0

    for spec in statements:
        result = apply_statement(
            bq_client,
            project_id=project,
            spec=spec,
            dry_run=dry_run,
        )
        results.append(result)
        print(f"  {result.action:12}  {result.table_fqn}  {result.detail}")
        if result.action == "error":
            errors += 1

    # Paso 6: relajar NOT NULL en bronze existente (CREATE IF NOT EXISTS no basta).
    print("==> relax bronze nullability (ALTER COLUMN DROP NOT NULL)")
    relax_results = relax_bronze_nullability(
        bq_client, project_id=project, dry_run=dry_run
    )
    for result in relax_results:
        results.append(result)
        print(f"  {result.action:12}  {result.table_fqn}  {result.detail}")
        if result.action == "error":
            errors += 1

    created = sum(1 for r in results if r.action in {"created", "would_create"})
    skipped = sum(
        1
        for r in results
        if r.action in {"exists", "would_skip", "skipped", "nullable_ok"}
    )
    relaxed = sum(1 for r in results if r.action in {"relaxed", "would_relax"})
    print(
        f"summary: tables={len(statements)} created_or_planned={created} "
        f"skipped={skipped} relaxed={relaxed} errors={errors}"
    )

    if dry_run:
        print("dry-run: validación BigQuery job dry_run; no se crearon/alteraron tablas.")
        return 1 if errors else 0

    if errors:
        print("APPLY FAILED: uno o más DDL fallaron.", file=sys.stderr)
        return 1
    print(
        "OK: DDL bronze/silver/gold aplicado; bronze NULLABLE "
        "(CREATE IF NOT EXISTS + ALTER DROP NOT NULL)."
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aplica DDL KORA (bronze/silver/gold) en BigQuery (idempotente)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida cada CREATE con QueryJobConfig(dry_run=True) sin crear tablas.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
