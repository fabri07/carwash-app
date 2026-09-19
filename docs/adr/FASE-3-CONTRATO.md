# Contrato — Fase 3: modelo de dominio y aislamiento probado

**Fecha:** 2026-09-18 · **Tiempo:** T1 (congelado al entrar a T2). Cambiarlo durante T2/T3 es reabrir la
fase con el dueño (regla 5 del roadmap).

**Objetivo:** el esquema completo del núcleo operativo, migrado, con RLS activo, las máquinas de estado
validadas en el dominio y el aislamiento verificado **tabla por tabla**. **Sin UI y sin endpoints HTTP.**

**Fuentes:** `docs/ROADMAP.md` (Fase 3), `docs/spec/05-decisiones-producto.md` (D-001…D-008, mandan),
`docs/spec/02a|02b|02c-reglas-*.md` (R-T, R-O, R-C). Las reglas se citan por id; los documentos de spec son
local-only, por eso este contrato vive en `docs/adr/`.

Etiquetas: **[migrar]** el legacy lo hace bien · **[corregir]** bug del legacy, se hace lo contrario ·
**[diseñar]** no existe en el legacy, se decide acá · **[abierto]** lo decide el dueño; el esquema no
lo cierra (ver §9).

---

## 0. Decisiones transversales

| # | Decisión | Por qué |
|---|---|---|
| X1 | **El lavadero es el tenant.** El roadmap dice `businesses`/`business_id`; la F2 ya construyó `tenants`/`tenant_id` con `TenantMixin` (ADR-0001). No se crea `businesses`. `tenants` suma `timezone` y `currency`. | Dos nombres para la misma raíz de aislamiento es la deuda que ADR-0001 elimina. |
| X2 | **Sin endpoints en F3.** Los casos de uso viven en `application/services/` y se prueban directo. Cada endpoint llega en su fase (F4–F7) con su test HTTP de aislamiento. | Es la tabla de equipo del roadmap (T2 = modelos ‖ repositorios+servicios). Un CRUD genérico ahora sería código que F4–F7 reescriben. |
| X3 | **Dinero en `BIGINT` de centavos** (`*_cents`), nunca float ni `NUMERIC` mezclado. Porcentajes en **puntos básicos** `INTEGER` (`*_bps`, 0..10000). Redondeo único: half-up al centavo. | `parseMoney_("20.000") → 20` (R-O-029). Enteros de punta a punta: sin `Decimal` que viaje como string en JSON. |
| X4 | **FKs compuestas con `tenant_id`.** Toda FK entre tablas de tenant es `(tenant_id, x_id) → padre(tenant_id, id)`. Todo padre lleva `UNIQUE (tenant_id, id)`. | RLS filtra la fila propia, pero **el chequeo de FK no pasa por RLS**: sin esto, B podría insertar un job que apunte al cliente de A conociendo su UUID. Con esto es imposible en la base. |
| X5 | **Toda FK tiene índice** cuyo prefijo son sus columnas. Índices compuestos con **nombre explícito** `ix_<tabla>_<col1>_<col2>`. | Postgres no indexa FKs solo (skill *supabase-postgres-best-practices*, `schema-foreign-key-indexes`). La `naming_convention` `ix_%(column_0_label)s` repite nombre en compuestos que empiezan igual. |
| X6 | **Enums nativos de Postgres**, valores en MAYÚSCULA, congelados en la migración (como `role` y `void_reason`). | Convención de F2 (ADR-0005). Agregar un valor es una migración, no un literal. |
| X7 | **Unicidad "entre vivos" con índice único parcial** `WHERE voided_at IS NULL` (y `postgresql_where`/`sqlite_where` iguales). | Anular (ADR-0003) no puede bloquear volver a cargar el mismo código o patente. |
| X8 | **`job_events` es append-only en la base**: sin `updated_at`, sin anulación, `carwash_app` solo con `SELECT, INSERT`, y un trigger que rechaza `UPDATE`/`DELETE` aun al dueño. | ROADMAP: es la fuente de verdad. Un `GRANT` se puede ampliar por error; el trigger no se saltea sin una migración visible. |
| X9 | **Estados derivados no se guardan**: saldo, estado de pago, seña pagada, métricas de cliente. | El legacy guarda dos columnas de estado que se desincronizan (R-O-007, C-5) y reconstruye `CLIENTES` de las transacciones (R-C-004). |
| X10 | **Hora del cliente vs. hora del server.** Todo `occurred_at`/`arrived_at` lo manda el cliente; `created_at` es del server. **No hay CHECKs de orden temporal entre columnas** (`finished_at ≥ started_at`). | Con la cola offline dos dispositivos con relojes distintos producen órdenes legítimos "imposibles". La máquina de estados ordena; el reloj no. |
| X11 | **`btree_gist` lo crea el superusuario** en `create_roles.sh`, no la migración. La migración verifica que exista y, si no, corta con un mensaje accionable. | `carwash_owner` tiene `CONNECT` pero no `CREATE` sobre la base (verificado en `create_roles.sh`): `CREATE EXTENSION` fallaría en el primer deploy. |
| X12 | **Se evaluó y no se adopta** envolver `current_setting` en `(SELECT …)` en las políticas RLS. | La skill lo recomienda para tablas grandes; acá cada tenant tiene miles de filas, no millones, y cambiarlo reescribe la política de las tablas de F2. Se revisa con datos reales. |
| X13 | **`dummy_resources` se queda** en F3. | Los tests HTTP de aislamiento de F2 corren sobre ese recurso. Se borra cuando exista el primer endpoint de dominio (F4). |

---

## 1. Tablas

Todas usan `TenantScopedModel` (`id` uuid, `tenant_id`, `created_at`, `updated_at`, `voided_at`,
`void_reason`) salvo que se diga otra cosa. Se listan solo las columnas propias. `NN` = NOT NULL.
Toda FK es compuesta (X4) y `ondelete="RESTRICT"`.

### 1.0 `tenants` (se altera)

| Columna | Tipo | Nota |
|---|---|---|
| `timezone` | text NN default `'America/Argentina/Cordoba'` | Hardcodeada en el legacy (tabla de constantes). Fechas de turno y demora se calculan en esta zona. |
| `currency` | text NN default `'ARS'` | `CHECK (currency ~ '^[A-Z]{3}$')`. |

El resto de la configuración del negocio (tolerancia 20 min, ventana 5 min, 720 min, hold 30 min…) es
**Fase 4**. En F3 las funciones de dominio la reciben **como parámetro**, nunca como constante.

### 1.1 Catálogo

**`vehicle_sizes`** — tamaños del lavadero, catálogo del tenant (R-C, §3).

| Columna | Tipo |
|---|---|
| `code` | text NN, `CHECK (code ~ '^[A-Z0-9_]{1,30}$')` (`AUTO`, `SUV`, `PICKUP`, `UTILITARIO`, `CAMION_PEQUENO`) |
| `label` | text NN |
| `sort_order` | integer NN default 0 |

