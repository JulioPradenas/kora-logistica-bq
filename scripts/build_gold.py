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

Orden: dims → facts → views (08–10 KPI + 11–13 Looker heatmap/funnel/drill).
Post-run: reconciliación gold↔silver, orphan FKs = 0, rangePartitioning,
          0 filas en __UNPARTITIONED__/__NULL__ (Paso 8),
          vistas existen + rows>0 + coherencia Looker (Paso 9).
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql" / "gold"
DDL_GOLD = ROOT / "sql" / "ddl_gold.sql"
APPLY_DDL_PATH = ROOT / "scripts" / "apply_ddl.py"


def _load_apply_ddl():
    """Carga apply_ddl para reutilizar check __UNPARTITIONED__ (Paso 8)."""
    name = "apply_ddl"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, APPLY_DDL_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

BQ_LOCATION = "southamerica-west1"
SILVER_DATASET = "silver_kora"
GOLD_DATASET = "gold_kora"

# Orden: dims → facts → views (Paso 7 KPIs + Paso 9 Looker heatmap/funnel/drill).
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
    "11_v_heatmap_bodega_semana.sql",
    "12_v_funnel_logistico.sql",
    "13_v_entregas_drill.sql",
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
    "v_heatmap_bodega_semana",
    "v_funnel_logistico",
    "v_entregas_drill",
)

# Vistas Looker Paso 9 (heatmap / funnel / drill) — post-check rows > 0 + coherencia.
LOOKER_VIEWS: tuple[str, ...] = (
    "v_heatmap_bodega_semana",
    "v_funnel_logistico",
    "v_entregas_drill",
)

# Calendario gold: WMS horizon end + max transit lag del generador (0–4 días).
# seed 42: delivery_date max = 2026-11-04 → 583 días (no 579).
D_FECHA_START = "2025-04-01"
D_FECHA_END = "2026-11-04"  # incluye Nov por lag de entrega; ver docs/paso-07-gold.md
WMS_HORIZON_END = "2026-10-31"
MAX_DELIVERY_TRANSIT_DAYS = 4

# Conteos esperados seed 42 (silver limpio → gold). Documentados en docs/paso-07-gold.md.
EXPECTED_GOLD_COUNTS: dict[str, int] = {
    "d_fecha": 583,
    "d_bodega": 8,
    "d_sku": 250,
    "d_transportista": 12,
    "f_movimientos_bodega": 55_548,
    "f_stock_diario": 208_440,
    "f_entregas": 29_803,
}

