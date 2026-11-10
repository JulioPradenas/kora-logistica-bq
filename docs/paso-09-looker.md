# Paso 9 — Looker Studio sobre `gold_kora`

**Proyecto:** KORA Deportes · Logística (WMS + last mile)  
**Prerrequisito:** Paso 8 mergeado (108 buckets + demo prune)  
**Base:** `main` @ `f7bb0b7`  
**Siguiente:** one-page / polish de portafolio (si aplica)

---

## Meta de esta sesión

1. Verificar schemas reales silver/gold (comuna, etapas picking/despacho/entrega).
2. Añadir vistas gold **11+**: heatmap, funnel, drill; reutilizar `v_otif_diario` / `v_cycle_time` / `v_stock_salud`.
3. Extender `build_gold.py` (stage 6): dry-run de las nuevas vistas + post-checks (existe, rows>0, coherencia); idempotente.
4. Guía exacta Looker Studio en `looker/README.md` (`kora-sports` / `gold_kora`) con placeholders de viewer/screenshots.
5. Pytest que falla en el código pre-Paso 9; CI verde sin credenciales GCP.

**Fuera de alcance:** merge a `main` por el agente, credenciales GCP en CI/agente, inventar URL publicada de Looker, inventar columnas de etapa.

---

## Verificación de schema (honestidad)

### Comuna — **sí existe**

| Capa | Columnas |
|---|---|
| Silver `sap_tm_entregas` | `comuna_id`, `comuna_name` NOT NULL |
| Gold `f_entregas` | `comuna_id`, `comuna_name` NOT NULL |
| Vista drill | expone ambas |

**Gap dim:** el levantamiento §7 nombra `d_comuna_destino`. **No** está en `ddl_gold.sql` ni se inventa aquí. Comuna vive **desnormalizada** en el fact (suficiente para drill Looker).

### Etapas picking → despacho → entrega — **no hay columna stage**

| ¿Existe? | Qué hay en su lugar |
|---|---|
| ❌ `stage` / `funnel_stage` / `estado_proceso` | — |
| ✅ pick | `pick_start_ts` / `pick_end_ts` en silver movimientos (OUT) |
| ✅ despacho | `ship_date` / `ship_fecha_id` en entregas |
| ✅ entrega | `delivery_date` / `ata_ts` + `status` canónico |

**No se inventan columnas.** El funnel se deriva.

### Funnel honesto (dos modos en la misma vista)

| `funnel_kind` | Semántica | Uso |
|---|---|---|
| `shipment_e2e` | `1_picking` (OUT+pick_start) → `2_despacho` (f_entregas) → `3_entrega` (delivered/late_delivered) | MVP Looker §10.3 (conversión) |
| `movement_type` | Conteos IN/OUT/ADJ | Alternativa volume si no se quiere E2E |
| `status` | Conteos por status TM | Alternativa volume last-mile |

Filtro Looker recomendado: `funnel_kind = 'shipment_e2e'`.

---

## Vistas gold (11+)

| Archivo | Vista | Rol |
|---|---|---|
| `08_v_otif_diario.sql` | `v_otif_diario` | Overview OTIF (reuso) |
| `09_v_cycle_time.sql` | `v_cycle_time` | Overview cycle (reuso) |
| `10_v_stock_salud.sql` | `v_stock_salud` | Stock secundaria (reuso) |
| **`11_v_heatmap_bodega_semana.sql`** | `v_heatmap_bodega_semana` | Heatmap bodega × semana (`late_rate`, pick lead) |
| **`12_v_funnel_logistico.sql`** | `v_funnel_logistico` | Funnel + alternativas |
| **`13_v_entregas_drill.sql`** | `v_entregas_drill` | Drill comuna / SKU / transportista |

**SKU en drill:** `f_entregas` no tiene `sku_id`. Se une OUT WMS por `shipment_id` → grano **shipment × sku** (fan-out posible).

---

## `build_gold.py` (stage 6)

- `BUILD_FILES` incluye `11`–`13` tras KPI `08`–`10`.
- `--dry-run`: `QueryJobConfig(dry_run=True)` en cada CTAS/VIEW (idempotente, sin writes).
- Apply: `CREATE OR REPLACE VIEW` (sandbox OK).
- `post_run_checks` → `post_run_view_checks`:
  - cada vista en `VIEW_TABLES` **existe** y **rows > 0**
  - heatmap: ≥1 bodega, ≥1 semana, `late_rate` no null
  - funnel `shipment_e2e`: 3 stages; entrega ≤ despacho; conteos > 0
  - drill: sin comuna/carrier vacíos; al menos un sku bridged

---

## Looker Studio

Guía paso a paso: [`looker/README.md`](../looker/README.md).

- Data sources solo `kora-sports.gold_kora.*`
- Placeholders: `PLACEHOLDER_LOOKER_VIEWER_URL`, screenshots — **no inventar link**

---

## Comandos

```bash
make setup
make test                 # pytest (incluye test_paso09_*; sin GCP)
make -n run               # plan stages (build_gold = stage 6)
make run DRY=1            # dry-run real BQ (laptop Julio + ADC)
make run                  # apply + verify
python scripts/build_gold.py --dry-run
python scripts/build_gold.py
```

---

## Criterios de aceptación