Único vivo `(tenant_id, code)`.

**`services`** (D-006, R-C-017/018)

| Columna | Tipo |
|---|---|
| `name` | text NN |
| `pricing_mode` | enum `pricing_mode` NN: `PRECIO_FIJO`, `A_COTIZAR` |
| `notes` | text (la prosa de `Regla_horaria`) |

Único vivo `(tenant_id, name)`. **[corregir]** R-T-002: borrar el precio **no** convierte el servicio en
"a cotizar"; la modalidad es explícita. La regla horaria estructurada es F5.

**`service_prices`** — servicio × tamaño.

| Columna | Tipo | Nota |
|---|---|---|
| `service_id` | FK NN | |
| `vehicle_size_id` | FK NN | |
| `price_cents` | bigint **NULL** | `CHECK (price_cents IS NULL OR price_cents > 0)`. NULL en `A_COTIZAR` (D-006). |
| `duration_min` | integer NN | `CHECK (duration_min > 0)`. Obligatoria aun sin precio (D-006). |
| `deposit_bps` | integer NN default 0 | `CHECK (deposit_bps BETWEEN 0 AND 10000)`. Única fuente del % de seña (hoy en 3 lugares, R-O-031). |

Único vivo `(tenant_id, service_id, vehicle_size_id)`. La coherencia con la modalidad (`PRECIO_FIJO` ⇒
precio; `A_COTIZAR` ⇒ sin precio y seña 0) cruza tablas: la valida el **servicio de aplicación** y tiene
test. El monto de la seña **no se guarda**: `round_half_up(price × bps / 10000)` (R-T-003).

**`resources`** — puestos de lavado (R-T-010).

| Columna | Tipo |
|---|---|
| `name` | text NN |
| `sort_order` | integer NN default 0 |

Único vivo `(tenant_id, name)`. Un lavadero de un puesto es una fila: no hay caso especial.

**`business_hours`** — franjas de atención (R-T-004, R-T-006). Varias por día (mañana y tarde).

| Columna | Tipo |
|---|---|
| `weekday` | smallint NN, `CHECK (weekday BETWEEN 1 AND 7)` (ISO: 1 = lunes) |
| `opens_at` / `closes_at` | time NN, `CHECK (closes_at > opens_at)` |

Índice `(tenant_id, weekday)`. Qué servicio se ofrece en qué franja es F5.

**`payment_methods`** — medios de pago del negocio (R-O-027/028). Reemplaza el rango fijo `A28:C31`.

| Columna | Tipo |
|---|---|
| `code` | text NN, `CHECK (code ~ '^[A-Z0-9_]{1,30}$')` (`EFECTIVO`, `TRANSFERENCIA`, `DEBITO`, `CREDITO`, `MERCADO_PAGO`) |
| `label` | text NN |
| `commission_bps` | integer NN default 0, `CHECK (commission_bps BETWEEN 0 AND 10000)` |
| `settlement_account` | text (Caja, Banco/CVU, Mercado Pago Point) |
| `for_income` / `for_expense` | boolean NN default true / false |

Único vivo `(tenant_id, code)`. **[abierto]** tasas reales del Point (pendiente de 05).

### 1.2 Clientes y vehículos

**`customers`** — entidad de primera clase (R-C-004, R-C-012). **[corregir]** nunca se reconstruye.

| Columna | Tipo | Nota |
|---|---|---|
| `name` | text NN | `CHECK (length(name) BETWEEN 1 AND 80)` |
| `phone_e164` | text NULL | `CHECK (phone_e164 ~ '^\+[1-9][0-9]{7,14}$')`. NULL permitido (agenda interna sin WhatsApp, R-C-001). |
| `phone_raw` | text NULL | lo que se tipeó |
| `email` | text NULL | |
| `acquisition_channel` | enum `channel` NULL | canal de **origen**: se fija una vez (R-C-002 guardaba el último). |
| `notes` | text NULL | |
| `legacy_ids` | text[] NULL | `CLI-…` opacos; **contienen teléfono** (PII, ver §7). |

Único vivo parcial `(tenant_id, phone_e164) WHERE phone_e164 IS NOT NULL`. **[abierto]** familias que
comparten WhatsApp: se arranca estricto porque relajar un único es una migración trivial y agregarlo
sobre datos duplicados no (§9).

**`vehicles`** (R-C-008/009/010)

| Columna | Tipo | Nota |
|---|---|---|
| `plate` | text NULL | como se ve |
| `plate_normalized` | text NULL | `CHECK (plate_normalized ~ '^[A-Z0-9]{1,10}$')`. `SIN000` y similares → NULL. |
| `plate_format` | enum `plate_format` NULL: `AR_1994`, `MERCOSUR`, `OTRO` | `OTRO` se acepta con advertencia: no se bloquea la recepción. |
| `vehicle_size_id` | FK NULL | tamaño habitual; el del trabajo va en el job. |
| `brand_model` | text NULL | `CHECK (length(brand_model) <= 40)` |
| `color` | text NULL | |
| `notes` | text NULL | |
| `legacy_id` | text NULL | `VEH-{tel}-{patente}`: PII. |

Único vivo parcial `(tenant_id, plate_normalized) WHERE plate_normalized IS NOT NULL`. Corregir la
patente no cambia el `id` (**[corregir]** R-C-008).

**`customer_vehicles`** — vínculo con vigencia (R-C-010: hoy un auto no puede cambiar de dueño).

| Columna | Tipo |
|---|---|
| `customer_id` / `vehicle_id` | FK NN |
| `valid_from` | timestamptz NN default now |
| `valid_to` | timestamptz NULL, `CHECK (valid_to IS NULL OR valid_to > valid_from)` |
| `is_primary` | boolean NN default false |

Único vivo parcial `(tenant_id, customer_id, vehicle_id) WHERE valid_to IS NULL`. Se permiten N
clientes vigentes sobre el mismo auto (familia/empresa; ninguna regla lo prohíbe).

### 1.3 Agenda

**`bookings`** — el turno. Una sola tabla para turnero web, carga del panel y agenda interna
(R-C-036, recomendación §12.6 de 02c). **[corregir]** la agenda interna ya no se saltea el bloqueo.

