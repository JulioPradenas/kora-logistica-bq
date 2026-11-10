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
  - Paso 7: gold DDL es el contrato; tablas ya creadas (Paso 4) reciben
    ALTER TABLE … ADD COLUMN IF NOT EXISTS para columnas nuevas (p. ej.
    on_time/in_full). Así el dry-run de vistas ve el schema correcto
    antes del CTAS 07 (CREATE IF NOT EXISTS no altera tablas existentes).
  - Paso 8: si el RANGE_BUCKET / GENERATE_ARRAY del DDL difiere del de la
    tabla BQ existente → DROP + CREATE (recreate idempotente). Seguro
    porque make run regenera CSV → bronze load → silver/gold CTAS.
  - Cargas posteriores: CREATE OR REPLACE TABLE AS SELECT / load jobs (Pasos 5+).
  - dry-run usa QueryJobConfig(dry_run=True) para validar SQL sin crear.

Partición (facts silver/gold): integer-range en fecha_id YYYYMMDD, intervalo 100
(≈ mensual). End exclusivo 20261201 → 108 buckets (cubre Nov deliveries;
20261101 dejaba fecha_id≥20261101 en __UNPARTITIONED__). Ver docs/paso-08-pruning.md.
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

# Horizonte lab: WMS 2025-04-01→2026-10-31 + deliveries Nov (lag 0–4d).
# GENERATE_ARRAY end es exclusivo; interval 100 ≈ mes en encoding YYYYMMDD.
# 20261201 (no 20261101): Nov 2026-11-01..04 debe caer en rango, no __UNPARTITIONED__.
FECHA_ID_START = 20250401
FECHA_ID_END_EXCLUSIVE = 20261201
FECHA_ID_INTERVAL = 100  # mensual; daily (1) → >10k partitions (cuota BQ)
EXPECTED_PARTITION_BUCKETS = 108

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

GOLD_DIM_TABLES: tuple[str, ...] = (
    "d_fecha",
    "d_bodega",
    "d_sku",
    "d_transportista",
)

GOLD_TABLES: tuple[str, ...] = GOLD_DIM_TABLES + GOLD_FACT_TABLES


@dataclass(frozen=True)
class StatementSpec:
    sql: str
    table_fqn: str  # dataset.table
    layer: str  # bronze | silver | gold


@dataclass(frozen=True)
class ApplyResult:
    table_fqn: str
    action: str  # created | exists | recreated | would_create | would_skip | would_recreate | error | …
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


def fecha_id_generate_array_sql(
    start: int = FECHA_ID_START,
    end_exclusive: int = FECHA_ID_END_EXCLUSIVE,
    interval: int = FECHA_ID_INTERVAL,
) -> str:
    """Snippet canónico GENERATE_ARRAY(...) para facts particionados."""
    return f"GENERATE_ARRAY({start}, {end_exclusive}, {interval})"


_GENERATE_ARRAY_RE = re.compile(
    r"GENERATE_ARRAY\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)",
    re.IGNORECASE,
)


def expected_range_from_sql(sql: str) -> tuple[int, int, int] | None:
    """
    Extrae (start, end_exclusive, interval) del PARTITION BY RANGE_BUCKET
    en un CREATE. None si la tabla no declara integer-range.
    """
    if not re.search(r"PARTITION\s+BY\s+RANGE_BUCKET", sql, re.IGNORECASE):
        return None
    m = _GENERATE_ARRAY_RE.search(sql)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def actual_range_from_table(table) -> tuple[int, int, int] | None:
    """
    Lee range_partitioning de un Table BQ → (start, end, interval).
    None si la tabla no está integer-range partitioned.
    """
    rp = getattr(table, "range_partitioning", None)
    if rp is None:
        return None
    rng = getattr(rp, "range_", None)
    if rng is None:
        rng = getattr(rp, "range", None)
    if rng is None:
        return None
    try:
        return int(rng.start), int(rng.end), int(rng.interval)
    except (TypeError, AttributeError, ValueError):
        return None


def partition_spec_mismatch(table, sql: str) -> bool:
    """
    True si el DDL exige integer-range y el de la tabla BQ no coincide
    (o la tabla no está particionada). Tablas sin PARTITION en DDL → False.
    """
    expected = expected_range_from_sql(sql)
    if expected is None:
        return False
    actual = actual_range_from_table(table)
    if actual is None:
        return True
    return actual != expected


def table_exists(client, project_id: str, table_fqn: str) -> bool:
    from google.api_core.exceptions import NotFound

    full = f"{project_id}.{table_fqn}"
    try:
        client.get_table(full)
        return True
    except NotFound:
        return False


def get_table(client, project_id: str, table_fqn: str):
    """Devuelve Table o None si no existe."""
    from google.api_core.exceptions import NotFound

    full = f"{project_id}.{table_fqn}"
    try:
        return client.get_table(full)
    except NotFound:
        return None


