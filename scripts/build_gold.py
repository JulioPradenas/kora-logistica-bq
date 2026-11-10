#!/usr/bin/env python3
"""
KORA Deportes — Paso 7: build gold datamart dm_logistica_retail (star en gold_kora).

Uso (Mac / laptop de Julio, con ADC ya configurado):
  python scripts/build_gold.py --dry-run
  python scripts/build_gold.py

Auth: Application Default Credentials (ADC). Sin SA keys en repo.

Sandbox:
  - CREATE OR REPLACE TABLE / VIEW … AS SELECT (query job) — permitido.
  - Sin DML (no INSERT/UPDATE/DELETE/MERGE).
  - CTAS de facts re-declara PARTITION BY / CLUSTER BY (si no, se pierden).

Orden: dims → facts → views.
Post-run: reconciliación gold↔silver, orphan FKs = 0, rangePartitioning.
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
SQL_DIR = ROOT / "sql" / "gold"
DDL_GOLD = ROOT / "sql" / "ddl_gold.sql"

BQ_LOCATION = "southamerica-west1"
SILVER_DATASET = "silver_kora"
GOLD_DATASET = "gold_kora"

# Orden: dims → facts → views.
BUILD_FILES: tuple[str, ...] = (
    "01_d_fecha.sql",
    "02_d_bodega.sql",
    "03_d_sku.sql",
    "04_d_transportista.sql",
    "05_f_movimientos_bodega.sql",
    "06_f_stock_diario.sql",
    "07_f_entregas.sql",
    "08_v_otif_diario.sql",
    "09_v_cycle_time.sql",
    "10_v_stock_salud.sql",
)

FACT_TABLES: tuple[str, ...] = (
    "f_movimientos_bodega",
    "f_stock_diario",
    "f_entregas",
)

DIM_TABLES: tuple[str, ...] = (
    "d_fecha",
    "d_bodega",
    "d_sku",
    "d_transportista",
)

VIEW_TABLES: tuple[str, ...] = (
    "v_otif_diario",
    "v_cycle_time",
    "v_stock_salud",
)

# Conteos esperados seed 42 (silver limpio → gold). Documentados en docs/paso-07-gold.md.
EXPECTED_GOLD_COUNTS: dict[str, int] = {
    "d_fecha": 579,
    "d_bodega": 8,
    "d_sku": 250,
    "d_transportista": 12,
    "f_movimientos_bodega": 55_548,
    "f_stock_diario": 208_440,
    "f_entregas": 29_803,
}

PARTITION_SNIPPET = (
    "PARTITION BY RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261101, 100))"
)

# Cluster esperados (Paso 4).
EXPECTED_CLUSTER: dict[str, tuple[str, ...]] = {
    "f_movimientos_bodega": ("bodega_id", "sku_id"),
    "f_stock_diario": ("bodega_id", "sku_id"),
    "f_entregas": ("bodega_id", "transportista_id"),
}


@dataclass(frozen=True)
class BuildResult:
    file_name: str
    action: str  # applied | would_apply | error
    detail: str = ""


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


def list_build_files(sql_dir: Path | None = None) -> list[Path]:
    base = sql_dir if sql_dir is not None else SQL_DIR
    files: list[Path] = []
    for name in BUILD_FILES:
        path = base / name
        if not path.is_file():
            raise FileNotFoundError(f"Falta SQL gold: {path}")
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


def render_sql(sql_text: str) -> str:
    return strip_sql_comments(sql_text)


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


def table_count_sql(dataset: str, table: str) -> str:
    return f"SELECT COUNT(*) AS n FROM `{dataset}.{table}`"


def apply_build_file(client, path: Path, *, dry_run: bool = False) -> BuildResult:
    try:
        sql = render_sql(read_sql(path))
        run_query(client, sql, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        return BuildResult(file_name=path.name, action="error", detail=str(exc))
    return BuildResult(
        file_name=path.name,
        action="would_apply" if dry_run else "applied",
        detail="ok",
    )


def assert_ctas_redeclarations(sql_text: str, table: str) -> list[str]:
    """Verifica que CTAS de facts re-declaren PARTITION/CLUSTER (testable)."""
    errors: list[str] = []
    upper = sql_text.upper()
    if table.startswith("f_"):
        if "CREATE OR REPLACE TABLE" not in upper:
            errors.append(f"{table}: missing CREATE OR REPLACE TABLE")
        if "PARTITION BY" not in upper or "RANGE_BUCKET" not in upper:
            errors.append(f"{table}: CTAS must re-declare PARTITION BY RANGE_BUCKET")
        if "CLUSTER BY" not in upper:
            errors.append(f"{table}: CTAS must re-declare CLUSTER BY")
        if PARTITION_SNIPPET.upper() not in upper.replace("\n", " "):
            # Tolerate whitespace: check GENERATE_ARRAY bounds.
            if "GENERATE_ARRAY(20250401, 20261101, 100)" not in sql_text.replace(" ", ""):
                if "GENERATE_ARRAY(20250401, 20261101, 100)" not in sql_text:
                    errors.append(f"{table}: PARTITION GENERATE_ARRAY bounds mismatch")
    elif table.startswith("v_"):
        if "CREATE OR REPLACE VIEW" not in upper:
            errors.append(f"{table}: missing CREATE OR REPLACE VIEW")
    else:
        if "CREATE OR REPLACE TABLE" not in upper:
            errors.append(f"{table}: missing CREATE OR REPLACE TABLE")
    if "INSERT " in upper or "MERGE " in upper or "DELETE " in upper:
        errors.append(f"{table}: DML not allowed in sandbox gold SQL")
    return errors


def parse_ddl_required_columns(ddl_text: str) -> dict[str, list[str]]:
    """
    Extrae columnas NOT NULL por tabla desde ddl_gold.sql.
    Usado para asegurar que CTAS no emite nulls en REQUIRED (lección Paso 6).
    """
    result: dict[str, list[str]] = {}
    # Split por CREATE TABLE blocks.
    blocks = re.split(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS", ddl_text, flags=re.IGNORECASE)
    for block in blocks[1:]:
        m_name = re.search(r"`?gold_kora\.([a-z0-9_]+)`?", block, flags=re.IGNORECASE)
        if not m_name:
            continue
        table = m_name.group(1)
        # Schema hasta PARTITION/CLUSTER/OPTIONS/closing before those.
        schema_m = re.search(r"\((.*?)\)\s*(?:PARTITION|CLUSTER|OPTIONS|;)", block, flags=re.I | re.S)
        if not schema_m:
            continue
        schema = schema_m.group(1)
        cols: list[str] = []
        for line in schema.splitlines():
            line = line.split("--")[0].strip().rstrip(",")
            if not line:
                continue
            # col TYPE [NOT NULL]
            cm = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s+\w+", line)
            if not cm:
                continue
            if re.search(r"\bNOT\s+NULL\b", line, flags=re.IGNORECASE):
                cols.append(cm.group(1))
        result[table] = cols
    return result


def ctas_select_aliases(sql_text: str) -> list[str]:
    """Aliases aproximados del SELECT final (AS name / trailing bare)."""
    body = strip_sql_comments(sql_text)
    # Ancla en el AS del CREATE OR REPLACE … AS (no en "col AS alias").
    m_as = re.search(
        r"CREATE\s+OR\s+REPLACE\s+(?:TABLE|VIEW)\b.*?(\bAS\b)",
        body,
        flags=re.I | re.S,
    )
    select_part = body[m_as.end(1) :] if m_as else body
    # WITH … SELECT final: tomar el último SELECT.
    if re.search(r"\bWITH\b", select_part, flags=re.I):
        parts = list(re.finditer(r"\bSELECT\b", select_part, flags=re.I))
        if not parts:
            return []
        select_part = select_part[parts[-1].start() :]
    sm_sel = re.search(r"\bSELECT\b", select_part, flags=re.I)
    if not sm_sel:
        return []
    i = sm_sel.end()
    depth = 0
    from_idx = None
    while i < len(select_part):
        ch = select_part[i]
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0 and select_part[i : i + 4].upper() == "FROM":
            before_ok = i == 0 or not (select_part[i - 1].isalnum() or select_part[i - 1] == "_")
            after_ok = i + 4 >= len(select_part) or not (
                select_part[i + 4].isalnum() or select_part[i + 4] == "_"
            )
            if before_ok and after_ok:
                from_idx = i
                break
        i += 1
    if from_idx is None:
        return []
    select_list = select_part[sm_sel.end() : from_idx]
    aliases: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in select_list:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0:
            item = "".join(current).strip()
            if item:
                aliases.append(_alias_from_select_item(item))
            current = []
        else:
            current.append(ch)
    item = "".join(current).strip()
    if item:
        aliases.append(_alias_from_select_item(item))
    return [a for a in aliases if a]


def _alias_from_select_item(item: str) -> str:
    m = re.search(r"\bAS\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*$", item, flags=re.I)
    if m:
        return m.group(1)
    # bare column ref: take last identifier
    m2 = re.search(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*$", item)
    return m2.group(1) if m2 else ""


def assert_ctas_covers_required(ddl_text: str, sql_dir: Path | None = None) -> list[str]:
    """
    Cada columna NOT NULL del DDL gold debe aparecer en el SELECT del CTAS
    correspondiente (evita nulls implícitos / schema drift vs REQUIRED).
    """
    base = sql_dir if sql_dir is not None else SQL_DIR
    required = parse_ddl_required_columns(ddl_text)
    errors: list[str] = []
    file_by_table = {
        "d_fecha": "01_d_fecha.sql",
        "d_bodega": "02_d_bodega.sql",
        "d_sku": "03_d_sku.sql",
        "d_transportista": "04_d_transportista.sql",
        "f_movimientos_bodega": "05_f_movimientos_bodega.sql",
        "f_stock_diario": "06_f_stock_diario.sql",
        "f_entregas": "07_f_entregas.sql",
    }
    for table, cols in required.items():
        fname = file_by_table.get(table)
        if not fname:
            continue
        path = base / fname
        if not path.is_file():
            errors.append(f"{table}: missing CTAS file {fname}")
            continue
        aliases = {a.lower() for a in ctas_select_aliases(read_sql(path))}
        for col in cols:
            if col.lower() not in aliases:
                errors.append(f"{table}: REQUIRED column `{col}` missing from CTAS SELECT")
        # Flags BOOL NOT NULL must use IF(…, TRUE, FALSE) — never bare NULL comparison.
        text = read_sql(path)
        if table == "f_entregas":
            if "AS on_time" not in text.replace("as on_time", "AS on_time"):
                if not re.search(r"\bAS\s+on_time\b", text, flags=re.I):
                    errors.append("f_entregas: on_time alias missing")
            if not re.search(r"\bAS\s+in_full\b", text, flags=re.I):
                errors.append("f_entregas: in_full alias missing")
            # Must coerce to BOOL non-null via IF(…, TRUE, FALSE)
            if not re.search(r"IF\s*\(.*\bTRUE\b.*\bFALSE\b", text, flags=re.I | re.S):
                errors.append(
                    "f_entregas: on_time/in_full must use IF(..., TRUE, FALSE) "
                    "so REQUIRED BOOL never gets NULL"
                )
    return errors


def reconcile_movimientos_sql() -> str:
    return f"""
