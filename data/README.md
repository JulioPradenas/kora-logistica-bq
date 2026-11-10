# Data contract — KORA Deportes (exports SAP-like)

**Lab de portafolio.** Datos sintéticos. Seed `RANDOM_SEED=42`.  
Generar: `python scripts/generate_synthetic.py`  
Proyecto GCP destino (Pasos 3+): `kora-sports`

## Archivos en `raw/`

| Archivo | Origen simulado | Filas objetivo | PK lógica |
|---|---|---|---|
| `md_bodega.csv` | Maestro WMS | 8 | `warehouse_id` |
| `md_sku.csv` | Maestro materiales | 250 | `sku_id` |
| `md_transportista.csv` | Maestro TM / 3PL | 12 | `carrier_id` |
| `sap_wms_movimientos.csv` | Export WMS movimientos | **≥30k** (typ. 50–70k) | `movement_id` |
| `sap_wms_stock_snapshot.csv` | Snapshot stock diario | ~assortment×días | (`snapshot_date`,`warehouse_id`,`sku_id`) |
| `sap_tm_entregas.csv` | Export transporte | ~25–40k | `shipment_id` |

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

## Calidad esperada post-generación

- [ ] `sap_wms_movimientos` ≥ 30 000 filas
- [ ] Horizonte ≥ 12 meses entre min/max `event_date`
- [ ] Todos los `warehouse_id` / `sku_id` / `carrier_id` de hechos existen en maestros (salvo quarantine futura)
- [ ] Intersección de `shipment_id` movimientos∩entregas > 0
- [ ] Re-run con seed 42 produce mismos conteos

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