def drop_table_sql(project_id: str, table_fqn: str) -> str:
    """DDL DROP TABLE IF EXISTS (recreate ante cambio de partition-spec)."""
    return f"DROP TABLE IF EXISTS `{project_id}.{table_fqn}`"


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
    """
    Aplica CREATE TABLE IF NOT EXISTS.
    Paso 8: si existe y el partition-spec (RANGE_BUCKET/GENERATE_ARRAY) difiere
    del DDL → DROP + CREATE (recreate). Seguro: make run regenera datos
    (CSV → bronze load → silver/gold CTAS).
    """
    table = get_table(client, project_id, spec.table_fqn)
    if table is not None:
        if partition_spec_mismatch(table, spec.sql):
            expected = expected_range_from_sql(spec.sql)
            actual = actual_range_from_table(table)
            detail = (
                f"partition-spec change: bq={actual} ddl={expected}; "
                "DROP+CREATE (data regenerates on make run)"
            )
            if dry_run:
                return ApplyResult(
                    table_fqn=spec.table_fqn,
                    action="would_recreate",
                    detail=detail,
                )
            try:
                run_ddl_job(
                    client,
                    drop_table_sql(project_id, spec.table_fqn),
                    dry_run=False,
                )
                run_ddl_job(client, spec.sql, dry_run=False)
            except Exception as exc:  # noqa: BLE001
                return ApplyResult(
                    table_fqn=spec.table_fqn,
                    action="error",
                    detail=f"recreate failed: {exc}",
                )
            return ApplyResult(
                table_fqn=spec.table_fqn,
                action="recreated",
                detail=detail,
            )
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


def _extract_dataset_columns_from_sql(
    sql_text: str, *, dataset: str
) -> dict[str, list[tuple[str, str]]]:
    """Parser genérico dataset.table → [(col, type), ...] (mismo subset de tipos)."""
    # Reusa el extractor bronze reescribiendo el dataset en el FQN.
    rewritten = re.sub(
        rf"`{re.escape(dataset)}\.",
        "`bronze_kora.",
        sql_text,
        flags=re.IGNORECASE,
    )
    return extract_bronze_columns_from_sql(rewritten)


def extract_gold_columns_from_sql(
    sql_text: str | None = None,
) -> dict[str, list[tuple[str, str]]]:
    """
    Extrae (columna, tipo) por tabla gold desde ddl_gold.sql.
    Contrato Paso 7: estas columnas deben existir en BQ antes del dry-run de vistas.
    """
    text = sql_text
    if text is None:
        text = (SQL_DIR / "ddl_gold.sql").read_text(encoding="utf-8")
    return _extract_dataset_columns_from_sql(text, dataset="gold_kora")


def gold_add_column_sql(
    project_id: str, table_id: str, column: str, col_type: str
) -> str:
    """
    DDL: añade columna faltante en gold existente (sandbox OK).
    Tipo NULLABLE en ALTER: suficiente para que el dry-run de vistas resuelva
    el nombre; el CTAS CREATE OR REPLACE aplica el modo REQUIRED final del DDL.
    """
    return (
        f"ALTER TABLE `{project_id}.gold_kora.{table_id}` "
        f"ADD COLUMN IF NOT EXISTS `{column}` {col_type}"
    )


def sync_gold_schema(
    client,
    *,
    project_id: str,
    dry_run: bool = False,
    ddl_text: str | None = None,
    tables: tuple[str, ...] | None = None,
) -> list[ApplyResult]:
    """
    Idempotente: para cada tabla gold existente, ADD COLUMN IF NOT EXISTS
    de columnas declaradas en ddl_gold.sql que aún no están en el schema BQ.

    Motivo (Paso 7 / Julio make run): CREATE TABLE IF NOT EXISTS no muta tablas
    Paso 4; el dry-run de v_otif_diario / v_cycle_time valida contra el schema
    *existente* (sin on_time/in_full), no contra el CTAS 07 que corre después.
    """
    from google.api_core.exceptions import NotFound

    text = ddl_text
    if text is None:
        text = (SQL_DIR / "ddl_gold.sql").read_text(encoding="utf-8")
    expected = _extract_dataset_columns_from_sql(text, dataset="gold_kora")
    target_tables = tables if tables is not None else tuple(expected.keys())
    results: list[ApplyResult] = []

    for table_id in target_tables:
        table_fqn = f"gold_kora.{table_id}"
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

        existing = {getattr(f, "name", "") for f in (table.schema or [])}
        want = expected.get(table_id, [])
        missing = [(name, typ) for name, typ in want if name not in existing]
        if not missing:
            results.append(
                ApplyResult(
                    table_fqn=table_fqn,
                    action="would_skip" if dry_run else "schema_ok",
                    detail="gold columns match ddl_gold.sql",
                )
            )
            continue

        altered: list[str] = []
        failed: list[str] = []
        for col, typ in missing:
            sql = gold_add_column_sql(project_id, table_id, col, typ)
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
                    detail=f"ADD COLUMN failed: {'; '.join(failed)}",
                )
            )
        else:
            results.append(
                ApplyResult(
                    table_fqn=table_fqn,
                    action="would_add_columns" if dry_run else "added_columns",
                    detail=f"ADD COLUMN IF NOT EXISTS → {', '.join(altered)}",
                )
            )
    return results


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