SELECT
  (SELECT SUM(qty) FROM `{GOLD_DATASET}.f_movimientos_bodega`) AS gold_qty,
  (SELECT SUM(n_movimientos) FROM `{GOLD_DATASET}.f_movimientos_bodega`) AS gold_n,
  (SELECT SUM(qty) FROM `{SILVER_DATASET}.sap_wms_movimientos`) AS silver_qty,
  (SELECT COUNT(*) FROM `{SILVER_DATASET}.sap_wms_movimientos`) AS silver_n
""".strip()


def orphan_fk_checks_sql() -> dict[str, str]:
    return {
        "mov_bodega": f"""
SELECT COUNT(*) AS n FROM `{GOLD_DATASET}.f_movimientos_bodega` f
LEFT JOIN `{GOLD_DATASET}.d_bodega` d ON f.bodega_id = d.bodega_id
WHERE d.bodega_id IS NULL
""".strip(),
        "mov_sku": f"""
SELECT COUNT(*) AS n FROM `{GOLD_DATASET}.f_movimientos_bodega` f
LEFT JOIN `{GOLD_DATASET}.d_sku` d ON f.sku_id = d.sku_id
WHERE d.sku_id IS NULL
""".strip(),
        "mov_fecha": f"""
SELECT COUNT(*) AS n FROM `{GOLD_DATASET}.f_movimientos_bodega` f
LEFT JOIN `{GOLD_DATASET}.d_fecha` d ON f.fecha_id = d.fecha_id
WHERE d.fecha_id IS NULL
""".strip(),
        "stock_bodega": f"""
