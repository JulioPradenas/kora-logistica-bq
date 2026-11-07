# Levantamiento de requerimientos — KORA Deportes · Logística

**Lab de portafolio.** Workshop simulado. Marca ficticia.  
**Fecha workshop (simulado):** 2026-03  
**Facilitador analista:** Julio Pradenas  
**Proyecto GCP:** `kora-sports`  
**Repo:** `kora-logistica-bq`

---

## 1. Contexto y objetivo

### Negocio
KORA Deportes es un retail deportivo chileno (ficticio) con bodegas propias y despacho last-mile a tiendas y clientes e-comm. Hoy el equipo mira:

- Movimientos y stock en exports WMS / Excel.
- Cumplimiento de entrega en planillas de transporte / 3PL.

No existe un número único (SSoT) para responder: *¿el atraso es de bodega o de calle?*

### Objetivo del engagement (lab)
Construir un **datamart único de logística** en BigQuery (medallón bronze → silver → gold) alimentado por exports SAP-like sintéticos, y un dashboard Looker Studio sobre gold.

### Fuera de alcance (explícito)
- Facturación, pricing, CRM, devoluciones e-comm profundas.
- Streaming GPS / telemetría de flota.
- Orquestación cloud (Scheduler, Functions, Composer) — solo narrativa de “siguiente escalón”.
- IAM enterprise multi-proyecto.

---

## 2. Stakeholders y dolores

| Rol | Nombre (workshop) | Dolor principal | Qué necesita ver |
|---|---|---|---|
| Jefe de bodega | Camila R. | No sabe dónde se atrasa el picking ni el % de pedidos cortados | Lead time pick, % short-pick, movimientos por tipo |
| Transporte / last-mile | Diego M. | OTIF por ruta/comuna vive en Excel del 3PL | OTIF %, late deliveries, carrier |
| Supply / inventario | Valentina S. | Stock “usable” vs comprometido no cuadra entre planillas | On-hand, reserved, DOH |
| Gerencia ops | Andrés P. | No puede atribuir cuello de botella (bodega vs calle) | Cycle time end-to-end picking→door |
| Sponsor analítica (IT/BI) | Julio (lab) | Sin contrato de datos ni grano acordado | SSoT gold, refresh batch diario |

**Owners de datos (decisión):** Ops logística / supply son dueños del dominio. Transporte aporta hechos de entrega pero el mart vive bajo el mismo sponsorship → favorece **un** datamart.

---

## 3. Agenda del workshop (90 min simulados)

| Min | Bloque | Salida |
|---|---|---|
| 0–10 | Problema y alcance | Lista in/out |
| 10–25 | Stakeholders y preguntas | Mapa rol → pregunta |
| 25–45 | KPIs y definiciones | Fórmulas + owner de cada KPI |
| 45–65 | Grano, claves, fuentes | Hechos + dims; data contract high-level |
| 65–80 | Un mart vs varios | Decisión + señales de split futuro |
| 80–90 | Prioridad MVP dashboard | 3–5 vistas Looker acordadas |

---

## 4. Preguntas de negocio (recogidas)

### Bodega / WMS
1. ¿Cuál es el lead time promedio de picking por bodega y por semana?
2. ¿Qué % de líneas/pedidos salen cortados (short-pick) por SKU/categoría?
3. ¿Qué tipos de movimiento (entrada, salida, ajuste) explican la merma o el ruido de stock?

### Last-mile
4. ¿Cuál es el OTIF % por transportista, comuna y semana?
5. ¿Cuántas entregas late y cuál es el delay mediano vs ETA?
6. ¿Hay correlación entre bodega de origen y late rate?

### Cruce (razón del mart único)
7. ¿El retraso de picking explica el late delivery del mismo pedido/shipment?
8. ¿Cuál es el cycle time end-to-end (primer pick → ATA en destino)?
9. ¿En qué tramo (bodega vs calle) se va el SLA cuando falla OTIF?

### Inventario
10. ¿Stock on-hand vs reserved por bodega×SKU y días de cobertura (DOH)?

---

## 5. KPIs acordados

| KPI | Definición (SSoT) | Grano de reporte | Owner |
|---|---|---|---|
| **Lead time pick** | `timestamp_pick_fin − timestamp_pick_inicio` (horas); avg/p50/p90 | bodega × semana | Jefe bodega |
| **% short-pick** | líneas con `qty_picked < qty_ordered` / líneas totales | bodega × semana | Jefe bodega |
| **OTIF %** | entregas con `ATA ≤ ETA` y `status = delivered` / entregas intentadas | carrier × comuna × semana | Transporte |
| **Late deliveries** | count donde `ATA > ETA` | carrier × semana | Transporte |
| **On-hand / reserved** | snapshot diario; usable = on-hand − reserved | bodega × SKU × día | Supply |
| **DOH** | on-hand usable / avg salida diaria 7d | bodega × SKU | Supply |
| **Cycle time E2E** | `ATA_entrega − pick_start` del mismo `order_id`/`shipment_id` | bodega × semana | Gerencia ops |

**Reglas de negocio (mínimas):**

- Zona horaria: `America/Santiago`.
- “Día” de hechos = fecha de evento en Santiago (no UTC crudo en gold).
- OTIF: solo estados canónicos `delivered` / `failed` / `returned`; excluir `in_transit` del denominador de OTIF cerrado del día.
- Pedidos cancelados antes de pick: fuera de lead time pick y de cycle time E2E.
- Ajustes WMS negativos con motivo `CYCLE_COUNT` cuentan para auditoría, no para fill rate de pedido.

---

## 6. Fuentes (exports SAP-like) — contrato high-level