| Columna | Tipo | Nota |
|---|---|---|
| `code` | text NN | `TUR-…`/`INT-…` legado o generado; único vivo `(tenant_id, code)` (**[corregir]** R-T-020: hoy sin unicidad). |
| `source` | enum `booking_source` NN: `TURNERO_WEB`, `PANEL`, `AGENDA_INTERNA`, `LISTA_ESPERA` | **quién lo cargó**, distinto del canal. |
| `channel` | enum `channel` NN | D-004, **de dónde vino el cliente**. |
| `status` | enum `booking_status` NN | §2.1 |
| `resource_id` | FK NN | |
| `start_at` / `end_at` | timestamptz NN | `CHECK (end_at > start_at)` |
| `hold_expires_at` | timestamptz NULL | vence la retención de un `PENDIENTE_SEÑA` **o** `PENDIENTE_COTIZACION`. `CHECK (status NOT IN ('PENDIENTE_SEÑA','PENDIENTE_COTIZACION') OR hold_expires_at IS NOT NULL)`. **[corregir]** C-03: la cotización pendiente tapaba el horario para siempre. |
| `customer_id` | FK NN | toda captura crea o resuelve cliente (R-C-012). |
| `vehicle_id` | FK NULL | la patente es opcional en la web (R-T-016). |
| `service_id` / `vehicle_size_id` | FK NN | |
| `service_name_snapshot` | text NN | |
| `duration_min` | integer NN `> 0` | snapshot: el bloqueo usa esta copia, no la duración actual (R-T-012). |
| `price_cents` | bigint NULL `> 0` | snapshot; NULL mientras sea a cotizar. |
| `deposit_bps` | integer NN default 0 | snapshot |
| `deposit_required_cents` | bigint NN default 0 `>= 0` | `CHECK (price_cents IS NOT NULL OR deposit_required_cents = 0)`: sin precio no hay seña (**[corregir]** R-C-032). |
| `quote_id` | FK NULL | |
| `terms_version` | text NULL | |
| `terms_accepted_at` | timestamptz NULL | Las altas del panel **no** estampan aceptación (**[corregir]** C-15). |
| `notes` | text NULL | `CHECK (length(notes) <= 500)` |
| `legacy_id` | text NULL | |

**La garantía de no solapamiento** (ROADMAP, R-T-010, R-T-018):

```sql
ALTER TABLE bookings ADD CONSTRAINT xc_bookings_sin_solapamiento
  EXCLUDE USING gist (
    tenant_id WITH =, resource_id WITH =, tstzrange(start_at, end_at, '[)') WITH &&
  ) WHERE (status IN ('PENDIENTE_SEÑA','PENDIENTE_COTIZACION','CONFIRMADO','RECIBIDO')
           AND voided_at IS NULL);
```

Se declara en el modelo con `postgresql.ExcludeConstraint(...).ddl_if(dialect="postgresql")` para que
la suite SQLite siga levantando con `create_all`.

**Vencimiento contra la exclusión** (contradicción 1 del turnero): el `WHERE` de un `EXCLUDE` no puede
depender de `now()`. Regla: **antes de insertar o confirmar un turno, el servicio vence en la misma
transacción** los `PENDIENTE_SEÑA`/`PENDIENTE_COTIZACION` del mismo puesto con
`hold_expires_at <= now()` (un solo comparador, **[corregir]** C-16). Así la base nunca rechaza una
reserva por un hold ya vencido, y el job periódico de F5 es solo limpieza.

**`schedule_blocks`** — bloqueos (BLOQUEOS, 4 filas reales, R-T-013, R-T-052…055).

| Columna | Tipo | Nota |
|---|---|---|
| `resource_id` | FK NULL | NULL = todo el lavadero |
| `starts_at` / `ends_at` | timestamptz NN | `CHECK (ends_at > starts_at)`. Día completo = `[00:00, 00:00 del día siguiente)` en la zona del tenant. |
| `reason` | text NN default `'Bloqueo operativo'` | |
| `created_by_user_id` | FK NN | reemplaza el responsable hardcodeado. |

Desactivar = anular con `DESACTIVADO` (R-T-054: nunca se borran). **Fuera** de la exclusión de
`bookings`: un bloqueo se puede crear sobre turnos vigentes y no los cancela (R-T-052); lo chequea el
motor de disponibilidad (F5).

Quedan para F5, con motivo: `waitlist_requests` (vacía, sin validar; R-T-047…051),
`availability_windows` (`DISPONIBILIDAD_SEMANAL` vacía y sin escritor, C-01, **[abierto]**),
`terms_versions`, `block_affected_bookings`.

### 1.4 Operación

**`jobs`** — el lavado. Nace cuando el auto llega (R-O-001/011/012). Un ingreso sin turno es un job sin
booking.

| Columna | Tipo | Nota |
|---|---|---|
| `booking_id` | FK NULL | Único vivo parcial `(tenant_id, booking_id) WHERE booking_id IS NOT NULL`: recibir es idempotente (**[corregir]** R-O-010). |
| `quote_id` | FK NULL | |
| `customer_id` | FK **NULL** | **[abierto]** el alta rápida del roadmap exige patente, tipo, servicio y responsable; el legacy además nombre y WhatsApp. |
| `vehicle_id` | FK NN | la patente es obligatoria en el alta rápida. |
| `vehicle_size_id` / `service_id` | FK NN | nunca texto libre (R-O-005). |
| `resource_id` | FK NULL | en qué puesto se lava; la ocupación de walk-ins es F5/F6. |
| `responsible_user_id` | FK NN | reemplaza el literal hardcodeado (R-O-038). |
| `channel` | enum `channel` NN | |
| `status` | enum `job_status` NN | **caché** del último `job_events.to_status` (§2.2). |
| `service_name_snapshot` | text NN | |
| `base_price_cents` | bigint NN `> 0` | de `service_prices` o de la cotización aceptada. Un `A_COTIZAR` no se recibe sin cotización aceptada (D-006, C-17). |
| `surcharge_cents` / `discount_cents` | bigint NN default 0, `>= 0` | separados (R-O-032). |
| `discount_reason` | text NULL | `CHECK (discount_cents = 0 OR discount_reason IS NOT NULL)` |
| | | `CHECK (base_price_cents + surcharge_cents - discount_cents > 0)` (R-O-003). El total pactado se **deriva**. |
| `deposit_required_cents` | bigint NN default 0 | snapshot del booking. |
| `scheduled_at` | timestamptz NULL | snapshot de `booking.start_at`; NULL en walk-in. |
| `arrived_at` | timestamptz NN | la pone el cliente (**[corregir]** R-O-006: el server inflaba la demora). |
| `arrival_delay_min` | integer NULL | **con signo** (llegar antes también se registra); NULL sin turno. |
| `started_at` / `finished_at` / `settled_at` / `picked_up_at` | timestamptz NULL | cachés de los eventos. NULL = no pasó (nunca `0`). |
| `notes` | text NULL | |
| `legacy_id` | text NULL | único vivo parcial `(tenant_id, legacy_id)`. |
| `legacy_review_flags` | text[] NULL | señales de D-008. |

**`job_events`** — append-only (X8). **No** usa `TenantScopedModel`: `Base` + `UUIDPrimaryKeyMixin` +
`TenantMixin` + `created_at`.

