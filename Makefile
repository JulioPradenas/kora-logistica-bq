# KORA Deportes — lab runners (Mac / laptop de Julio)
# Uso: make paso3 | make paso4 | make test | make setup
# Futuro: añadir make paso5, make paso6, … con el mismo patrón (setup → dry-run → apply → verify).
#
# Auth: ADC en la laptop. Nunca imprime secretos ni paths de SA keys.
# CI: solo `make test` / `make -n paso4` (sin llamadas GCP reales).

PY      ?= .venv/bin/python
VENV    ?= .venv
PIP     := $(PY) -m pip

# Proyecto GCP desde .env (solo GCP_PROJECT); default lab.
# No hacemos `include .env` para no exportar GOOGLE_APPLICATION_CREDENTIALS u otros.
GCP_PROJECT ?= $(shell if [ -f .env ]; then sed -n 's/^[[:space:]]*GCP_PROJECT[[:space:]]*=[[:space:]]*//p' .env | head -n1 | tr -d '"' | tr -d "'"; fi)
ifeq ($(strip $(GCP_PROJECT)),)
  GCP_PROJECT := kora-sports
endif

.PHONY: help setup test paso3 paso4

help:
	@echo "Targets: setup | test | paso3 | paso4"
	@echo "PY=$(PY)  GCP_PROJECT=$(GCP_PROJECT)"
	@echo "Patrón futuro: make pasoN  (dry-run && apply && verify; depende de setup)"

# Idempotente: crea .venv si falta e instala deps (requirements-dev → requirements).
setup: $(VENV)/bin/python
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r requirements-dev.txt

$(VENV)/bin/python:
	python3 -m venv $(VENV)

test: setup
	$(PY) -m pytest -q

# Paso 3 — datasets medallón (dry-run → apply → bq ls). Para si dry-run falla (&&).
paso3: setup
	$(PY) scripts/setup_bigquery.py --dry-run
	$(PY) scripts/setup_bigquery.py
	bq ls --project_id=$(GCP_PROJECT)

# Paso 4 — DDL vacío (dry-run → apply → list datasets + schema de un fact gold).
paso4: setup
	$(PY) scripts/apply_ddl.py --dry-run
	$(PY) scripts/apply_ddl.py
	bq ls --project_id=$(GCP_PROJECT) bronze_kora
	bq ls --project_id=$(GCP_PROJECT) silver_kora
	bq ls --project_id=$(GCP_PROJECT) gold_kora
	bq show --schema --format=prettyjson $(GCP_PROJECT):gold_kora.f_movimientos_bodega

# --- Plantilla futuros pasos (descomentar / copiar al implementar) ---
# paso5: setup
# 	$(PY) scripts/load_bronze.py --dry-run
# 	$(PY) scripts/load_bronze.py
# 	bq ls --project_id=$(GCP_PROJECT) bronze_kora