SELECT COUNT(*) AS n FROM `{GOLD_DATASET}.f_stock_diario` f
LEFT JOIN `{GOLD_DATASET}.d_bodega` d ON f.bodega_id = d.bodega_id
WHERE d.bodega_id IS NULL
""".strip(),
        "stock_sku": f"""
SELECT COUNT(*) AS n FROM `{GOLD_DATASET}.f_stock_diario` f
LEFT JOIN `{GOLD_DATASET}.d_sku` d ON f.sku_id = d.sku_id
WHERE d.sku_id IS NULL
""".strip(),
        "stock_fecha": f"""
SELECT COUNT(*) AS n FROM `{GOLD_DATASET}.f_stock_diario` f
LEFT JOIN `{GOLD_DATASET}.d_fecha` d ON f.fecha_id = d.fecha_id
WHERE d.fecha_id IS NULL
""".strip(),
        "ent_bodega": f"""
SELECT COUNT(*) AS n FROM `{GOLD_DATASET}.f_entregas` f
LEFT JOIN `{GOLD_DATASET}.d_bodega` d ON f.bodega_id = d.bodega_id
WHERE d.bodega_id IS NULL
""".strip(),
        "ent_transportista": f"""
SELECT COUNT(*) AS n FROM `{GOLD_DATASET}.f_entregas` f
LEFT JOIN `{GOLD_DATASET}.d_transportista` d ON f.transportista_id = d.transportista_id
WHERE d.transportista_id IS NULL
""".strip(),
        "ent_fecha": f"""