| Columna | Tipo | Nota |
|---|---|---|
| `job_id` | FK NN | |
| `event_type` | enum `job_event_type` NN | §2.2 |
| `from_status` / `to_status` | enum `job_status` NULL | `CHECK ((from_status IS NULL AND to_status IS NULL) OR to_status IS NOT NULL)`. |
| `occurred_at` | timestamptz NN | del cliente. |
| `created_at` | timestamptz NN default now | del server. |
| `actor_user_id` | FK NN | siempre identificado (D-001.4). |
| `idempotency_key` | text NN | `UNIQUE (tenant_id, idempotency_key)`: un reenvío de la cola offline no duplica. |
| `metadata` | jsonb NN default `'{}'` | `reason`, `delay_min`, `tolerance_min`, `amount_cents`, `payment_id`, `source`. |

`CHECK (event_type <> 'DEPOSIT_RETENTION_REVERSED' OR length(btrim(metadata->>'reason')) > 0)` (D-001.4).
Índice `(tenant_id, job_id, occurred_at)`.

**`job_inspections`** — opcional, 0..1 por job, todo nullable (D-007). *No reconstruir `CONTROL_INICIO`.*

| Columna | Tipo |
|---|---|
| `job_id` | FK NN, único vivo `(tenant_id, job_id)` |
| `dirt_level` | enum `dirt_level` NULL (6 niveles del legacy, R-O-001 B15) |
| `pre_existing_damage` / `valuables` | text NULL |
| `photo_consent` | boolean NULL |
| `checklist` | jsonb NULL (ítem → bool) |
| `inspected_by_user_id` | FK NULL |
| `inspected_at` | timestamptz NULL |

**`quotes`** — **[diseñar]** (R-C-027: el legacy es insert-only).

| Columna | Tipo | Nota |
|---|---|---|
| `customer_id` | FK NN | |
| `vehicle_id` | FK NULL | |
| `service_id` / `vehicle_size_id` | FK NN | |
| `booking_id` | FK NULL | una cotización puede nacer sin turno (la fila real no tenía, R-C-028). |
| `status` | enum `quote_status` NN | §2.3 |
| `requested_at` | timestamptz NN | |
| `agreed_price_cents` | bigint NULL `> 0` | NULL = sin cotizar (el legacy usaba `0`, R-C-024). |
| `agreed_duration_min` | integer NULL `> 0` | pisa la del catálogo. |
| | | `CHECK (status NOT IN ('COTIZADO','ACEPTADO') OR (agreed_price_cents IS NOT NULL AND agreed_duration_min IS NOT NULL))` |
| `supplies_purchaser` | enum `supplies_purchaser` NN default `A_DEFINIR`: `A_DEFINIR`, `CLIENTE`, `NEGOCIO`, `NO_APLICA` | |
| `supplies_cost_cents` | bigint NULL `>= 0` | |
| `expires_at` | timestamptz NULL | TTL propio (R-C-026). |
| `quoted_at` / `decided_at` | timestamptz NULL | |
| `decided_by_user_id` | FK NULL | |
| `notes` / `legacy_id` | text NULL | |

Una cotización viva por turno: único parcial `(tenant_id, booking_id) WHERE booking_id IS NOT NULL AND
status IN ('PENDIENTE','COTIZADO','ACEPTADO') AND voided_at IS NULL`.

**`cancellations`** — cancelación de un turno y resolución de la seña (D-005). **[diseñar]** el cierre:
cuatro caminos abren un caso y ninguno lo cierra (C-02).

| Columna | Tipo | Nota |
|---|---|---|
| `booking_id` | FK NN | **único** `(tenant_id, booking_id)`: una cancelación por turno. Idempotente: una segunda solicitud devuelve la existente (R-T-035). Resuelve C-13 porque el cliente solo cancela desde estados activos (§2.1). |
| `initiator` | enum `cancellation_initiator` NN: `CLIENTE`, `NEGOCIO` | |
| `classification` | enum `cancellation_classification` NN: `NORMAL`, `TARDIA`, `POSTERIOR_AL_TURNO`, `OPERATIVA` | valores exactos del código (R-T-037, C-06). |
| `requested_at` | timestamptz NN | |
| `anticipation_min` | integer NN | `floor((start − requested)/60)`; puede ser negativo. |
| `reason` | text NN default `'Prefiere no informarlo'` | `CHECK (length(reason) <= 160)` |
| `previous_status` / `resulting_status` | enum `booking_status` NN | |
| `deposit_paid_cents` | bigint NN `>= 0` | snapshot al cancelar. |
| `deposit_status` | enum `deposit_status` NN | §2.4 |
| `resolved_at` / `resolved_by_user_id` / `resolution_reason` | NULL | `CHECK (deposit_status NOT IN ('DEVUELTA','RETENIDA','REPROGRAMADA') OR (resolved_at IS NOT NULL AND resolved_by_user_id IS NOT NULL AND length(btrim(resolution_reason)) > 0))` |
| `refund_payment_id` | FK NULL | `CHECK (deposit_status <> 'DEVUELTA' OR refund_payment_id IS NOT NULL)` |
| `actor_user_id` | FK NULL | NULL cuando cancela el cliente desde la web. |
| `terms_version` | text NULL | la **del turno**, no la vigente (R-T-057). |

`CANCELADO_DEMORA` **no** crea fila acá: su retención se audita en `job_events` (D-001.5).

### 1.5 Dinero

**`payments`** — 1:N (**[corregir]** R-O-026: un segundo cobro pisaba al primero).

| Columna | Tipo | Nota |
|---|---|---|
| `job_id` / `booking_id` | FK NULL | `CHECK (job_id IS NOT NULL OR booking_id IS NOT NULL)`. La seña se cobra antes de que exista el job (R-O-030); al recibir, el servicio **vincula** las señas del turno al job. |
| `kind` | enum `payment_kind` NN: `SEÑA`, `SALDO`, `DEVOLUCION` | la devolución resta. |
| `amount_cents` | bigint NN `> 0` | el signo lo da `kind`. |
| `payment_method_id` | FK NN | nunca "A definir" (R-O-028). |
| `commission_bps` | integer NN | snapshot de la tasa del medio. |
| `commission_cents` | bigint NN `>= 0` | **siempre calculado**, costo interno; no toca el precio al cliente (**[corregir]** C-5: eran literales `0`). |
| `occurred_at` | timestamptz NN | del cliente. |
| `actor_user_id` | FK NN | |
| `idempotency_key` | text NN | `UNIQUE (tenant_id, idempotency_key)`: un cobro nunca se duplica. |
| `reference` / `notes` / `legacy_id` | text NULL | |
| `legacy_review_flags` | text[] NULL | D-008 |

Anular un cobro = `VoidableMixin` (ADR-0003) **más** el evento `PAYMENT_VOIDED` con motivo cuando el
pago es de un job.

