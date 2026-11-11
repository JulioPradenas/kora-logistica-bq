# Guía de estilo Looker Studio — KORA Deportes

Objetivo: pulir el informe de Data Studio para que coincida con los mockups de [`looker/mockup/`](mockup/).

> **Mockups = referencia visual.** Los archivos en `looker/mockup/` (PNG + HTML) **no** son valores del dashboard en vivo. En particular son **ilustrativos** (no datos reales del informe):
>
> - los **% a tiempo** de las filas 2–5 de la tabla
> - la **forma de la curva semanal** OTIF
> - los **deltas de período** en los KPI (vs período anterior)
>
> Usa esta guía + los valores canónicos de verificación al final.

Viewer actual: https://datastudio.google.com/reporting/172aa667-fb63-4c36-84d4-4585c8ad89ba

---

## 1. Tokens de diseño

| Token | Valor |
|---|---|
| Fondo página | `#F4F6FA` |
| Navy (header / primario) | `#0B2545` |
| Navy secundarios | `#163A63`, `#1B4A78` |
| Naranja (acento / filete / meta) | `#F28C28` |
| Verde | `#2BB673` — texto `#1C8A56`, fondo `#E7F7EF` |
| Rojo | `#E5484D` — texto `#C5373B`, fondo `#FDECEC` |
| Ámbar | `#D97706` — fondo `#FFF4E5` |
| Texto | `#1F2937` |
| Muted | `#6B7280` |
| Líneas / bordes | `#E6E9F0` |
| Fuente | **Inter** o **Roboto** |
| Label KPI | 11px, uppercase, weight 600 |
| Valor KPI | 28–32px, weight 700 |
| Título card | 14px |
| Tabla | 13px |
| Radio cards | 12px |
| Sombra | suave (`0 1px 2px` + `0 8px 24px` a ~4–6% navy) |
| Grilla | 12 columnas, gutter **24px** |
| Header | **64px** navy con **filete naranja 2px** inferior |

---

## 2. Estado actual (ya aplicado en Overview)

Lo siguiente **ya está** en la página Overview del informe publicado:

1. **Tema global** — Roboto, fondo `#F4F6FA`, paleta navy / naranja / semáforo.
2. **Header** navy 64px + filete naranja 2px, con controles **Periodo** y **Bodega**.
3. **4 KPI alineados:**
   | KPI | Valor | Fuente / fórmula |
   |---|---|---|
   | OTIF | **58,06%** | `SUM(n_otif)/SUM(n_intentadas_cerradas)` sobre `v_otif_diario` — **nunca** `AVG(otif_pct)` |
   | Entregas atrasadas | **10.718** | `SUM(n_late)` sobre `v_otif_diario` |
   | Cycle time | **51,6 h** | `AVG(cycle_hours_pick_to_ata)` sobre `v_cycle_time` |
   | Envíos despachados | **29.803** | `COUNT_DISTINCT(shipment_id)` sobre **`v_entregas_drill`** |
4. **Gráfico OTIF** con línea de referencia **0,85** · etiqueta `Meta 85%` · naranja discontinua.
5. **Funnel horizontal** navy — filtro `funnel_kind = shipment_e2e` → **40.823 / 29.803 / 21.325**.
6. **Tabla** top comuna × transportista con barras + semáforo.
7. **Footer** del informe.

### Envíos despachados — fuente correcta

Usa **`v_entregas_drill`** con `COUNT_DISTINCT(shipment_id)` → **29.803**.

**No** uses `v_cycle_time` para este KPI: esa vista entrega **25.609** (población distinta; no es el mismo conjunto de envíos despachados).

---

## 3. Checklist paso a paso

Etiquetas de la UI de Data Studio en español. Abrir el informe en modo edición.  
**(a)–(f) aplicados** en Overview (2026-11-11) — detalle y capturas: [`docs/paso-11-overview-pulido.md`](../docs/paso-11-overview-pulido.md). Resta **(g)** homologar header/cards en las demás páginas.

### (a) OTIF semanal

1. Seleccionar el gráfico de series OTIF.
2. **Estilo del gráfico** / dimensión de fecha → **Dimensión de período**.
3. Opción A: granularidad **Semana ISO**.
4. Opción B: campo calculado `DATETIME_TRUNC(fecha, WEEK)`.
5. Métrica OTIF % = `SUM(n_otif)/SUM(n_intentadas_cerradas)` — **nunca** `AVG(otif_pct)`.

### (b) Sparklines en los KPI (por semana)