Detalle de columnas → Paso 2 (`data/README.md`). Aquí el acuerdo de **qué** llega:

| Archivo | Sistema origen (simulado) | Contenido | Volumetría objetivo |
|---|---|---|---|
| `sap_wms_movimientos.csv` | WMS / MM | Entrada / salida / ajuste por warehouse×sku×tipo×día | **~50k–70k** filas · ~18 meses |
| `sap_wms_stock_snapshot.csv` | WMS | Snapshot diario on-hand / reserved | ~bodegas × SKUs × días |
| `sap_tm_entregas.csv` | TM / 3PL | Shipments: ETA, ATA, estado, carrier, destino | **~20k–40k** |
| `md_sku.csv` | Maestro | SKU, categoría, marca propia | cientos |
| `md_bodega.csv` | Maestro | Bodegas / CDs | ~8 |
| `md_transportista.csv` | Maestro | Carriers 3PL | decenas |

**Cadencia acordada:** batch diario (lab: re-run manual `build_all`).  
**Landing lab:** carpeta `data/raw/` del repo (GCS sería el análogo prod; mismo contrato de columnas).

---

## 7. Grano dimensional (acuerdo de modelado)

| Hecho gold | Grano (1 fila =) | Claves naturales |
|---|---|---|
| `f_movimientos_bodega` | warehouse × sku × movement_type × día | `warehouse_id`, `sku_id`, `movement_type`, `event_date` |
| `f_stock_diario` | warehouse × sku × día | `warehouse_id`, `sku_id`, `snapshot_date` |
| `f_entregas` | shipment × día de entrega/intento | `shipment_id`, `delivery_date` |

**Dims compartidas:** `d_fecha`, `d_bodega`, `d_sku`, `d_transportista`, `d_comuna_destino`, `d_estado_entrega`.

**Puente bodega ↔ last-mile:** `order_id` y/o `shipment_id` presentes en movimientos de salida y en entregas → habilita cycle time E2E y la pregunta 7–9.

**Partition / cluster (gold, a implementar en Paso 4):**

- `f_movimientos_bodega`: `PARTITION BY event_date` + `CLUSTER BY warehouse_id, sku_id`
- `f_entregas`: `PARTITION BY delivery_date` + `CLUSTER BY warehouse_id, carrier_id`

---

## 8. Decisión: un datamart vs varios

### Decisión del lab
**Un datamart** `dm_logistica_retail` (tablas en dataset `gold_kora`).

### Por qué (criterios que se cumplieron)

| Criterio | En KORA |
|---|---|
| Mismos owners | Ops logística / supply sponsorship |
| Preguntas cruzadas | Sí — picking vs late delivery |
| Grano compatible | Sí — vía `order_id` / `shipment_id` |
| Mismo SLA refresh | Batch diario ambos lados |
| Misma audiencia dashboard | Ops + gerencia logística |

### Cuándo lo partiría en un engagement real

| Señal | Acción |
|---|---|
| Ownership distinto (WMS=Supply; last-mile=Transportes 3PL con P&L propio) | `dm_wms` + `dm_last_mile` |
| Grano incompatible (stock horario vs GPS ruta) sin puente fuerte | Separar; vista puente solo con contrato |
| Refresh SLA distinto (stock 15 min vs OTIF diario) | Pipelines/marts separados |
| Seguridad: bodega no debe ver costo flete | Datasets/views + IAM distintos |

Esta tabla se repite en el one-page (Paso 10): muestra criterio semi-senior, no solo “todo en una tabla”.

---

## 9. Vocabulario fijado (para entrevistas / README)

| Término | Uso en este proyecto |
|---|---|
| **SSoT** | Gold/datamart: un número por KPI |
| **Medallion** | Bronze tipado → silver limpio → gold mart |
| **ELT** | Carga a BQ; transform en SQL |
| **Star schema** | Hechos + dims; grano explícito arriba |
| **Datamart** | Subconjunto gold del dominio logística |
| **Data contract** | Columnas/tipos/claves del export (Paso 2) |
| **Self-service** | Stakeholder explora Looker sin nuevo extract |

---

## 10. MVP dashboard Looker (prioridad)

1. **Overview:** OTIF % + cycle time E2E + late count (filtros semana / bodega).
2. **Heatmap:** bodega × semana — lead time pick o late rate.
3. **Funnel:** picking → despacho → entrega (conteos / conversión).
4. **Drill:** comuna destino, SKU/categoría, transportista.
5. **Stock salud:** on-hand vs reserved + DOH (página secundaria).

Fuente de datos: solo vistas/tablas **gold**. Nada de Looker leyendo bronze.

---

## 11. Riesgos y supuestos

| Riesgo / supuesto | Mitigación |
|---|---|
| Datos sintéticos no “huelen” a SAP | Nombrar archivos `sap_*`, códigos de movimiento y estados canónicos documentados |
| Sandbox sin billing | Tablas medianas; queries con filtro de partición; sin Composer/Dataflow |
| OTIF mal definido entre stakeholders | Fórmula escrita en §5; cambios = versionar este doc |
| Cycle time sin puente pedido | Generador (Paso 2) debe emitir `order_id`/`shipment_id` coherentes entre WMS salida y TM |

---

## 12. Criterios de aceptación del lab (vinculados al DoD)

- [ ] Este documento versionado en el repo.
- [ ] Gold responde KPIs §5 con grano §7.
- [ ] Looker consume solo gold.
- [ ] README explica un mart vs split (§8).
- [ ] Disclaimer lab visible en README y one-page.

---

## 13. Próximo paso técnico

**Paso 2:** generar CSV sintéticos + documentar data contract columna a columna en `data/README.md`.