**`cash_movements`** — libro de caja mínimo. Las reglas no definen apertura, cierre ni arqueo: eso es
F7 (**[abierto]**). F7B agrega `kind`s por migración.

| Columna | Tipo | Nota |
|---|---|---|
| `direction` | enum `cash_direction` NN: `ENTRADA`, `SALIDA` | |
| `kind` | enum `cash_movement_kind` NN: `COBRO`, `DEVOLUCION`, `AJUSTE` | |
| `amount_cents` | bigint NN `> 0` | |
| `payment_method_id` | FK NN | |
| `payment_id` | FK NULL | único vivo parcial `(tenant_id, payment_id)`: un movimiento por pago. |
| `occurred_at` | timestamptz NN | |
| `actor_user_id` | FK NN | |
| `idempotency_key` | text NN | `UNIQUE (tenant_id, idempotency_key)` |
| `notes` | text NULL | |

La comisión **no** es un movimiento de caja.

### 1.6 Enums

`channel` (D-004): `TURNERO_WEB`, `WHATSAPP`, `INSTAGRAM`, `REFERIDO`, `CALLE`, `CLIENTE_ANTERIOR`,
`CARGA_MANUAL`, `OTRO`. La tabla de mapeo de los cinco vocabularios legacy es de F8.

`dirt_level`: `NORMAL`, `INTENSA`, `BARRO`, `ARENA`, `PELOS_DE_MASCOTA`, `TRABAJO_ESPECIAL` (los 6 de
`RECEPCION!B15` / `ADMIN_DIRT_LEVELS`; los 12 valores libres de `REGISTRO_DIARIO.O` se mapean en F8).

**Todos los enums están congelados en `backend/app/domain/enums.py`** (lo escribe T1), con el nombre de
su tipo nativo de Postgres. Los identificadores Python van sin `Ñ` (`PENDIENTE_SENA`); los valores
persistidos son los del legacy (`PENDIENTE_SEÑA`).

`void_reason` no cambia.

---

## 2. Máquinas de estado

Cada máquina es una **tabla de transiciones en `app/domain/`**, pura, sin I/O. Una transición que no
está en la tabla es `InvalidTransition` (whitelist, **[corregir]** R-O-018 usaba blacklist). Los
servicios toman la fila con `SELECT … FOR UPDATE` y exigen `from == status actual` (**[corregir]** C-18).

### 2.1 `booking_status`

| Estado | Bloquea el intervalo |
|---|---|
| `PENDIENTE_SEÑA` | sí (mientras `hold_expires_at > now`, vía el vencimiento perezoso de §1.3) |
| `PENDIENTE_COTIZACION` | sí (ídem) |
| `CONFIRMADO` | sí |
| `RECIBIDO` | sí — el auto llegó, hay job |
| `ATENDIDO` | no — el job terminó; libera como el legacy al `FINALIZADO` |
| `VENCIDO` | no, **no terminal** (R-T-030) |
| `CANCELADO_CLIENTE`, `AUSENTE_CON_AVISO_POSTERIOR`, `CANCELADO_OPERATIVO`, `CANCELADO_DEMORA`, `NO_ASISTIO` | no, terminales |

| Desde → a | Disparador | Guardas |
|---|---|---|
| ∅ → `PENDIENTE_SEÑA` | alta con seña requerida > 0 | `hold_expires_at` NN |
| ∅ → `PENDIENTE_COTIZACION` | alta de servicio `A_COTIZAR` (crea la cotización `PENDIENTE`) | `hold_expires_at` NN |
| ∅ → `CONFIRMADO` | alta sin seña, o carga del panel con seña ya cobrada | |
| `PENDIENTE_SEÑA` → `CONFIRMADO` | se registra una `SEÑA` | importe > 0 |
| `PENDIENTE_COTIZACION` → `CONFIRMADO` / `PENDIENTE_SEÑA` | se acepta la cotización | precio y duración de la cotización pasan al turno |
| `PENDIENTE_SEÑA`, `PENDIENTE_COTIZACION` → `VENCIDO` | vence el hold | `hold_expires_at <= now` |
| `VENCIDO` → `CONFIRMADO` | confirmación tardía | el `EXCLUDE` decide si el horario sigue libre |
| `PENDIENTE_SEÑA`, `PENDIENTE_COTIZACION`, `CONFIRMADO` → `CANCELADO_CLIENTE` / `AUSENTE_CON_AVISO_POSTERIOR` | el cliente cancela | anticipación ≥ 0 → `CANCELADO_CLIENTE`; < 0 → `AUSENTE_CON_AVISO_POSTERIOR`. **[corregir]** R-T-036: ya no se cancela desde `VENCIDO` ni estados cerrados. |
| `PENDIENTE_SEÑA`, `PENDIENTE_COTIZACION`, `CONFIRMADO` → `CANCELADO_OPERATIVO` | el negocio cancela | un solo caso de uso (**[corregir]** C-10); no desde `RECIBIDO`. |
| `CONFIRMADO` → `NO_ASISTIO` | el operador marca la inasistencia | **[diseñar]**: unifica `AUSENTE`/`NO_ASISTIO`, que ningún código escribía (C-04). |
| `CONFIRMADO`, `PENDIENTE_SEÑA` → `RECIBIDO` | se recibe el auto (job `JOB_RECEIVED`) | **[abierto]** si se recibe con la seña sin pagar; el esquema lo permite, la guarda es configurable en F6. |
| `RECIBIDO` → `ATENDIDO` | el job pasa a `FINALIZADO` | misma transacción |
| `RECIBIDO` → `CANCELADO_DEMORA` | el job pasa a `CANCELADO_DEMORA` | misma transacción |

### 2.2 `job_status` y `job_event_type`

`job_status`: `PRESENTE`, `EN_PROCESO`, `FINALIZADO`, `COBRADO`, `RETIRADO`, `CANCELADO_DEMORA`.
El estado de pago del legacy (`Estado_pago`) **no existe**: se deriva de `payments` (X9).