1. En cada scorecard KPI, añadir una dimensión de fecha con granularidad semanal.
2. Activar sparkline / mini-serie en **Estilo del gráfico** del scorecard (si el tipo lo permite) o usar un gráfico de área compacto bajo el valor.
3. Misma métrica que el KPI padre; color navy o naranja según el mockup.

### (c) Comparación vs período anterior

1. Seleccionar el control de fecha (**Periodo**).
2. Activar **comparación con el período anterior** en el control de rango de fechas.
3. Los deltas del mockup son **ilustrativos**; el valor real depende del rango elegido.

### (d) Formato condicional solo en la celda «% a tiempo»

1. Abrir la tabla comuna × transportista.
2. **Formato condicional** → aplicar **solo** a la columna / celda **% a tiempo** (no a la fila entera).
3. Reglas:
   - `< 0,50` → rojo (`#E5484D` / fondo `#FDECEC`)
   - `0,50 – 0,55` → ámbar (`#D97706` / fondo `#FFF4E5`)
   - `> 0,55` → verde (`#2BB673` / fondo `#E7F7EF`)

### (e) Tipografía de KPI

1. Seleccionar cada scorecard.
2. **Estilo del gráfico** → bajar el título / label a **11–12px** (uppercase 600).
3. Valor a **~30px** (weight 700), dentro del rango 28–32px del token.

### (f) Ordenar el funnel por `stage_code`

1. Seleccionar el gráfico funnel.
2. Ordenar por dimensión / campo **`stage_code`** (ascendente), no por el valor de la métrica.
3. Mantener filtro `funnel_kind = shipment_e2e`.

### (g) Homologar header / cards en el resto de páginas

Aplicar el mismo header navy + filete naranja y el mismo estilo de cards (radio 12px, sombra suave, títulos 14px) a:

- Heatmap
- Funnel
- Drill
- Stock salud

### Fórmulas exactas (Drill / Envíos)

```text
Envíos = COUNT_DISTINCT(shipment_id)

% a tiempo = COUNT_DISTINCT(CASE WHEN on_time THEN shipment_id ELSE NULL END)
             / COUNT_DISTINCT(shipment_id)
```

Tipo de campo / métrica: **Percentage** (porcentaje).  
No usar `AVG(on_time)` ni promediar un booleano.

---

## 4. Trampas conocidas

| Trampa | Qué pasa | Qué hacer |
|---|---|---|
| **Override de métrica a nivel chart** | El scorecard hereda AVG del data source y contradice el campo calculado | Revisar agregación en cada gráfico |
| **Cross-filters accidentales** | Un click en heatmap/funnel vacía scorecards | Preferir controles explícitos; desactivar interacciones donde no aporten; botón **Restablecer** |
| **Sesiones a medio cargar** | Valores parciales / scorecards incompletos | Recargar en incógnito; esperar a que terminen todos los charts |
| **Boolean no se promedia** | `AVG(on_time)` distorsiona el % a tiempo (grano shipment×SKU) | Usar `COUNT_DISTINCT` condicional / total |

---

## 5. Checklist de verificación final (incógnito)

Abrir el viewer en ventana de incógnito, **sin** filtros cruzados residuales (si hace falta: **Restablecer**). Valores esperados (datos sintéticos seed 42 · 2026-11-10):

| Métrica | Esperado |
|---|---|
| OTIF | **58,06%** |
| Entregas atrasadas | **10.718** |
| Cycle time | **51,6** h |
| Envíos despachados | **29.803** |
| Funnel `shipment_e2e` | **40.823 / 29.803 / 21.325** |
| Top tabla: Santiago / 3PL Partner 10 | **283** envíos · **49,82%** a tiempo |

Si algún número no cuadra: primero Restablecer filtros → recargar → confirmar que Envíos usa `v_entregas_drill` (no `v_cycle_time`) y que OTIF no es `AVG(otif_pct)`.

---

## Archivos de referencia

| Archivo | Uso |
|---|---|
| [`mockup/overview.png`](mockup/overview.png) / [`overview.html`](mockup/overview.html) | Mockup Overview (referencia visual) |
| [`mockup/drill.png`](mockup/drill.png) / [`drill.html`](mockup/drill.html) | Mockup Drill (referencia visual) |
| [`mockup/README.md`](mockup/README.md) | Disclaimer de mockups |
| [`README.md`](README.md) | Guía de conexión BQ + páginas MVP |
| [`screenshots/`](screenshots/) | Capturas reales del informe publicado |
| [`screenshots/overview/`](screenshots/overview/) | Overview pulido (Paso 11) — ver [`docs/paso-11-overview-pulido.md`](../docs/paso-11-overview-pulido.md) |
