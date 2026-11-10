#!/usr/bin/env python3
"""
KORA Deportes — Paso 8: demo partition / cluster pruning (bytes).

Uso (Mac / laptop de Julio, con ADC):
  python scripts/demo_partition_prune.py --dry-run
  python scripts/demo_partition_prune.py          # dry_run de las 3 + opcional real (c)
  make run / make run DRY=1                       # stage 7 en STAGES

Auth: ADC. Sin SA keys en repo. Sin inventar números: imprime totalBytesProcessed
del job dry_run (o placeholders si la tabla aún no existe).

Honestidad dry-run:
  - BigQuery dry_run estima bytes a escanear; tablas chicas / estimate por
    partición → el ahorro de CLUSTER BY suele NO aparecer en dry_run.
  - Facturación real tiene mínimo ~10 MB por query.
  - Opcional: --real-cluster ejecuta (c) como job real y reporta bytes reales.
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
SQL_FILE = ROOT / "sql" / "demo_partition_prune.sql"
APPLY_DDL_PATH = ROOT / "scripts" / "apply_ddl.py"
BQ_LOCATION = "southamerica-west1"

QUERY_LABELS: tuple[str, ...] = ("a", "b", "c")
QUERY_TITLES: dict[str, str] = {
    "a": "no filter (baseline)",
    "b": "one month fecha_id (partition prune)",
    "c": "month + bodega_id (partition + cluster)",
}


@dataclass(frozen=True)
class QuerySpec:
    label: str
    title: str
    sql: str


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


def _load_apply_ddl():
    name = "apply_ddl"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, APPLY_DDL_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def parse_demo_queries(sql_text: str | None = None) -> list[QuerySpec]:
    """
    Extrae las 3 queries marcadas QUERY_A/B/C desde demo_partition_prune.sql.
    """
    text = sql_text if sql_text is not None else SQL_FILE.read_text(encoding="utf-8")
    # Quita comentarios de línea pero conserva markers antes de cortar statements.
    markers = re.findall(
        r"--\s*===\s*QUERY_([ABC])\s*:(.*?)===\s*\n(.*?)(?=(?:--\s*===\s*QUERY_)|\Z)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if len(markers) != 3:
        # Fallback: split por ';' ignorando comentarios vacíos.
        body = re.sub(r"--.*?$", "", text, flags=re.MULTILINE)
        stmts = [s.strip() for s in body.split(";") if s.strip()]
        if len(stmts) != 3:
            raise ValueError(
                f"Expected 3 demo queries in {SQL_FILE.name}; found {len(stmts)}"
            )
        return [
            QuerySpec(label=QUERY_LABELS[i], title=QUERY_TITLES[QUERY_LABELS[i]], sql=stmts[i])
            for i in range(3)
        ]

    out: list[QuerySpec] = []
    for letter, title_hint, body in markers:
        label = letter.lower()
        sql = body.strip().rstrip(";").strip()
        # Quitar comentarios residuales dentro del bloque.
        sql_lines = []
        for line in sql.splitlines():
            if "--" in line:
                line = line[: line.index("--")]
            if line.strip():
                sql_lines.append(line)
        sql = "\n".join(sql_lines).strip().rstrip(";")
        title = QUERY_TITLES.get(label, title_hint.strip())
        out.append(QuerySpec(label=label, title=title, sql=sql))
    labels = [q.label for q in out]
    if labels != list(QUERY_LABELS):
        raise ValueError(f"Query labels out of order: {labels}")
    return out


def dry_run_bytes(client, sql: str) -> int:
    """QueryJobConfig(dry_run=True) → total_bytes_processed (estimado)."""
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(dry_run=True, use_legacy_sql=False)
    job = client.query(sql, job_config=job_config, location=BQ_LOCATION)
    return int(job.total_bytes_processed or 0)


def run_query_bytes(client, sql: str) -> int:
    """Job real → total_bytes_processed tras result()."""
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(dry_run=False, use_legacy_sql=False)
    job = client.query(sql, job_config=job_config, location=BQ_LOCATION)
    job.result()
    return int(job.total_bytes_processed or 0)


def pct_of_baseline(bytes_n: int, baseline: int) -> str:
    if baseline <= 0:
        return "n/a"
    return f"{(100.0 * bytes_n / baseline):.1f}%"


def format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.2f} KiB"
    if n < 1024**3:
        return f"{n / (1024**2):.2f} MiB"
    return f"{n / (1024**3):.2f} GiB"


def run(
    *,
    dry_run: bool = False,
    project_id: str | None = None,
    client=None,
    real_cluster: bool = False,
    skip_unpartitioned_check: bool = False,
) -> int:
    """
    dry_run=True (flag CLI / DRY=1): solo dry_run de las 3 queries + check SQL.
    dry_run=False: igual dry_run de las 3 (bytes demo) + check real UNPARTITIONED;
      si real_cluster, ejecuta (c) como job real.
    """
    project = project_id or load_project_id()
    print(f"project={project}")
    print(f"location={BQ_LOCATION}")
    print(f"dry_run={dry_run}")
    print(f"sql={SQL_FILE.relative_to(ROOT)}")
    print(
        "note: dry_run estimates; clustering savings often invisible "
        "(~10MB min bill / partition-level estimate). "
        "Optional real job: --real-cluster for query (c)."
    )

    queries = parse_demo_queries()
    bq_client = client if client is not None else build_client(project)

    results: dict[str, int] = {}
    errors = 0

    print("==> dry_run bytes on gold_kora.f_stock_diario")
    for q in queries:
        try:
            n = dry_run_bytes(bq_client, q.sql)
            results[q.label] = n
            print(f"  ({q.label}) {q.title}")
            print(f"      totalBytesProcessed={n} ({format_bytes(n)})")
        except Exception as exc:  # noqa: BLE001
            errors += 1
            results[q.label] = -1
            print(f"  ({q.label}) ERROR: {exc}")

    baseline = results.get("a", -1)
    if baseline >= 0:
        print("==> % vs (a) baseline")
        for label in QUERY_LABELS:
            n = results.get(label, -1)
            if n < 0:
                print(f"  ({label}) n/a")
            else:
                print(
                    f"  ({label}) {format_bytes(n)} = {pct_of_baseline(n, baseline)} of (a)"
                )
        print(
            "honesty: if (b)≈(a) or (c)≈(b) on dry_run, that can be estimate "
            "granularity — not proof that prune/cluster failed. "
            "Fill README table from a real make run; do not invent numbers."
        )

    if real_cluster and not dry_run:
        print("==> optional real job for (c) only")
        qc = next(q for q in queries if q.label == "c")
        try:
            n_real = run_query_bytes(bq_client, qc.sql)
            print(
                f"  (c) real totalBytesProcessed={n_real} ({format_bytes(n_real)})"
            )
            if baseline >= 0:
                print(f"  (c) real = {pct_of_baseline(n_real, baseline)} of dry_run (a)")
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"  (c) real ERROR: {exc}")

    if not skip_unpartitioned_check:
        print("==> INFORMATION_SCHEMA: 0 rows in __UNPARTITIONED__/__NULL__")
        apply_ddl = _load_apply_ddl()
        try:
            n_bad, bad_rows = apply_ddl.check_no_unpartitioned_rows(
                bq_client, project_id=project, dry_run=dry_run
            )
            if dry_run:
                print("  (DRY: SQL validated; skip row assert)")
            elif n_bad == 0:
                print("  OK        no rows in __UNPARTITIONED__/__NULL__")
            else:
                errors += n_bad
                for row in bad_rows:
                    print(
                        f"  FAIL      {row.get('table_schema')}.{row.get('table_name')} "
                        f"partition_id={row.get('partition_id')} "
                        f"total_rows={row.get('total_rows')}"
                    )
        except Exception as exc:  # noqa: BLE001
            # En DRY=1 early pipeline la tabla puede no existir aún tras solo dry-runs.
            if dry_run:
                print(f"  (DRY: unpartitioned check skipped/err ok: {exc})")
            else:
                errors += 1
                print(f"  ERROR     {exc}")

    if errors:
        print("DEMO PARTITION PRUNE FAILED.", file=sys.stderr)
        return 1
    print("OK: demo partition prune dry_runs complete.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Demo partition/cluster pruning bytes (Paso 8)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo dry_run de queries + validar SQL del check UNPARTITIONED.",
    )
    parser.add_argument(
        "--real-cluster",
        action="store_true",
        help="Ejecuta query (c) como job real (documentar bytes; opcional).",
    )
    parser.add_argument(
        "--skip-unpartitioned-check",
        action="store_true",
        help="Omite assert INFORMATION_SCHEMA (tests / debug).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run(
        dry_run=args.dry_run,
        real_cluster=args.real_cluster,
        skip_unpartitioned_check=args.skip_unpartitioned_check,
    )


if __name__ == "__main__":
    raise SystemExit(main())