| Evento | from → to | Guardas | Efectos (misma transacción) |
|---|---|---|---|
| `JOB_RECEIVED` | ∅ → `PRESENTE` | con turno: estado recibible (§2.1) y **sin job previo** (si existe, se devuelve el existente); `A_COTIZAR` exige cotización `ACEPTADO`; estado inicial **siempre** `PRESENTE` (**[corregir]** R-O-001: el operador elegía cualquiera) | snapshots de precio y seña; `arrival_delay_min`; turno → `RECIBIDO`; señas del turno → `job_id` |
| `JOB_STARTED` | `PRESENTE` → `EN_PROCESO` | **no** exige inspección (D-007) | `started_at` |
| `JOB_FINISHED` | `EN_PROCESO` → `FINALIZADO` | separado del cobro (**[corregir]** R-O-026) | `finished_at`; turno → `ATENDIDO`; si el saldo ya es 0, encadena `JOB_SETTLED` |
| `JOB_SETTLED` | `FINALIZADO` → `COBRADO` | saldo derivado == 0 | `settled_at` |
| `JOB_PICKED_UP` | `COBRADO` → `RETIRADO` | no es obligatorio: quedarse en `COBRADO` **es** la señal "terminado sin retirar" (D-003) | `picked_up_at` |
| `JOB_CANCELLED_DELAY` | `PRESENTE` → `CANCELADO_DEMORA` | acción humana (D-001.2); `scheduled_at` NN (un walk-in no se cancela por demora); demora **recalculada con el `occurred_at` del evento** `> tolerancia` (parámetro, default 20 en F4) | turno → `CANCELADO_DEMORA`; emite `DEPOSIT_RETAINED`; `metadata` guarda `delay_min` y `tolerance_min` aplicadas |
| `DEPOSIT_RETAINED` | sin transición | solo junto a `JOB_CANCELLED_DELAY` | `metadata.amount_cents` = señas cobradas |
| `DEPOSIT_RETENTION_REVERSED` | sin transición | existe una retención sin reversar; **motivo obligatorio** (CHECK en la base) y actor | nunca un `UPDATE` (D-001.5) |
| `PAYMENT_RECORDED` / `PAYMENT_VOIDED` | sin transición | job no `CANCELADO_DEMORA` | un cobro que deja saldo 0 en `FINALIZADO` encadena `JOB_SETTLED` |
| `INSPECTION_RECORDED`, `PRICE_ADJUSTED` | sin transición | ajuste con motivo | |

Retención vigente = hay `DEPOSIT_RETAINED` y no hay `DEPOSIT_RETENTION_REVERSED` posterior (X9: no hay
columna escalar; **[corregir]** C-12).

**[corregir]**, no se reproduce: cancelar por demora sin guarda un `FINALIZADO` cobrado (R-O-016, C-7);
finalizar sin haber iniciado o refinalizar pisando el cobro (R-O-022); iniciar un turno cancelado
(R-O-018); alta no idempotente (R-O-010); escrituras parciales sin transacción (C-18).

### 2.3 `quote_status` — **[diseñar]**, a validar con el dueño

`PENDIENTE` → `COTIZADO` (exige precio y duración) → `ACEPTADO` | `RECHAZADO`.
`PENDIENTE`, `COTIZADO` → `CANCELADO` | `VENCIDO` (por `expires_at`).
Terminales: `ACEPTADO`, `RECHAZADO`, `CANCELADO`, `VENCIDO`. Aceptar con turno pasa precio y duración
al turno (§2.1) y el job hereda el precio acordado (**[corregir]** R-C-029: entraba como "Adicional").

### 2.4 `deposit_status` — resolución de la seña (D-005)

Abiertos: `SIN_PAGO` (terminal de hecho), `DEVOLUCION_PENDIENTE`, `EN_REVISION`,
`REPROGRAMACION_O_DEVOLUCION_PENDIENTE`. Cierres **[diseñar]**: `DEVUELTA`, `RETENIDA`, `REPROGRAMADA`.

| Clasificación | Con seña | Sin seña |
|---|---|---|
| `NORMAL` (≥ umbral, default 720) / `TARDIA` (0 ≤ x < umbral) | `DEVOLUCION_PENDIENTE` | `SIN_PAGO` |
| `POSTERIOR_AL_TURNO` (< 0) | `EN_REVISION` | `SIN_PAGO` |
| `OPERATIVA` | `REPROGRAMACION_O_DEVOLUCION_PENDIENTE` | `SIN_PAGO` |

Cerrar exige actor y motivo (CHECK); `DEVUELTA` exige el pago `DEVOLUCION`. `NORMAL` y `TARDIA` tienen
**el mismo efecto económico** (C-14): cualquier retención por tardía sería regla nueva y **[abierto]**.

---

## 3. Dominio puro (`app/domain/`)

Funciones sin I/O, 100% testeables, con los parámetros de negocio como argumentos:

| Módulo | Contenido |
|---|---|
| `money.py` | `parse_ars(text) -> int` (centavos, locale `es-AR`: `.` miles, `,` decimales; **rechaza** lo ambiguo en vez de devolver 0 — **[corregir]** R-O-029); `format_ars(cents)`; `apply_bps(cents, bps)` half-up. |
| `phone.py` | `normalize_phone_ar(raw) -> PhoneResult` a E.164 (10 dígitos → `+549…`; `549…` de 13; `0` inicial se saca; 12 con `54` sin `9` y prefijo `15` → **ambiguo, no se adivina**); `phones_equivalent(a, b)` (sufijo ≥ 8, R-C-006) — **solo para la migración**. |
| `plate.py` | `normalize_plate(raw)` (`upper`, solo `A-Z0-9`, centinelas `SIN000` → None); `classify_plate()` → `AR_1994`/`MERCOSUR`/`OTRO`. |
| `delay.py` | `arrival_delay_min(scheduled_at, arrived_at)` con signo, `floor`. |
| `booking_state.py`, `job_state.py`, `quote_state.py`, `deposit.py` | enums, tablas de transición de §2, `BLOCKING_BOOKING_STATUSES`, `classify_cancellation(anticipation_min, late_threshold_min)`, `deposit_status_for(classification, paid_cents)`. |

---

## 4. Servicios de aplicación (`app/application/services/`)

Toman la sesión, fijan el tenant del contexto (nunca de un argumento libre), usan `FOR UPDATE` y hacen
todo en **una** transacción.

- `JobService`: `receive` (con y sin turno, idempotente), `start`, `finish`, `record_payment`,
  `void_payment`, `settle` (interno), `pick_up`, `cancel_for_delay`, `reverse_retention`,
  `record_inspection`. Cada uno escribe su `job_events` y actualiza la caché `jobs.status` y los
  `*_at`. Un `idempotency_key` repetido devuelve el resultado anterior sin efectos.
- `BookingService`: `expire_holds(resource_id, now)`, `confirm_deposit`, `cancel_by_client`
  (clasifica y crea `cancellations`), `cancel_operational`, `mark_no_show`. **Crear** turnos y la
  disponibilidad son F5.
- `QuoteService`: `quote`, `accept`, `reject`, `cancel`, `expire`.
- `DepositResolutionService`: `resolve(cancellation, status, reason, refund_payment?)`.
- `CatalogService`: validación de coherencia modalidad ↔ precios (§1.1).
- `CustomerService.resolve_by_phone` / `VehicleService.resolve_by_plate`: upsert por clave normalizada.

---

## 5. Migración `0002_dominio`

- Nombre `YYYYMMDD_NNNN_descripcion.py`, docstring con el **porqué**, **idempotente** (A6: el
  `preDeployCommand` puede correr dos veces), additive-first. `down_revision = "0001_inicial"`.
