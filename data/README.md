# Data contract — KORA Deportes (exports SAP-like)

**Lab de portafolio.** Datos sintéticos. Seed `RANDOM_SEED=42`.  
Generar: `python scripts/generate_synthetic.py`  
Proyecto GCP destino (Pasos 3+): `kora-sports`

**Horizonte fijo (determinista):** por defecto `HORIZON_END=2026-10-31` → rango `2025-04-01` → `2026-10-31` (18 meses). No usa `date.today()`. Override opcional: `HORIZON_END=YYYY-MM-DD`.

## Archivos en `raw/`

| Archivo | Origen simulado | Filas objetivo (seed 42) | PK lógica |
|---|---|---|---|
| `md_bodega.csv` | Maestro WMS | **8** | `warehouse_id` |
| `md_sku.csv` | Maestro materiales | **250** | `sku_id` |
| `md_transportista.csv` | Maestro TM / 3PL | **12** | `carrier_id` |
| `sap_wms_movimientos.csv` | Export WMS movimientos | **60 285** (núcleo 60 120 + anomalías) | `movement_id` |
| `sap_wms_stock_snapshot.csv` | Snapshot stock diario | **208 555** (núcleo 208 440 + anomalías) | (`snapshot_date`,`warehouse_id`,`sku_id`) |
| `sap_tm_entregas.csv` | Export transporte | **29 923** (núcleo 29 803 + anomalías) | `shipment_id` |

Resumen bronze seed 42: **8 / 250 / 12 / 60285 / 208555 / 29923**.

Encoding: UTF-8. Separador: `,`. Header en fila 1. Fechas: `YYYY-MM-DD`. Timestamps: `YYYY-MM-DD HH:MM:SS` (naive, calendario Chile).

Los CSV **no** se versionan en git (`data/raw/*.csv` en `.gitignore`). Regenerar con el comando de arriba; `data/raw/.gitkeep` mantiene la carpeta.

---

## `md_bodega.csv`

| Columna | Tipo lógico | Null | Descripción |
|---|---|---|---|
| warehouse_id | STRING | No | Ej. `WH-01` |
| warehouse_name | STRING | No | Nombre CD |
| region_code | STRING | No | RM, V, VI, VIII… |
| city | STRING | No | Ciudad |
| is_active | BOOL | No | `True`/`False` |

## `md_sku.csv`

| Columna | Tipo lógico | Null | Descripción |
|---|---|---|---|
| sku_id | STRING | No | Ej. `SKU-00001` |
| sku_name | STRING | No | Descripción corta |
| category | STRING | No | CALZADO / TEXTIL / ACCESORIO / EQUIPO |
| brand | STRING | No | KORA |
| unit_cost_clp | INT64 | No | Costo unitario CLP (lab) |

## `md_transportista.csv`

| Columna | Tipo lógico | Null | Descripción |
|---|---|---|---|
| carrier_id | STRING | No | Ej. `CAR-01` |
| carrier_name | STRING | No | 3PL |
| service_level | STRING | No | `STD` \| `EXP` |

## `sap_wms_movimientos.csv`

Grano: **1 fila = 1 movimiento** (warehouse × sku × tipo × evento). Agregación gold a warehouse×sku×movement_type×día es Paso 7.

| Columna | Tipo lógico | Null | Descripción |
|---|---|---|---|
| movement_id | STRING | No | PK surrogada `MOV-########` |
| event_date | DATE | No | Día del movimiento |
| warehouse_id | STRING | No | FK → md_bodega |
| sku_id | STRING | No | FK → md_sku |
| movement_code | STRING | No | SAP-like: 101, 601, 701, 311, 312 |
| movement_type | STRING | No | `IN` \| `OUT` \| `ADJ` (canónico) |
| qty | INT64 | No | Cantidad firmada (ADJ puede ser negativo) |
| qty_ordered | INT64 | Sí (vacío) | Pedido; solo OUT con pedido |
| order_id | STRING | Sí | Puente a entrega |
| shipment_id | STRING | Sí | Puente a entrega |
| pick_start_ts | DATETIME | Sí | Inicio pick (OUT pedido) |
| pick_end_ts | DATETIME | Sí | Fin pick |

**Reglas:** `movement_type` deriva de `movement_code`. Short-pick: `qty < qty_ordered` en OUT. Traslados 311/312 pueden no tener entrega.

## `sap_wms_stock_snapshot.csv`

Grano: warehouse × sku (assortment activo) × día.

| Columna | Tipo lógico | Null | Descripción |
|---|---|---|---|
| snapshot_date | DATE | No | Día del snapshot |
| warehouse_id | STRING | No | FK bodega |
| sku_id | STRING | No | FK sku |
| on_hand_qty | INT64 | No | Físico |
| reserved_qty | INT64 | No | Comprometido; ≤ on_hand en silver |

**Usable (KPI):** `on_hand_qty - reserved_qty` (regla silver/gold).

## `sap_tm_entregas.csv`

Grano: 1 fila = 1 shipment (intento/cierre de entrega).