PARTITION_SNIPPET = (
    "PARTITION BY RANGE_BUCKET(fecha_id, GENERATE_ARRAY(20250401, 20261201, 100))"
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


def parse_d_fecha_bounds(sql_text: str) -> tuple[str, str]:
    """Extrae (start, end) ISO de GENERATE_DATE_ARRAY en 01_d_fecha.sql."""
    m = re.search(
        r"GENERATE_DATE_ARRAY\s*\(\s*DATE\s+'(\d{4}-\d{2}-\d{2})'\s*,\s*DATE\s+'(\d{4}-\d{2}-\d{2})'",
        sql_text,
        flags=re.IGNORECASE,
    )
    if not m:
        raise ValueError("GENERATE_DATE_ARRAY bounds not found in d_fecha SQL")
    return m.group(1), m.group(2)


def d_fecha_covers_fact_dates(
    *,
    d_fecha_start: str,
    d_fecha_end: str,
    wms_horizon_end: str = WMS_HORIZON_END,
    max_transit_days: int = MAX_DELIVERY_TRANSIT_DAYS,
) -> bool:
    """
    True si d_fecha cubre event/snapshot (≤ wms_horizon_end) y delivery_date
    hasta wms_horizon_end + max_transit_days (lag last-mile del generador).
    """
    from datetime import date, timedelta

    start = date.fromisoformat(d_fecha_start)
    end = date.fromisoformat(d_fecha_end)
    wms_end = date.fromisoformat(wms_horizon_end)
    need_end = wms_end + timedelta(days=max_transit_days)
    return start <= date.fromisoformat(D_FECHA_START) and end >= need_end


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
            if "GENERATE_ARRAY(20250401, 20261201, 100)" not in sql_text.replace(" ", ""):
                if "GENERATE_ARRAY(20250401, 20261201, 100)" not in sql_text:
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


def view_gold_column_refs(view_sql: str) -> dict[str, set[str]]:
    """
    Columnas referenciadas como alias.col sobre tablas gold_kora.* en una vista.
    Retorna {table_name: {col, ...}} (solo dataset gold_kora).
    """
    body = strip_sql_comments(view_sql)
    alias_to_table: dict[str, str] = {}
    # FROM/JOIN `gold_kora.table` [AS] alias
    for m in re.finditer(
        r"(?:FROM|JOIN)\s+`gold_kora\.([a-zA-Z0-9_]+)`(?:\s+(?:AS\s+)?([a-zA-Z_][a-zA-Z0-9_]*))?",
        body,
        flags=re.IGNORECASE,
    ):
        table = m.group(1)
        alias = (m.group(2) or table).lower()
        alias_to_table[alias] = table

    refs: dict[str, set[str]] = {}
    for m in re.finditer(
        r"\b([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\b", body
    ):
        alias, col = m.group(1).lower(), m.group(2)
        # Skip dataset.table FQNs already handled; gold_kora.x is dataset
        if alias in {"gold_kora", "silver_kora", "bronze_kora"}:
            continue
        table = alias_to_table.get(alias)
        if not table:
            continue
        refs.setdefault(table, set()).add(col)
    return refs


def assert_views_columns_in_gold_contract(
    ddl_text: str,
    sql_dir: Path | None = None,
    *,
    ctas_aliases: dict[str, set[str]] | None = None,
) -> list[str]:
    """
    Cada columna gold referenciada por vistas 08-10 debe existir en ddl_gold
    (contrato) o, si se pasa, en el SELECT del CTAS correspondiente.
    Regresión: dry-run de vistas falla si el schema BQ (heredado de DDL) no
    tiene on_time/in_full aunque el CTAS 07 las vaya a crear después.
    """
    base = sql_dir if sql_dir is not None else SQL_DIR
    # Parse DDL columns (all modes).
    ddl_cols: dict[str, set[str]] = {}
    for table, cols in parse_ddl_required_columns(ddl_text).items():
        ddl_cols.setdefault(table, set()).update(c.lower() for c in cols)
    # Also nullable columns from DDL (ata_ts, qty_ordered).
    for block in re.split(
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS", ddl_text, flags=re.IGNORECASE
    )[1:]:
        m_name = re.search(r"`?gold_kora\.([a-z0-9_]+)`?", block, flags=re.I)
        if not m_name:
            continue
        table = m_name.group(1)
        schema_m = re.search(
            r"\((.*?)\)\s*(?:PARTITION|CLUSTER|OPTIONS|;)", block, flags=re.I | re.S
        )
        if not schema_m:
            continue
        for line in schema_m.group(1).splitlines():
            line = line.split("--")[0].strip().rstrip(",")
            cm = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s+\w+", line)
            if cm:
                ddl_cols.setdefault(table, set()).add(cm.group(1).lower())

    errors: list[str] = []
    view_files = (
        "08_v_otif_diario.sql",
        "09_v_cycle_time.sql",
        "10_v_stock_salud.sql",
        "11_v_heatmap_bodega_semana.sql",
        "12_v_funnel_logistico.sql",
        "13_v_entregas_drill.sql",
    )
    for fname in view_files:
        path = base / fname
        if not path.is_file():
            errors.append(f"missing view file {fname}")
            continue
        refs = view_gold_column_refs(read_sql(path))
        for table, cols in refs.items():
            allowed = set(ddl_cols.get(table, set()))
            if ctas_aliases and table in ctas_aliases:
                allowed |= {c.lower() for c in ctas_aliases[table]}
            missing = sorted(c for c in cols if c.lower() not in allowed)
            for col in missing:
                errors.append(
                    f"{fname}: `{table}.{col}` referenced by view but missing "
                    f"from ddl_gold.sql (and CTAS SELECT if provided)"
                )
    return errors


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

    print("==> INFORMATION_SCHEMA: 0 rows in __UNPARTITIONED__/__NULL__")
    try:
        apply_ddl = _load_apply_ddl()
        project = getattr(client, "project", None) or load_project_id()
        n_bad, bad_rows = apply_ddl.check_no_unpartitioned_rows(
            client, project_id=project, dry_run=False
        )
        if n_bad == 0:
            print("  OK        no rows in __UNPARTITIONED__/__NULL__")
        else:
            for row in bad_rows:
                print(
                    f"  FAIL      {row.get('table_schema')}.{row.get('table_name')} "
                    f"partition_id={row.get('partition_id')} "
                    f"total_rows={row.get('total_rows')}"
                )
            failures += n_bad
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR     unpartitioned check: {exc}")
        failures += 1

    print("==> gold row counts (seed 42 expected)")
    for table, expected in EXPECTED_GOLD_COUNTS.items():
        n = scalar_count(client, table_count_sql(GOLD_DATASET, table))
        ok = n == expected
        print(f"  {'OK' if ok else 'MISMATCH':8}  {table}: {n} (expected {expected})")
        if not ok:
            failures += 1

    failures += post_run_view_checks(client)
    return failures


def view_exists(client, view: str) -> bool:
    """True si la vista/tabla gold existe (get_table)."""
    try:
        client.get_table(f"{GOLD_DATASET}.{view}")
        return True
    except Exception:  # noqa: BLE001
        return False


def post_run_view_checks(client) -> int:
    """
    Paso 9: vistas Looker existen, rows > 0, coherencia mínima.
    Idempotente: solo SELECT / get_table; no escribe.
    """
    failures = 0
    print("==> Looker views exist + rows > 0")
    for view in VIEW_TABLES:
        try:
            exists = view_exists(client, view)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR     {view}: {exc}")
            failures += 1
            continue
        if not exists:
            print(f"  MISSING   {view}")
            failures += 1
            continue
        n = scalar_count(client, table_count_sql(GOLD_DATASET, view))
        ok = n > 0
        print(f"  {'OK' if ok else 'EMPTY':8}  {view}: rows={n}")
        if not ok:
            failures += 1

    print("==> Looker views coherence (Paso 9)")
    # Heatmap: al menos 1 bodega y 1 semana.
    heat_sql = f"""
SELECT
  COUNT(DISTINCT bodega_id) AS n_bodegas,
  COUNT(DISTINCT semana_inicio) AS n_semanas,
  COUNTIF(late_rate IS NULL) AS n_null_late
FROM `{GOLD_DATASET}.v_heatmap_bodega_semana`
""".strip()
    try:
        h = list(run_query(client, heat_sql, dry_run=False))[0]
        n_bodegas, n_semanas = int(h["n_bodegas"]), int(h["n_semanas"])
        n_null_late = int(h["n_null_late"])
        heat_ok = n_bodegas > 0 and n_semanas > 0 and n_null_late == 0
        print(
            f"  {'OK' if heat_ok else 'FAIL':8}  heatmap: "
            f"bodegas={n_bodegas} semanas={n_semanas} null_late={n_null_late}"
        )
        if not heat_ok:
            failures += 1
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR     heatmap coherence: {exc}")
        failures += 1

    # Funnel shipment_e2e: exactamente 3 stages; picking/despacho/entrega presentes.
    funnel_sql = f"""
SELECT
  COUNT(*) AS n_rows,
  COUNTIF(stage_label = 'picking') AS n_picking,
  COUNTIF(stage_label = 'despacho') AS n_despacho,
  COUNTIF(stage_label = 'entrega') AS n_entrega,
  MAX(IF(stage_label = 'picking', n_shipments, NULL)) AS c_picking,
  MAX(IF(stage_label = 'despacho', n_shipments, NULL)) AS c_despacho,
  MAX(IF(stage_label = 'entrega', n_shipments, NULL)) AS c_entrega
FROM `{GOLD_DATASET}.v_funnel_logistico`
WHERE funnel_kind = 'shipment_e2e'
""".strip()
    try:
        f = list(run_query(client, funnel_sql, dry_run=False))[0]
        n_rows = int(f["n_rows"])
        stages_ok = (
            n_rows == 3
            and int(f["n_picking"]) == 1
            and int(f["n_despacho"]) == 1
            and int(f["n_entrega"]) == 1
        )
        c_pick = int(f["c_picking"] or 0)
        c_desp = int(f["c_despacho"] or 0)
        c_ent = int(f["c_entrega"] or 0)
        # Conversión E2E: entrega ≤ despacho; picking y despacho > 0.
        counts_ok = c_pick > 0 and c_desp > 0 and c_ent > 0 and c_ent <= c_desp
        funnel_ok = stages_ok and counts_ok
        print(
            f"  {'OK' if funnel_ok else 'FAIL':8}  funnel shipment_e2e: "
            f"stages={n_rows} pick={c_pick} despacho={c_desp} entrega={c_ent}"
        )
        if not funnel_ok:
            failures += 1
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR     funnel coherence: {exc}")
        failures += 1

    # Drill: comuna + transportista siempre; sku puede ser NULL si sin OUT bridge.
    drill_sql = f"""
SELECT
  COUNT(*) AS n,
  COUNTIF(comuna_id IS NULL OR comuna_id = '') AS n_sin_comuna,
  COUNTIF(transportista_id IS NULL OR transportista_id = '') AS n_sin_carrier,
  COUNTIF(sku_id IS NOT NULL) AS n_con_sku
FROM `{GOLD_DATASET}.v_entregas_drill`
""".strip()
    try:
        dr = list(run_query(client, drill_sql, dry_run=False))[0]
        n = int(dr["n"])
        n_sin_comuna = int(dr["n_sin_comuna"])
        n_sin_carrier = int(dr["n_sin_carrier"])
        n_con_sku = int(dr["n_con_sku"])
        drill_ok = n > 0 and n_sin_comuna == 0 and n_sin_carrier == 0 and n_con_sku > 0
        print(
            f"  {'OK' if drill_ok else 'FAIL':8}  drill: "
            f"rows={n} sin_comuna={n_sin_comuna} sin_carrier={n_sin_carrier} "
            f"con_sku={n_con_sku}"
        )
        if not drill_ok:
            failures += 1
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR     drill coherence: {exc}")
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