SELECT COUNT(*) AS n FROM `{GOLD_DATASET}.f_entregas` f
LEFT JOIN `{GOLD_DATASET}.d_fecha` d ON f.fecha_id = d.fecha_id
WHERE d.fecha_id IS NULL
""".strip(),
    }


def confirm_range_partitioning(client, table: str) -> bool:
    """True si la tabla tiene integer-range partitioning en fecha_id."""
    table_ref = client.get_table(f"{GOLD_DATASET}.{table}")
    rp = getattr(table_ref, "range_partitioning", None)
    if rp is None:
        return False
    field = getattr(rp, "field", None) or getattr(rp, "range_", None)
    # google.cloud.bigquery RangePartitioning: .field + .range_
    field_name = getattr(rp, "field", None)
    return str(field_name) == "fecha_id"


def post_run_checks(client) -> int:
    """Reconcile + orphans + partitioning. Return failure count."""
    failures = 0

    print("==> reconcile gold ↔ silver")
    rows = list(run_query(client, reconcile_movimientos_sql(), dry_run=False))
    r = rows[0]
    gold_qty, gold_n = int(r["gold_qty"]), int(r["gold_n"])
    silver_qty, silver_n = int(r["silver_qty"]), int(r["silver_n"])
    mov_ok = gold_qty == silver_qty and gold_n == silver_n
    print(
        f"  {'OK' if mov_ok else 'MISMATCH':8}  f_movimientos_bodega: "
        f"gold_qty={gold_qty} silver_qty={silver_qty} "
        f"gold_n={gold_n} silver_n={silver_n}"
    )
    if not mov_ok:
        failures += 1

    gold_stock = scalar_count(client, table_count_sql(GOLD_DATASET, "f_stock_diario"))
    silver_stock = scalar_count(
        client, table_count_sql(SILVER_DATASET, "sap_wms_stock_snapshot")
    )
    stock_ok = gold_stock == silver_stock
    print(
        f"  {'OK' if stock_ok else 'MISMATCH':8}  f_stock_diario: "
        f"gold={gold_stock} silver={silver_stock}"
    )
    if not stock_ok:
        failures += 1

    gold_ent = scalar_count(client, table_count_sql(GOLD_DATASET, "f_entregas"))
    silver_ent = scalar_count(client, table_count_sql(SILVER_DATASET, "sap_tm_entregas"))
    ent_ok = gold_ent == silver_ent
    print(
        f"  {'OK' if ent_ok else 'MISMATCH':8}  f_entregas: "
        f"gold={gold_ent} silver={silver_ent}"
    )
    if not ent_ok:
        failures += 1

    print("==> orphan FK checks (expect 0)")
    for name, sql in orphan_fk_checks_sql().items():
        n = scalar_count(client, sql)
        ok = n == 0
        print(f"  {'OK' if ok else 'ORPHAN':8}  {name}: {n}")
        if not ok:
            failures += 1

    print("==> rangePartitioning (fecha_id integer-range)")
    for table in FACT_TABLES:
        try:
            ok = confirm_range_partitioning(client, table)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR     {table}: {exc}")
            failures += 1
            continue
        print(f"  {'OK' if ok else 'MISSING':8}  {table}.range_partitioning.field=fecha_id")
        if not ok:
            failures += 1

    print("==> gold row counts (seed 42 expected)")
    for table, expected in EXPECTED_GOLD_COUNTS.items():
        n = scalar_count(client, table_count_sql(GOLD_DATASET, table))
        ok = n == expected
        print(f"  {'OK' if ok else 'MISMATCH':8}  {table}: {n} (expected {expected})")
        if not ok:
            failures += 1

    return failures


def run(
    *,
    dry_run: bool = False,
    project_id: str | None = None,
    client=None,
    sql_dir: Path | None = None,
    skip_checks: bool = False,
) -> int:
    project = project_id or load_project_id()
    print(f"project={project}")
    print(f"location={BQ_LOCATION}")
    print(f"dry_run={dry_run}")
    print(f"sql_dir={sql_dir or SQL_DIR}")
    print("datamart=dm_logistica_retail (conceptual) → dataset gold_kora")

    bq_client = client if client is not None else build_client(project)
    results: list[BuildResult] = []
    failures = 0

    for path in list_build_files(sql_dir):
        result = apply_build_file(bq_client, path, dry_run=dry_run)
        results.append(result)
        print(f"  {result.action:12}  {result.file_name}  {result.detail}")
        if result.action == "error":
            failures += 1

    if dry_run:
        print("dry-run: QueryJobConfig(dry_run=True); no CTAS/VIEW writes.")
        return 1 if failures else 0

    if failures:
        print("BUILD GOLD FAILED: uno o más CTAS/VIEW fallaron.", file=sys.stderr)
        return 1

    if skip_checks:
        print("OK: gold CTAS aplicados (checks skipped).")
        return 0

    check_failures = post_run_checks(bq_client)
    if check_failures:
        print("POST-RUN CHECKS FAILED.", file=sys.stderr)
        return 1

    print("OK: gold_kora datamart construido; reconcile/orphans/partition OK.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build gold star dm_logistica_retail (CTAS, sandbox)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida cada CTAS/VIEW con QueryJobConfig(dry_run=True); no escribe.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