| Columna | Tipo lógico | Null | Descripción |
|---|---|---|---|
| shipment_id | STRING | No | PK |
| order_id | STRING | No | Puente a WMS |
| warehouse_id | STRING | No | Origen |
| carrier_id | STRING | No | FK transportista |
| comuna_id | STRING | No | Código comuna |
| comuna_name | STRING | No | Nombre |
| ship_date | DATE | No | Salida bodega |
| delivery_date | DATE | No | Día entrega/intento |
| eta_ts | DATETIME | No | ETA |
| ata_ts | DATETIME | Sí | ATA; vacío si `in_transit` |
| status | STRING | No | `delivered` \| `late_delivered` \| `failed` \| `in_transit` |
| qty_delivered | INT64 | No | 0 si failed/in_transit |

**OTIF (definición workshop):** entregas con `ata_ts ≤ eta_ts` y status en (`delivered`) — en gold se normaliza `late_delivered` → late. Denominador OTIF cerrado: excluye `in_transit`.

---

## Puentes (cruces WMS ↔ last-mile)

```
sap_wms_movimientos.shipment_id  =  sap_tm_entregas.shipment_id
sap_wms_movimientos.order_id     =  sap_tm_entregas.order_id
```

Sin estos puentes no hay cycle time picking→door ni “¿el late viene de bodega?”.

## Anomalías documentadas (Paso 6 — seed 42)

Tras el núcleo limpio, `inject_anomalies` añade un presupuesto **fijo** (decenas–cientos) que emula basura típica de exports SAP B1 / CSV ops. Determinista (`random.Random(42)` dedicado). Status casing **muta in-place** (no suma filas).

| Anomalía | Entidad | Conteos | Destino silver |
|---|---|---:|---|
| Exact duplicates | movimientos / stock / entregas | 50 / 50 / 40 | 1→silver; extras = **eliminated_dupes** |
| Key duplicates (misma PK, payload distinto) | movimientos / stock / entregas | 40 / 30 / 30 | idem (ROW_NUMBER) |
| Missing FK warehouse | movimientos / entregas | 30 / 15 | **quarantine** `missing_fk_warehouse` |
| Missing FK sku | movimientos / stock | 25 / 25 | **quarantine** `missing_fk_sku` |
| Missing FK carrier | entregas | 20 | **quarantine** `missing_fk_carrier` |
| Null required date/key | movimientos / stock / entregas | 20 / 10 / 15 | **quarantine** `null_required_*` |
| Status casing / whitespace | entregas | 80 (in-place) | **normalize** (UPPER/TRIM + map); no drop |

IDs rotos fijos: `WH-99`, `SKU-99999`, `CAR-99`.

**Ecuación de reconciliación (Paso 6):**  
`bronze = silver + quarantine + eliminated_dupes`  
(status casing no aparece en quarantine ni en eliminated).

Constantes: `ANOMALY_COUNTS` en `scripts/generate_synthetic.py`.

## Calidad esperada post-generación

- [ ] `sap_wms_movimientos` ≥ 30 000 filas (núcleo limpio; bronze total 60 285 con anomalías)
- [ ] Horizonte fijo por defecto: `event_date` min=`2025-04-01`, max=`2026-10-31` (≥ 12 meses)
- [ ] FKs del **núcleo** OK; anomalías documentadas rompen FK / null / dupes a propósito
- [ ] Intersección de `shipment_id` movimientos∩entregas (núcleo) > 0
- [ ] Re-run con seed 42 (+ mismo `HORIZON_END`) produce **mismos bytes/conteos**, independiente del día de ejecución

## Extracción: CSV plano vs SAP Business One (nota de diseño)

Este lab **no** conecta a ningún ERP real. El generador emula un **export CSV plano** ya aplanado (una fila = un hecho o maestro), listo para `LOAD DATA` / pandas → bronze.

En un entorno con **SAP Business One**, la extracción típica diferiría así:

| Aspecto | Export CSV plano (este lab) | SAP Business One (hipótesis de diseño) |
|---|---|---|
| Origen | Archivo único por entidad | Service Layer (OData REST) o queries HANA/SQL sobre tablas (`OITM`, `OWHS`, documentos de inventario / entregas) |
| Forma | Ya desnormalizado / grano fijo | Objetos anidados (cabecera + líneas); hay que aplanar a este contrato |
| Auth | N/A (archivo local) | Usuario Service Layer / DB read-only; credenciales fuera de git |
| Incremental | Re-generar o re-copiar el CSV | Filtros por fecha documento / `UpdateDate`; watermark en silver |
| Semántica | Códigos SAP-*like* inventados (101, 601…) | Códigos y series reales del company DB; mapear a `movement_type` canónico aquí |

El **contrato de columnas** de esta carpeta es la promesa hacia bronze: da igual si la fuente real llega como CSV batch o como respuesta Service Layer, siempre que el loader respete tipos, PKs y puentes `order_id` / `shipment_id`.

## Fuera de este contrato

GPS de ruta, costos de flete unitarios, devoluciones e-comm, streaming. Ver levantamiento § fuera de alcance.
