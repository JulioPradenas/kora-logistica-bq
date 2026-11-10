# KORA Deportes — lab runners (Mac / laptop de Julio)
# Uso: make setup | make run | make run DRY=1 | make test
#
# Un entrypoint estable (`make run`) ejecuta TODAS las etapas en orden,
# cada una dry-run → apply (idempotente). Futuros pasos = una línea en STAGES.
#
# Auth: ADC en la laptop. Nunca imprime secretos ni paths de SA keys.
# CI: `make test` + `make -n run` (sin llamadas GCP reales).

PY      ?= .venv/bin/python
VENV    ?= .venv
PIP     := $(PY) -m pip
DRY     ?= 0

# Proyecto GCP desde .env (solo GCP_PROJECT); default lab.
# No hacemos `include .env` para no exportar GOOGLE_APPLICATION_CREDENTIALS u otros.
GCP_PROJECT ?= $(shell if [ -f .env ]; then sed -n 's/^[[:space:]]*GCP_PROJECT[[:space:]]*=[[:space:]]*//p' .env | head -n1 | tr -d '"' | tr -d "'"; fi)
ifeq ($(strip $(GCP_PROJECT)),)
  GCP_PROJECT := kora-sports
endif

# ---------------------------------------------------------------------------
# STAGES — ÚNICO lugar extensible (orden = pipeline).
# datasets (Paso 3) → DDL vacío (Paso 4) → CSV sintéticos → load bronze (Paso 5).
#
# generate_synthetic: SIEMPRE (no if-missing). Justificación:
#   - data/raw/*.csv está en .gitignore → no hay artefacto versionado.
#   - seed 42 + horizonte fijo → mismos bytes/conteos; regenerar es barato vs load.
#   - evita CSV stale tras cambios al generador/contrato.
# ---------------------------------------------------------------------------
STAGES := \
	scripts/setup_bigquery.py \
	scripts/apply_ddl.py \
	scripts/generate_synthetic.py \
	scripts/load_bronze.py
#	scripts/transform_silver.py \
#	scripts/build_gold.py

# Tablas bronze para verify de conteos (orden = load_bronze.BRONZE_TABLES).
BRONZE_TABLES := md_bodega md_sku md_transportista sap_wms_movimientos sap_wms_stock_snapshot sap_tm_entregas

.PHONY: help setup test run

help:
	@echo "Targets: setup | run | run DRY=1 | test"
	@echo "PY=$(PY)  GCP_PROJECT=$(GCP_PROJECT)  DRY=$(DRY)"
	@echo "STAGES=$(STAGES)"

# Idempotente: crea .venv si falta e instala deps (requirements-dev → requirements).
setup: $(VENV)/bin/python
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r requirements-dev.txt

$(VENV)/bin/python:
	python3 -m venv $(VENV)

test: setup
	$(PY) -m pytest -q

# Pipeline completo: por cada stage → dry-run → (si DRY!=1) apply; luego verify + summary.
# Para si cualquier dry-run/apply falla (set -e).
run: setup
	@set -e; \
	n=0; \
	for script in $(STAGES); do \
	  n=$$((n+1)); \
	  echo ""; \
	  echo "==> [$$n] $$script --dry-run"; \
	  $(PY) $$script --dry-run; \
	  if [ "$(DRY)" = "1" ]; then \
	    echo "    (DRY=1: skip apply)"; \
	  else \
	    echo "==> [$$n] $$script"; \
	    $(PY) $$script; \
	  fi; \
	done; \
	echo ""; \
	if [ "$(DRY)" = "1" ]; then \
	  echo "==> verify skipped (DRY=1)"; \
	  echo "summary: stages=$$n mode=dry-only project=$(GCP_PROJECT) OK"; \
	else \
	  echo "==> verify: bq ls datasets + bronze num_rows + schema gold fact"; \
	  bq ls --project_id=$(GCP_PROJECT); \
	  bq ls --project_id=$(GCP_PROJECT) bronze_kora; \
	  bq ls --project_id=$(GCP_PROJECT) silver_kora; \
	  bq ls --project_id=$(GCP_PROJECT) gold_kora; \
	  echo "==> bronze_kora row counts (expect 8/250/12/60120/208440/29803)"; \
	  for t in $(BRONZE_TABLES); do \
	    bq show --format=prettyjson $(GCP_PROJECT):bronze_kora.$$t \
	      | $(PY) -c "import json,sys; d=json.load(sys.stdin); print(f\"  bronze_kora.$$t  num_rows={d.get('numRows')}\")"; \
	  done; \
	  bq show --schema --format=prettyjson $(GCP_PROJECT):gold_kora.f_movimientos_bodega; \
	  echo ""; \
	  echo "summary: stages=$$n mode=apply+verify project=$(GCP_PROJECT) OK"; \
	fi