- Verifica `btree_gist` y corta con: `"falta la extensión btree_gist: correr backend/scripts/create_roles.sh
  como superusuario (ver railway.toml)"` (X11).
- Enums con `_create_type` idempotente, valores **congelados en la migración**.
- Por cada tabla de tenant: `enable_rls` + grants. `job_events`: `GRANT SELECT, INSERT` solamente, y el
  trigger `job_events_append_only` (`BEFORE UPDATE OR DELETE … RAISE EXCEPTION`).
- `UNIQUE (tenant_id, id)` en los padres, incluido `users` (tabla de F2, cambio aditivo).
- `downgrade` completo: CI hace `downgrade base && upgrade head`.
- `create_roles.sh`: `CREATE EXTENSION IF NOT EXISTS btree_gist` como superusuario (idempotente).
  **Paso manual para el dueño:** re-correrlo en staging y producción antes del primer deploy de F3.

---

## 6. Construcción (T2) — un dueño por carpeta

| Agente | Dueño de | Arranca |
|---|---|---|
| **Dominio** | `app/domain/**` (salvo `roles.py`, `void.py`, `errors.py` y `enums.py`, que es de T1) y `app/tests/domain/**` | ya, en paralelo |
| **Esquema** | `app/persistence/models/**`, `app/persistence/migrations/versions/**`, `app/persistence/db/rls.py`, `backend/scripts/create_roles.sh`, `app/tests/persistence/**` (tests nuevos del esquema) | ya, en paralelo; importa los enums de `app/domain/enums.py` |
| **Servicios** | `app/persistence/repositories/**`, `app/application/services/**`, `app/tests/application/**` | cuando Esquema y Dominio entregan |

Los tests de aceptación de §8 que no son de un agente los escribe **T1** (este contrato) antes de T2:
`app/tests/meta/test_fks_compuestas.py`, `app/tests/persistence/test_indices_de_fks_pg.py`,
`app/tests/security/test_aislamiento_dominio_pg.py` (genérico; T3 escribe `_poblar_dominio.py`, que
inserta una fila válida por tabla), más `app/domain/enums.py` y el ajuste de `app/tests/conftest_pg.py`
(limpieza desde la metadata y `btree_gist` al recrear el esquema).

**Postgres local:** cada agente usa su propia base en el contenedor de tests del puerto 5433
(`carwash_dominio`, `carwash_esquema`, `carwash_servicios`, `carwash_t3`), porque el fixture recrea el
esquema `public` entero: `PG_TEST_URL=postgresql://carwash:carwash@localhost:5433/<base> uv run pytest
-m postgres -n 0`.

---

## 7. PII

Se suman al scrubbing (Sentry y logs): `customers.name/phone_e164/phone_raw/email/legacy_ids`,
`vehicles.plate/plate_normalized/legacy_id`, y todo `notes`. **La patente es el PII fuerte**
(`_PATENTE_VALUE_RE`). Los `legacy_id` de clientes, vehículos y cotizaciones **llevan teléfono o patente
adentro**: se tratan como PII.

---

## 8. Aceptación (checkpoint de F3)

| # | Comprobación | Dónde |
|---|---|---|
| B1 | `make check` y `make test-cov` verdes (cobertura ≥ 80%, sin bajar el piso). | CI |
| B2 | `alembic upgrade head`, `alembic check`, `downgrade base`, `upgrade head` limpios contra Postgres con `carwash_owner`. | CI |
| B3 | Re-aplicar 0002 con el stamp atrasado no falla (A6 extendido). | `test_migraciones_idempotentes_pg.py` |
| B4 | Toda tabla de tenant: RLS forzado, `USING` = `WITH CHECK` (los tests de F2 lo cubren solos, por metadata). | `test_rls_politicas_pg.py` |
| B5 | Toda FK entre tablas de tenant es compuesta con `tenant_id`. | `test_fks_compuestas.py` |
| B6 | Toda FK tiene índice que la cubre. | `test_indices_de_fks_pg.py` |
| B7 | **Aislamiento, tabla por tabla**, como `carwash_app`: con contexto B, las filas de A no se leen, no se actualizan (0 filas), no se insertan con `tenant_id` de A (WITH CHECK), y una fila de B **no puede apuntar a un padre de A** (FK compuesta). Parametrizado sobre `Base.metadata`: una tabla nueva sin cobertura hace fallar el test. | `test_aislamiento_dominio_pg.py` |
| B8 | Repositorios: `get_by_id` de un id ajeno devuelve `None` (→ 404 en F4+), `list` y su `total` no cuentan filas ajenas. | `app/tests/application/` |
| B9 | Unicidad por tenant: la misma patente, teléfono, código de turno y `idempotency_key` conviven en A y B, y chocan dentro de A. | ídem |
| B10 | `EXCLUDE`: dos turnos solapados en el mismo puesto chocan; en puestos distintos no; tocarse en el borde no; uno `CANCELADO_*`/`VENCIDO`/`ATENDIDO` libera; el vencimiento perezoso deja reservar sobre un hold vencido. | `app/tests/persistence/` (pg) |
| B11 | `job_events`: `UPDATE` y `DELETE` fallan como `carwash_app` **y** como dueño; un `idempotency_key` repetido no duplica; la reversión sin motivo falla en la base. | ídem |
| B12 | Máquinas de estado: toda transición de §2 que está en la tabla pasa; **toda** combinación que no está, falla (test exhaustivo sobre el producto cartesiano). | `app/tests/domain/` |
| B13 | Casos corregidos del legacy, uno por test: `"20.000"` → 2.000.000 centavos; `"abc"` rechazado; cancelar por demora un `FINALIZADO` falla; finalizar sin iniciar falla; segundo cobro suma; walk-in no se cancela por demora; recibir dos veces el mismo turno devuelve el mismo job. | `app/tests/domain/`, `app/tests/application/` |
| B14 | **El test del checkpoint:** con datos en todas las tablas de A, el lavadero B no ve **ninguna** fila de A en **ningún** recurso. | `test_aislamiento_dominio_pg.py` |

**Revisión (T3/T3b):** una ronda de tester de aislamiento + revisor adversarial, `/code-review` sobre el
diff de la fase, y **una** verificación después de los arreglos. Lo que aparezca después se difiere con
motivo escrito, salvo pérdida de datos o fuga entre lavaderos.

---

## 9. Preguntas abiertas para el dueño

El esquema **no las cierra**: quedan nullable o como guarda configurable, así cualquier respuesta entra
sin migración destructiva.

1. ¿El alta rápida exige cliente (nombre y WhatsApp) o alcanza con la patente? (`jobs.customer_id` NULL)
2. ¿Dos clientes pueden compartir WhatsApp (familia)? (hoy único)
3. ¿Se puede recibir un turno con la seña sin pagar? (R-O-010)
4. ¿Se permite "retirado con deuda" (`FINALIZADO → RETIRADO` con saldo)? (R-O-026 vs D-003) — hoy no.
5. Reversar la retención por demora: ¿revierte la cancelación? ¿adónde va la plata (devolución, crédito)?
6. Cotizaciones: ¿un `A_COTIZAR` puede tener precio de referencia? ¿la agenda interna con precio a mano
   es una cotización implícita o un override?
