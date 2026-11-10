# Looker Studio — KORA Deportes (Paso 9)

Guía exacta para conectar el dashboard MVP al datamart gold.  
**Proyecto BQ:** `kora-sports` · **Dataset:** `gold_kora` · **Región:** `southamerica-west1`

> Auth en la laptop de Julio (ADC). Este repo **no** guarda credenciales GCP ni publica el informe por el agente.

---

## Placeholders (rellenar tras publicar)

| Campo | Valor |
|---|---|
| **Viewer link (informe publicado)** | `PLACEHOLDER_LOOKER_VIEWER_URL` — pegar URL de Looker Studio cuando exista |
| **Screenshot overview** | `PLACEHOLDER_SCREENSHOT_OVERVIEW` — p. ej. `looker/screenshots/01-overview.png` |
| **Screenshot heatmap** | `PLACEHOLDER_SCREENSHOT_HEATMAP` — p. ej. `looker/screenshots/02-heatmap.png` |
| **Screenshot funnel** | `PLACEHOLDER_SCREENSHOT_FUNNEL` — p. ej. `looker/screenshots/03-funnel.png` |
| **Screenshot drill** | `PLACEHOLDER_SCREENSHOT_DRILL` — p. ej. `looker/screenshots/04-drill.png` |

No inventar links. Si aún no hay informe publicado, dejar los `PLACEHOLDER_*`.

---

## Prerrequisito datos

```bash
make run          # regenera CSV → bronze → silver → gold (vistas 08–13)
# o solo gold si silver ya está:
python scripts/build_gold.py --dry-run
python scripts/build_gold.py
```

Post-check Paso 9: vistas existen, `rows > 0`, coherencia heatmap/funnel/drill.

---

## 1) Crear data source BigQuery

1. Abrir [Looker Studio](https://lookerstudio.google.com/) → **Create** → **Data source**.
2. Conector **BigQuery**.
3. Proyecto: **`kora-sports`**.
4. Dataset: **`gold_kora`**.
5. Añadir **una data source por vista** (recomendado; evita joins caros en el canvas):

| Data source (nombre sugerido) | Tabla/vista BQ | Uso dashboard |
|---|---|---|
| `KORA · OTIF diario` | `v_otif_diario` | Overview OTIF % / late |
| `KORA · Cycle time` | `v_cycle_time` | Overview cycle E2E |
| `KORA · Stock salud` | `v_stock_salud` | Página secundaria stock |
| `KORA · Heatmap bodega×semana` | `v_heatmap_bodega_semana` | Heatmap |
| `KORA · Funnel logístico` | `v_funnel_logistico` | Funnel |
| `KORA · Entregas drill` | `v_entregas_drill` | Drill comuna/SKU/carrier |

6. Tipo de credencial: **Viewer's credentials** (demo) o Owner's si compartes fixed.
7. **Connect** → revisar campos (tipos DATE/BOOL/NUMERIC).

**Regla:** Looker **solo** lee `gold_kora`. Nunca `bronze_kora` / `silver_kora`.

---

## 2) Informe — páginas MVP (§10 levantamiento)

Crear informe en blanco → añadir las data sources de arriba.

### Página A — Overview (OTIF + cycle)

| Chart | Data source | Campos / filtro |
|---|---|---|
| Scorecard OTIF % | `v_otif_diario` | Métrica: `AVG(otif_pct)` o `SUM(n_otif)/SUM(n_intentadas_cerradas)` |
| Scorecard late | `v_otif_diario` | `SUM(n_late)` |
| Scorecard cycle h | `v_cycle_time` | `AVG(cycle_hours_pick_to_ata)` |
| Time series OTIF | `v_otif_diario` | Dimensión `fecha`; métrica OTIF |
| Filtros | — | `bodega_id`, rango `fecha` / semana |

### Página B — Heatmap bodega × semana

| Chart | Data source | Setup |
|---|---|---|
| Heatmap / pivot | `v_heatmap_bodega_semana` | Filas: `bodega_name`; Columnas: `semana_inicio`; Color: `late_rate` o `avg_pick_lead_minutes` |

### Página C — Funnel picking → despacho → entrega

| Chart | Data source | Setup |
|---|---|---|
| Bar / funnel | `v_funnel_logistico` | **Filtro obligatorio:** `funnel_kind = shipment_e2e` |
| Dimensión | | `stage_label` (orden por `stage_code`) |
| Métrica | | `n_shipments` |

Alternativa volume (no conversión E2E): mismo chart con `funnel_kind ∈ {movement_type, status}` — ver gaps en `docs/paso-09-looker.md`.

### Página D — Drill comuna / SKU / transportista

| Chart | Data source | Dimensiones |
|---|---|---|
| Table / bar | `v_entregas_drill` | `comuna_name`, `sku_id`/`category`, `transportista_name` |
| Métricas | | `COUNT(DISTINCT shipment_id)`, `AVG(on_time)`, `SUM(qty_delivered)` |

### Página E — Stock (secundaria)

Usar `v_stock_salud`: `is_stockout`, `doh_7d`, `is_overstock` (heurística lab; no SLA stakeholder).

---

## 3) Publicar / compartir (manual)

1. **Share** → añadir viewers (cuenta Google).
2. **File → Embed report** / link de viewer → copiar URL real.
3. Pegar en `PLACEHOLDER_LOOKER_VIEWER_URL` (arriba) y en el briefing.
4. Capturas → carpeta `looker/screenshots/` (opcional; gitignore si pesan) y actualizar placeholders.

---

## Mapa rápido vista → pregunta de negocio

| Vista | Pregunta (§3–§5 / §10) |
|---|---|
| `v_otif_diario` | OTIF % por día / bodega / carrier / comuna |
| `v_cycle_time` | Cycle pick→ATA (E2E) |
| `v_stock_salud` | Stockout / DOH / overstock demo |
| `v_heatmap_bodega_semana` | ¿Dónde (bodega×semana) sube late o pick lead? |
| `v_funnel_logistico` | Conversión picking→despacho→entrega (derivada) |
| `v_entregas_drill` | Corte por comuna, SKU, transportista |

Detalle de gaps de schema y asunciones: [`docs/paso-09-looker.md`](../docs/paso-09-looker.md) — ver sección **Decisiones y cómo defenderlas**.