# Facts particionados (silver + gold) sujetos a check __UNPARTITIONED__/__NULL__.
PARTITIONED_FACT_TABLES: tuple[tuple[str, str], ...] = (
    ("silver_kora", "sap_wms_movimientos"),
    ("silver_kora", "sap_wms_stock_snapshot"),
    ("silver_kora", "sap_tm_entregas"),
    ("gold_kora", "f_movimientos_bodega"),
    ("gold_kora", "f_stock_diario"),
    ("gold_kora", "f_entregas"),
)


def unpartitioned_rows_sql(
    project_id: str,
    tables: tuple[tuple[str, str], ...] | None = None,
) -> str:
    """
    INFORMATION_SCHEMA.PARTITIONS: filas en __UNPARTITIONED__ / __NULL__.
    Debe devolver 0 filas con total_rows > 0 tras make run (Paso 8).
    """
    targets = tables if tables is not None else PARTITIONED_FACT_TABLES
    # Un UNION ALL por dataset para evitar escanear todo el proyecto.
    by_dataset: dict[str, list[str]] = {}
    for dataset, table in targets:
        by_dataset.setdefault(dataset, []).append(table)
    parts: list[str] = []
    for dataset, table_names in by_dataset.items():
        in_list = ", ".join(f"'{t}'" for t in table_names)
        parts.append(
            f"""
SELECT
  table_schema,
  table_name,
  partition_id,
  total_rows
FROM `{project_id}.{dataset}.INFORMATION_SCHEMA.PARTITIONS`
WHERE table_name IN ({in_list})
  AND partition_id IN ('__UNPARTITIONED__', '__NULL__')
  AND total_rows > 0
""".strip()
        )
    return "\nUNION ALL\n".join(parts)


def check_no_unpartitioned_rows(
    client,
    *,
    project_id: str,
    dry_run: bool = False,
) -> tuple[int, list[dict]]:
    """
    Post-run: falla si alguna fact particionada tiene filas en
    __UNPARTITIONED__ o __NULL__. Retorna (n_bad_partitions, rows).
    dry_run: solo valida el SQL (QueryJobConfig dry_run); retorna (0, []).
    """
    from google.cloud import bigquery

    sql = unpartitioned_rows_sql(project_id)
    job_config = bigquery.QueryJobConfig(dry_run=dry_run, use_legacy_sql=False)
    job = client.query(sql, job_config=job_config, location=BQ_LOCATION)
    if dry_run:
        _ = job.total_bytes_processed
        return 0, []
    rows = [dict(r) for r in job.result()]
    return len(rows), rows


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

    # Paso 7: alinear gold existente al contrato ddl_gold (ADD COLUMN IF NOT EXISTS).
    print("==> sync gold schema (ALTER ADD COLUMN IF NOT EXISTS)")
    sync_results = sync_gold_schema(
        bq_client, project_id=project, dry_run=dry_run
    )
    for result in sync_results:
        results.append(result)
        print(f"  {result.action:12}  {result.table_fqn}  {result.detail}")
        if result.action == "error":
            errors += 1

    created = sum(1 for r in results if r.action in {"created", "would_create"})
    recreated = sum(
        1 for r in results if r.action in {"recreated", "would_recreate"}
    )
    skipped = sum(
        1
        for r in results
        if r.action
        in {"exists", "would_skip", "skipped", "nullable_ok", "schema_ok"}
    )
    relaxed = sum(1 for r in results if r.action in {"relaxed", "would_relax"})
    added = sum(
        1 for r in results if r.action in {"added_columns", "would_add_columns"}
    )
    print(
        f"summary: tables={len(statements)} created_or_planned={created} "
        f"recreated={recreated} skipped={skipped} relaxed={relaxed} "
        f"gold_columns_added={added} errors={errors}"
    )

    if dry_run:
        print(
            "dry-run: validación BigQuery job dry_run; "
            "no se crearon/alteraron/recrearon tablas."
        )
        return 1 if errors else 0

    if errors:
        print("APPLY FAILED: uno o más DDL fallaron.", file=sys.stderr)
        return 1
    print(
        "OK: DDL bronze/silver/gold aplicado; bronze NULLABLE "
        "(ALTER DROP NOT NULL); gold schema synced "
        "(ALTER ADD COLUMN IF NOT EXISTS); "
        "partition-spec recreate si cambió RANGE_BUCKET "
        "(seguro: make run regenera datos)."
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