- [x] Gaps documentados (sin columnas inventadas)
- [x] Vistas 11–13 + reuso 08–10
- [x] Post-checks vistas en `build_gold`
- [x] `looker/README.md` con placeholders
- [x] pytest Paso 9; CI sin GCP
- [ ] Julio publica informe Looker y reemplaza placeholders (manual laptop)

---

## Decisiones y cómo defenderlas

Tres gaps del storyboard Looker vs el contrato real. Respuestas cortas para entrevista.

### 1) Funnel picking → despacho → entrega (sin columna `stage`)

**Hecho:** no existe `stage_*` / `estado_proceso` en bronze/silver/gold.  
**Decisión:** funnel **derivado** (`funnel_kind = shipment_e2e`) con columnas reales:

| Etapa | Definición (columnas) | Conteo seed 42 |
|---|---|---:|
| picking | OUT WMS con `shipment_id` + `pick_start_ts` | **40 823** |
| despacho | shipments en `f_entregas` (`ship_fecha_id` NOT NULL) | **29 803** |
| entrega | `status ∈ {delivered, late_delivered}` | **21 325** |

**Lógica de subconjunto (no es BPM perfecto):** entrega ⊆ despacho (21 325 ≤ 29 803). Picking (40 823) **no** es subconjunto de despacho: hay OUT con pick que no cruzan a TM (traslados, short-pick sin shipment TM, etc.). Por eso no se vende como conversión 100 % encadenada; se vende como funnel operativo derivado. Alternativa volume en la misma vista: `movement_type` / `status`.

**Riesgo funnel — etapa inferida:** las etiquetas picking/despacho/entrega **no** son eventos de proceso en el origen; se **infieren** de timestamps/`status`. Un cambio de semántica en `pick_start_ts` o en el mapa de `status` mueve los conteos sin que exista una tabla de etapas. En demo se declara el proxy; no se presenta como workflow engine.

- **Alternativa descartada y por qué:** inventar `stage_*` en gold (o forzar picking ⊆ despacho ⊆ entrega). Rompe linaje (campo sin origen) y miente sobre conversión; picking > despacho es un hecho del seed, no un bug a ocultar.
- **En producción:** event log / status history del WMS+TM (o CDC) con etapa explícita; hasta entonces, proxy documentado + `funnel_kind` volume como control.
- **Respuesta de entrevista:** “No inventé `stage`: el funnel es derivado — 40 823 / 29 803 / 21 325 — y dejo claro que picking no es subconjunto de despacho. El riesgo es etapa inferida; en prod pediría el event stream, no una columna fabricada en gold.”

### 2) Comuna sin `d_comuna_destino`

**Hecho:** levantamiento §7 nombra la dim; `ddl_gold` **no** la materializa. Sí hay `comuna_id` / `comuna_name` NOT NULL en `f_entregas`.  
**Decisión:** tratar comuna como **dim degenerada** (atributos desnormalizados en el fact). Suficiente para filtros/drill Looker. No inventar `d_comuna_destino` sin maestro de origen ni SCD.

- **Alternativa descartada y por qué:** crear `d_comuna_destino` con `DISTINCT` del fact. Parece dim pero no hay maestro, SCD ni dueño de datos; es un distinct disfrazado y confunde el star.
- **En producción:** maestro geográfico (o export SAP de destinos) → dim tipada + FK; hasta entonces, degenerate dim en el fact es honesta.
- **Respuesta de entrevista:** “Comuna ya viene en el fact; la usé como dim degenerada. No armé `d_comuna_destino` con un DISTINCT porque eso finge un maestro que no existe.”

### 3) SKU en drill de entregas

**Hecho:** `f_entregas` no trae `sku_id`.  
**Decisión:** bridge OUT WMS por `shipment_id` → grano **shipment × sku** (fan-out si el shipment tiene varios SKU).  
**Mitigación fan-out en Looker:** métricas de shipment con `COUNT(DISTINCT shipment_id)` / `COUNT_DISTINCT`; no sumar `qty_delivered` a ciegas al cortar por SKU (se repetiría por línea). Para qty a nivel SKU usar `qty_out` del bridge WMS.

- **Alternativa descartada y por qué:** meter `sku_id` “principal” en `f_entregas` o explotar el fact a shipment×sku en CTAS. Pierde grano acordado del fact (1 shipment) o inventa un SKU representativo sin regla de negocio.
- **En producción:** línea de entrega / delivery item en el export TM, o bridge formal WMS↔TM versionado; Looker sigue con `COUNT DISTINCT` si el grano es multi-SKU.
- **Respuesta de entrevista:** “El fact de entregas no trae SKU; lo cruzo por `shipment_id` con el OUT WMS y mitigo el fan-out con `COUNT DISTINCT`. No metí un sku_id inventado en el fact.”

### Narrativa de migración (solo lab)

En un cutover tipificado el linaje sería **SAP B1 (o export ops) → CSV contrato → BigQuery** medallón bronze→silver→gold, y Looker solo sobre gold. Este repo **simula** ese camino con CSV sintético SAP-like y el mismo contrato de columnas; no hay conexión SAP real ni claims de empleador. Si en producción faltara una etapa de proceso o una dim, se documenta el proxy (timestamps/status, degenerate dim, bridge) en vez de fabricar el campo en gold.

---

## Límites

- Sin billing → table expiration ~60d en sandbox.
- Agente cloud: sin ADC → no ejecuta `make run` real; solo mocks CI.
- Marca KORA ficticia; sin claims Esparta.