7. ¿`Lavado completo + lavado de motor` es servicio o adicional? ¿`Otro` es un tamaño?
8. Tasas reales de comisión de débito y crédito del Point.
9. ¿Existen las ventanas manuales de lunes a jueves (`DISPONIBILIDAD_SEMANAL` vacía)?
10. Apertura y cierre de caja: ¿hacen falta?

---

## Adenda de T2 (2026-09-18) — aclaraciones, no cambios de alcance

Huecos que el agente Dominio encontró al implementar §2. Se cierran del lado conservador; ninguno
cambia una tabla.

| # | Hueco | Resolución |
|---|---|---|
| A1 | Anular un cobro de un job `COBRADO`/`RETIRADO` puede dejar saldo > 0, y no hay transición de vuelta: `jobs.status` mentiría. | `JobService.void_payment` lo **rechaza** si el job está en `COBRADO`/`RETIRADO` y el saldo resultante sería > 0 (anular un cobro duplicado, que deja saldo 0, sí se puede). Reabrir un cobro es la pregunta 11. |
| A2 | §2.4 dejaba cerrar cualquier caso con `RETENIDA`, pero C-14 dice que retener una cancelación a tiempo o tardía es regla nueva. | `RETENIDA` **solo desde `EN_REVISION`** (cancelación posterior al turno). `DEVUELTA` y `REPROGRAMADA` desde cualquier caso abierto. Tabla `DEPOSIT_RESOLUTIONS` en `app/domain/deposit.py`. |
| A3 | `PRICE_ADJUSTED` no decía desde qué estados. | `PRESENTE`, `EN_PROCESO`, `FINALIZADO`: un ajuste después de cobrar tiene el mismo problema que A1. |
| A4 | El contrato nombra la excepción `InvalidTransition`; ruff N818 pide sufijo `Error`. | Se mantiene el nombre del contrato con `noqa` y motivo. |
| A5 | `test_modelo_tenant.py` (F2) exigía una sola FK sobre `tenant_id`; X4 agrega las compuestas. | El test verifica una sola FK **a `tenants`** y que las demás sean compuestas. |

| A6 | **Bug del contrato:** `length(btrim(metadata->>'reason')) > 0` da NULL si falta `reason`, y un CHECK con NULL **pasa**: la reversión sin motivo entraba. Igual en `cancellations.resolution_reason`. | `length(btrim(coalesce(…, ''))) > 0`. Lo encontró el agente Esquema; hay test. |
| A7 | X8 le quita `UPDATE` a `carwash_app` sobre `job_events`, y el test genérico de T1 hacía un `UPDATE` esperando 0 filas. | Para las tablas append-only el test exige `permission denied` en `UPDATE` y `DELETE`, aun sobre filas propias. |
| A8 | "Vivo" en los únicos parciales. | Siempre suma `voided_at IS NULL`, salvo `cancellations(booking_id)` e `idempotency_key`: anular no libera la clave. |
| A9 | `seed_staging.py` y `test_aislamiento_tenants_pg.py` (F2) exigen datos en **toda** tabla de tenant y no tenían dueño en §6. | `seed_staging.py` + su test: agente **Servicios** (siembra con los servicios, datos sintéticos). El poblador por tabla y el test de F2: **Tester-aislamiento** de T3. |
| A10 | Un agente copió un teléfono y una patente **reales** de `docs/spec/` en un test (no llegó a publicarse: se reescribió el commit). | Hook de pre-commit `pii-del-spec` (`scripts/check_pii_del_spec.py`): corta si un teléfono o patente del spec o del legacy aparece en un archivo versionado. Todo dato de test es sintético. |

**Pregunta 11 para el dueño:** si se anula un cobro después de cerrar el job (cobro mal cargado,
contracargo), ¿se reabre el job (`COBRADO → FINALIZADO`) o se registra aparte?

## Adenda de T3 (2026-09-18) — ronda única de arreglos

Hallazgos del revisor adversarial y de `/code-review` sobre el diff de la fase. Ninguno crítico ni de
fuga entre lavaderos. Todos resueltos con un test que falló primero; ninguno quedó diferido salvo la
pregunta 12.

| # | Regla |
|---|---|
| A11 | Una `DEVOLUCION` no supera lo pagado neto vivo del job, y en `COBRADO`/`RETIRADO` no puede dejar saldo > 0 (A1 extendida a `record_payment`). |
| A12 | Un `SALDO` no supera el saldo pendiente: un job no se sobrepaga por cobro. Un saldo a favor solo nace de un ajuste de precio. |
| A13 | `cancel_for_delay` exige que los `SALDO` vivos menos las `DEVOLUCION` vivas den 0: la plata no queda atrapada en `CANCELADO_DEMORA`. |
| A14 | Una cotización se usa en **un** job vivo (`ux_jobs_tenant_id_quote_id`). Un walk-in solo acepta cotizaciones sin turno; un turno, solo la suya. |
| A15 | `confirm_late` exige precio (un turno a cotizar vencido no se confirma tarde); exige la seña si `deposit_required_cents > 0` y la rechaza si es 0. |
| A16 | `record_deposit_after_slot_lost`: la seña que llega después de perder el horario se registra en el turno `VENCIDO` y abre una cancelación `OPERATIVA` con `REPROGRAMACION_O_DEVOLUCION_PENDIENTE`. Una por turno. |
| A17 | Una cotización con `expires_at <= now` no se acepta (mismo comparador que los holds). |
| A18 | `cancel_by_client(now=…)` usa la hora **del server**: la clasificación decide si la seña se puede retener. |
| A19 | La recepción de un turno decide el camino de cotización por el **snapshot** del turno, no por el `pricing_mode` vivo del servicio. |
| A20 | Reenviar el mismo cierre de una seña no tiene efectos (cola offline). |
| A21 | La migración 0002 corre con `lock_timeout = 5s`: toma `ACCESS EXCLUSIVE` sobre `tenants` y `users`, y no puede colgar los logins. |
| A22 | Vencer holds usa `FOR UPDATE SKIP LOCKED` (se reprodujo un deadlock 40P01 entre dos holds vencidos del mismo puesto). |
| A23 | El seed de staging usa teléfonos `+549110000xxxx` y patentes `ZZ0xxZZ`: pasan los CHECK y no pueden ser de una persona real. |

**Pregunta 12 para el dueño:** una devolución con tarjeta, ¿recupera la comisión del medio de pago?
Hoy se calcula comisión también sobre la devolución (decisión de F7).
