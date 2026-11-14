# Contrato — Fase 5: turnero público, disponibilidad, cancelación y lista de espera

**Fecha:** 2026-11-11 · **Tiempo:** T1. Se congela al entrar a T2 de cada PR; cambiarlo después es reabrir
la fase con el dueño.

**Objetivo:** `https://<dominio>/<slug>` funcionando. El cliente reserva desde el celular sin instalar nada
ni crear cuenta; el motor ofrece solo horarios reales (horario del negocio, reglas del servicio,
bloqueos, puestos ocupados); el dueño confirma señas, resuelve la lista de espera y ve los turnos que
entran.

**Fuentes:** `docs/ROADMAP.md` (Fase 5), `docs/adr/FASE-3-CONTRATO.md` (esquema de `bookings`,
`cancellations`, `schedule_blocks`, D-005), `docs/adr/FASE-4-CONTRATO.md` (seña, `tenant_settings`, slug,
D4-13/15/17/18), `docs/spec/05-decisiones-producto.md` (D-014, D-015) y
`docs/spec/02-reglas-relevamiento-2026-11.md` (R-N2, R-N4, R-N5; local-only). Decisiones del dueño del
2026-11-11 (abajo).

---

## 0. Decisiones del dueño (2026-11-11)

| # | Decisión |
|---|---|
| D5-1 | **Seña sin pasarela.** Al reservar, el cliente ve las instrucciones de pago del negocio (alias/CBU, texto libre) y un botón a WhatsApp para mandar el comprobante. El turno queda `PENDIENTE_SEÑA` retenido `hold_min` (30 por defecto, editable) y el dueño lo confirma desde la bandeja. Si nadie lo confirma a tiempo, vence y el horario se libera; una seña que llega tarde se confirma igual si el horario sigue libre (`confirm_late`). |
| D5-2 | **Horizonte de reserva: 14 días**, editable por negocio. |
| D5-3 | **El cliente elige solo el tamaño** del vehículo (Auto, SUV, Pickup… los del negocio), no marca y modelo. La patente es opcional. |
| D5-4 | **Máximo 2 turnos activos a futuro por cliente** (mismo WhatsApp), además del límite por IP. Un tercero lo carga el negocio. |
| D5-5 | **Servicios a cotizar:** el turnero los muestra con "desde $X" y un botón "Consultar por WhatsApp"; **no** se reservan desde la web. Las pantallas de cotización sobre el `QuoteService` existente quedan para una fase posterior; el dueño los agenda a mano en F6. Corrige el ROADMAP, que traía D-006 a F5. |

Decisiones por defecto (las fija este contrato; el dueño las puede cambiar en el checkpoint):

| # | Decisión |
|---|---|
| D5-6 | **El puesto lo asigna el sistema**: el primer puesto libre por `sort_order`; si el `EXCLUDE` choca, prueba el siguiente. El cliente no elige puesto. **El orden es editable** por el dueño (orden de los puestos en Configuración → Puestos), y el puesto de cada turno también (D5-10). |
| D5-7 | **Grilla de presentación de 30 min** (`slot_step_min`, **editable** en "Seña y reservas"). Es UI sobre un modelo continuo: los inicios se ofrecen cada `slot_step_min` desde el comienzo de cada ventana. |
| D5-10 | **La agenda es editable (2026-11-11, dueño):** desde la bandeja, el negocio **reprograma** un turno (otro día/hora) y/o lo **cambia de puesto**. El `EXCLUDE` decide si entra (409 `SLOT_TAKEN` si no). El dueño puede salir de las reglas del servicio y del horario del negocio (es una decisión suya, la pantalla avisa), pero no de un bloqueo. |
| D5-8 | **Cancelación web = código del turno + WhatsApp con el que se reservó** (como hoy en Sola). Sin los dos, la respuesta es la misma que para un turno inexistente. |
| D5-9 | **Lista de espera (D-014):** con 2 o más incidencias en 90 días, el pedido queda `PENDIENTE_REVISION`, sin seña y sin ocupar el horario; el dueño lo aprueba (pasa al flujo normal **en la misma fila**, y el `EXCLUDE` decide si el horario sigue libre) o lo rechaza. |

## 1. Decisiones transversales

| # | Decisión | Por qué |
|---|---|---|
| Z1 | **Cuatro PRs:** 5.0 este contrato → 5.1 motor y configuración de agenda → 5.2 turnero público ‖ 5.3 bandeja del dueño. 5.2 y 5.3 en worktrees paralelos con base `main` después de mergear 5.1. | Mismo esquema que F4: un PR por tema; lo paralelo va en carpetas disjuntas. |
| Z2 | **Una sola migración (`0010_agenda_f5`) en 5.1**, con todo el esquema de la fase, incluida la función de búsqueda por slug. 5.2 y 5.3 no migran. | Los PRs paralelos no chocan en la numeración (Y2 de F4) ni dejan dos heads. |
| Z3 | **Permisos: se reusan los existentes** (`domain/permissions.py`): `AGENDA_VER` (ver bandeja y turnos), `TURNOS_GESTIONAR` (bloqueos, aperturas, aprobar/rechazar, cancelar, no asistió, marcar incidencia), `COBROS_REGISTRAR` (confirmar seña: registra un pago), `CATALOGO_EDITAR` (reglas y modo del servicio). | Los perfiles Encargado/Cajero/Lavador no cambian; no hay migración de perfiles. |
| Z4 | **El motor de disponibilidad es dominio puro** (`domain/availability.py`): recibe datos ya leídos y `now`, devuelve inicios. Sin I/O, sin reloj propio. | Se testea con reloj fijo y sin base, como el resto del dominio (F3 pasa `now` explícito). |
| Z5 | **La garantía contra el doble turno sigue en la base** (`xc_bookings_sin_solapamiento`). El motor decide qué se **ofrece**; el `EXCLUDE` decide qué **entra**. Nunca un `SELECT` previo como garantía. | ROADMAP F5. `_savepoint.py` ya traduce el choque a `SlotTakenError` (no hay que extenderlo: el ROADMAP lo daba como pendiente y ya estaba hecho en F3). |
| Z6 | **El `POST` público valida el horario contra el motor** antes de crear: el `EXCLUDE` no ve horario del negocio, reglas del servicio ni bloqueos. | Sin esto, un cliente con la API a mano reserva un domingo a las 3 AM. |
| Z7 | **Las rutas públicas viven en `/v1/public/{slug}/…`** y resuelven el tenant con una función `SECURITY DEFINER` (`public_tenant_by_slug`), como `auth_lookup_user`. Con el `tenant_id`, los servicios abren el contexto RLS con `_enter()` como siempre. | `tenants` tiene RLS por `id`: sin sesión no se ve ningún negocio. |
| Z8 | **Un slug inexistente y uno de un negocio con el alta sin terminar dan el mismo 404**, idéntico byte a byte. | No se enumeran negocios ni se expone uno a medio configurar. |
| Z9 | **Zona horaria:** las franjas y reglas se interpretan en `tenants.timezone` con `zoneinfo`; los instantes se guardan y viajan en UTC con offset (ISO 8601). El front muestra en la zona del negocio, no en la del navegador. | Un cliente que reserva desde otra provincia o del exterior ve la hora del lavadero. |
| Z10 | **Nada de nombres de servicio ni de vehículo en el código** (D-015). "El camión vie–sáb" es una regla genérica cargada en datos. | CLAUDE.md: ningún servicio queda fijo en el código. |

---

## 2. Permisos

Sin permisos nuevos (Z3). Matriz:

| Acción | Permiso |
|---|---|
| Ver bandeja, próximos turnos, disponibilidad interna | `AGENDA_VER` |
| Crear/anular bloqueos y aperturas | `TURNOS_GESTIONAR` |
| Aprobar/rechazar lista de espera, cancelación operativa, no asistió, marcar/anular incidencia | `TURNOS_GESTIONAR` |
| Confirmar seña (también la tardía) | `COBROS_REGISTRAR` |
| Resolver la seña de una cancelación (`DepositResolutionService`, que hoy no tiene endpoint) | `COBROS_REGISTRAR` |
| Modo de reserva y reglas horarias de un servicio | `CATALOGO_EDITAR` |
| Horizonte, paso e instrucciones de seña | `OWNER` (son `tenant_settings`, como el resto de "Seña y reservas") |

---

## 3. Tablas (migración `0010_agenda_f5`, PR 5.1)

Idempotente (A6). Rellenos y guardas con `NO FORCE`/`FORCE ROW LEVEL SECURITY` (el dueño también pasa por
RLS). Downgrade con corte explícito si se perderían datos (patrón de `0007`).

### 3.1 `services` — modo de reserva

| Columna | Tipo | Regla |
|---|---|---|
| `booking_mode` | enum `service_booking_mode` (`TURNERO`, `SOLO_A_MANO`) NOT NULL default `TURNERO` | `SOLO_A_MANO` no se ofrece ni se acepta en el turnero; lo agenda el negocio (F6). |

Un servicio `A_COTIZAR` se muestra en el turnero pero no se reserva (D5-5), independientemente de
`booking_mode`.

### 3.2 `service_schedule_rules` — reglas horarias por servicio (D-015, R-N2)

| Columna | Tipo | Regla |
|---|---|---|
| `service_id` | FK compuesta a `services` | |
| `weekday` | smallint 1–7 (ISO, 1 = lunes) | |
| `window_start` | time | desde cuándo puede **arrancar** |
| `last_start` | time | último inicio permitido; `last_start >= window_start` |

- Receta RLS de siempre, `tenant_index` en `service_id`, anulable (`voided`).
- **Sin filas vivas = el servicio puede arrancar en cualquier momento del horario del negocio** (siempre
  que termine dentro de la franja).
- **Con filas:** solo arranca en `[window_start, last_start]` de su día; además tiene que **terminar dentro
  de la franja del negocio** que lo contiene (o de una apertura especial).
- Varias filas por día = varias ventanas (mañana y tarde).
- Se editan **como lista completa** en el `PATCH` del servicio (reemplazo: se anulan las vivas y se crean
  las nuevas en la misma transacción).

### 3.3 `schedule_openings` — disponibilidad especial (R-N4, `DISPONIBILIDAD_SEMANAL`)

| Columna | Tipo | Regla |
|---|---|---|
| `resource_id` | uuid NULL | NULL = todos los puestos |
| `starts_at`, `ends_at` | timestamptz | `ends_at > starts_at` |
| `reason` | text ≤ 160 | |
| `created_by_user_id` | FK `users` | |

Espejo de `schedule_blocks`; anulable con `voided`. Suma una ventana abierta fuera (o además) del horario
habitual. Las reglas del servicio **no** se aplican dentro de una apertura especial salvo el día de la
semana (la apertura es una decisión puntual del dueño). Un bloqueo **gana** sobre una apertura.

### 3.4 `tenant_settings` — tres columnas

| Columna | Tipo | Default | Regla |
|---|---|---|---|
| `booking_horizon_days` | smallint | 14 | 1–90 |
| `slot_step_min` | smallint | 30 | 5–120 |
| `deposit_instructions` | text NULL | NULL | ≤ 500 caracteres; alias/CBU/titular |

No son requisito del alta (no se tocan los requisitos de §11.9 de F4). Sin instrucciones, la página pública
dice "Coordiná el pago de la seña por WhatsApp".

### 3.5 `BookingStatus` — dos valores

| Valor | Ocupa puesto | Hold |
|---|---|---|
| `PENDIENTE_REVISION` | no | no |
| `RECHAZADO` | no | no |

`ALTER TYPE … ADD VALUE` en su propio paso (Postgres no deja usar el valor nuevo en la misma transacción).
No entran a `BLOCKING_BOOKING_STATUSES` ni al `WHERE` del `EXCLUDE`, que no se recrea.

Transiciones nuevas (`domain/booking_state.py`):

| Acción | Desde | Hacia |
|---|---|---|
| `CREATE_FOR_REVIEW` | — | `PENDIENTE_REVISION` |
| `APPROVE_REVIEW_WITH_DEPOSIT` | `PENDIENTE_REVISION` | `PENDIENTE_SEÑA` (hold nuevo desde la aprobación) |
| `APPROVE_REVIEW_CONFIRMED` | `PENDIENTE_REVISION` | `CONFIRMADO` |
| `REJECT_REVIEW` | `PENDIENTE_REVISION` | `RECHAZADO` |
| `EXPIRE_REVIEW` | `PENDIENTE_REVISION` | `VENCIDO` (pasó `start_at` sin decisión) |
| `CANCEL_BY_CLIENT` | suma `PENDIENTE_REVISION` a `_ACTIVE` | `CANCELADO_CLIENTE` |

Un `PENDIENTE_REVISION` guarda precio y seña calculados como cualquier turno (snapshot de ítems) pero
`deposit_required_cents` se **recalcula al aprobar** con la configuración vigente.

### 3.6 `customer_incident_marks` — marca manual (D-014)

| Columna | Tipo | Regla |
|---|---|---|
| `customer_id` | FK compuesta | |
| `booking_id` | FK compuesta NULL | el turno desde el que se marcó, si hay |
| `reason` | text 1–160 | obligatorio |
| `created_by_user_id` | FK `users` | |

Anulable con `voided` (se "desmarca" anulando, nunca borrando).

### 3.7 Función `public_tenant_by_slug(p_slug text) RETURNS uuid`

`SECURITY DEFINER`, `SET search_path = public, pg_temp`, dueño `carwash_owner`, `EXECUTE` solo para
`carwash_app`. Devuelve el `id` del tenant **vivo** con ese slug **y** `tenant_settings.onboarding_completed_at
IS NOT NULL`; si no, NULL. Mismo patrón y tests que `auth_lookup_user` (`persistence/db/rls.py`).

### 3.8 Slugs reservados

`RESERVED_SLUGS` (`domain/slug.py`) suma `catalogo`, `register` e `icons` (hoy son rutas o carpetas
públicas reales del front y no estaban). Test nuevo del front: cada carpeta de primer nivel de `app/` (dentro
de los grupos) y de `public/` está en la lista (espejo de la lista del backend, verificado por un test del
backend que lee el archivo del front). La migración **corta y avisa** si un tenant vivo tiene un slug
que pasó a reservado.

---

## 4. Endpoints (prefijo `/v1`)

### 4.1 Con sesión (5.1 y 5.3)

| PR | Método y ruta | Quién |
|---|---|---|
| 5.1 | `GET/POST /agenda/blocks`, `DELETE /agenda/blocks/{id}` (anula) | `AGENDA_VER` / `TURNOS_GESTIONAR` |
| 5.1 | `GET/POST /agenda/openings`, `DELETE /agenda/openings/{id}` | `AGENDA_VER` / `TURNOS_GESTIONAR` |
| 5.1 | `GET /agenda/availability?date&vehicle_size_id&service_ids` | `AGENDA_VER` |
| 5.1 | `PATCH /services/{id}` suma `booking_mode` y `schedule_rules[]` | `CATALOGO_EDITAR` |
| 5.1 | `PATCH /settings` suma `booking_horizon_days`, `slot_step_min`, `deposit_instructions` | `OWNER` |
| 5.1 | `DELETE /resources/{id}` con turnos futuros activos → 409 `IN_USE` (diferido de F4 §11.4) | `CATALOGO_EDITAR` (sin cambios) |
| 5.3 | `GET /bookings?status&from&to&page` (paginación ADR-0006) | `AGENDA_VER` |
| 5.3 | `GET /bookings/counts` (pendientes de seña, en revisión, señas a resolver) | `AGENDA_VER` |
| 5.3 | `GET /bookings/{id}` | `AGENDA_VER` |
| 5.3 | `POST /bookings/{id}/confirm-deposit` (`payment_method_id`, `amount_cents`, `Idempotency-Key`) | `COBROS_REGISTRAR` |
| 5.3 | `POST /bookings/{id}/approve`, `POST /bookings/{id}/reject` (`reason`) | `TURNOS_GESTIONAR` |
| 5.3 | `POST /bookings/{id}/cancel` (operativa, `reason`), `POST /bookings/{id}/no-show` | `TURNOS_GESTIONAR` |
| 5.3 | `POST /bookings/{id}/incident-marks`, `DELETE /incident-marks/{id}` | `TURNOS_GESTIONAR` |
| 5.3 | `POST /bookings/{id}/reschedule` (`start_at` y/o `resource_id`) — D5-10 | `TURNOS_GESTIONAR` |
| 5.3 | `POST /cancellations/{id}/resolve` (`DEVUELTA`/`RETENIDA`/`REPROGRAMADA`, `reason`) | `COBROS_REGISTRAR` |

**Reprogramar (D5-10):** permitido en `PENDIENTE_SEÑA`, `CONFIRMADO` y `PENDIENTE_REVISION`. `end_at = start_at +
duration_min` (snapshot, no el catálogo actual). No cambia estado, seña ni `hold_expires_at`. Corre dentro de
`guarded_savepoint` (choque → 409 `SLOT_TAKEN`); un bloqueo que lo pisa → 409 `BLOCKED`. Fuera de reglas u horario
del negocio se acepta y la respuesta lo marca (`outside_rules: true`) para que la pantalla lo avise. El
historial de cambios de la agenda (quién movió qué) es de F6.

`confirm-deposit` usa `confirm_deposit`; si el turno ya está `VENCIDO`, `confirm_late(payment=…)`
(el `EXCLUDE` decide; si el horario ya no está: 409 `SLOT_TAKEN` y el caso queda como hoy, A16 de F3).

### 4.2 Públicos (5.2), sin sesión

| Método y ruta | Límite (slowapi, por IP) | Devuelve |
|---|---|---|
| `GET /public/{slug}` | 60/min | nombre, branding (paleta, tipografía, logo), `welcome_text`, `address`, `whatsapp_e164`, `cancellation_policy`, `deposit_instructions`, `deposit_enabled`, horizonte, zona horaria |
| `GET /public/{slug}/catalog` | 60/min | tamaños; servicios **vivos** no `SOLO_A_MANO` con precio y duración por tamaño, `pricing_mode` y % de seña efectivo |
| `GET /public/{slug}/availability?vehicle_size_id&service_ids&date` | 60/min | inicios libres del día (solo horas, nunca qué puesto ni quién ocupa) |
| `POST /public/{slug}/bookings` | 5/min y 20/día | código, estado (`PENDIENTE_SEÑA`, `CONFIRMADO` o `PENDIENTE_REVISION`), inicio, fin, total, seña, `hold_expires_at` |
| `POST /public/{slug}/bookings/lookup` (`code`, `phone`) | 10/min | el turno (mismo shape que el POST) + si se puede cancelar |
| `POST /public/{slug}/bookings/cancel` (`code`, `phone`, `reason` ≤160) | 5/min | estado final + clasificación (`NORMAL`/`TARDIA`/`POSTERIOR_AL_TURNO`) + caso de seña |

Todas en la allowlist de `tests/meta/test_rutas_con_sesion_exigen_usuario.py`.

**Flujo del `POST /bookings`** (todo en una transacción):

1. Resolver el tenant (Z7/Z8). `Idempotency-Key` obligatorio (`claim_idempotency_key`, acción
   `public_booking`): un reenvío devuelve el mismo turno.
2. Validar servicios: vivos, del tenant, no `SOLO_A_MANO`, no `A_COTIZAR` (422 `SERVICE_NOT_BOOKABLE`), con
   precio para el tamaño.
3. Validar el horario contra el motor (Z6): si `start_at` no está entre los inicios libres → 409
   `SLOT_TAKEN` (mismo código que el choque del `EXCLUDE`: para el cliente es lo mismo).
4. `CustomerService.resolve_by_phone` con `normalize_phone_ar`; teléfono inválido o ambiguo → 422
   `INVALID_PHONE`. **Si el cliente ya existe no se le pisa el nombre** (cualquiera puede tipear un
   WhatsApp ajeno): el nombre tipeado va a `notes` del turno ("Nombre en la web: …"). Cliente nuevo:
   `acquisition_channel = TURNERO_WEB`.
5. Límite D5-4: turnos del cliente en `PENDIENTE_SEÑA` (hold vigente), `PENDIENTE_REVISION` o
   `CONFIRMADO` con `start_at > now` ≥ 2 → 409 `TOO_MANY_ACTIVE_BOOKINGS`.
6. `count_incidents(customer, now) >= 2` → crear `PENDIENTE_REVISION` (sin puesto ocupado, sin seña
   pedida, sin hold). Si no → `BookingService.create` probando los puestos libres en orden (D5-6); si
   todos chocan → 409 `SLOT_TAKEN`.
7. `code` = `TUR-` + 6 caracteres de `23456789ABCDEFGHJKMNPQRSTUVWXYZ` (sin 0/O/1/I/L), reintento si
   choca la unicidad viva (máx. 5).
8. `source = channel = TURNERO_WEB`; `terms_accepted_at = now` y `terms_version` = primeros 12 hex del
   SHA-256 de `cancellation_policy` vigente (`accept_policy: true` obligatorio en el body; TyC versionados
   son v2, D-017).

`PENDIENTE_REVISION` necesita un `resource_id` (columna NOT NULL): se guarda el primer puesto libre al
momento del pedido; al aprobar se vuelve a elegir (D5-6).

**Cancelar:** `lookup` del turno por `code` y `customer.phone_e164 == normalize(phone)`; si no coincide o no
existe → 404 `NOT_FOUND` idéntico. Después, `BookingService.cancel_by_client` sin cambios (clasificación
D-005, caso de seña, idempotente).

---

## 5. Motor de disponibilidad (`domain/availability.py`, PR 5.1)

```
free_starts(
    day: date, tz: ZoneInfo, now: datetime,
    business_hours: [(weekday, opens_at, closes_at)],
    rules: [ [(weekday, window_start, last_start)] por servicio ]   # lista vacía = sin regla
    openings: [(resource_id | None, starts_at, ends_at)],
    blocks:   [(resource_id | None, starts_at, ends_at)],
    occupied: {resource_id: [(start_at, end_at)]},                  # ya filtrado: holds vigentes
    resources: [resource_id en sort_order],
    duration_min: int, min_advance_min: int, horizon_days: int, step_min: int,
) -> [(start_at, [resource_id libres])]
```

Algoritmo:

1. **Fuera de rango:** `day` anterior a hoy (en `tz`) o posterior a `hoy + horizon_days` → `[]`.
2. **Franjas del día:** las de `business_hours` de su `weekday`, como intervalos `[opens, closes)` en `tz`
   convertidos a UTC; más las `openings` que tocan el día (recortadas al día).
3. **Candidatos:** por cada franja, inicios cada `step_min` desde su apertura, con
   `inicio + duration_min <= cierre de esa franja`.
4. **Reglas del servicio:** con varios servicios, el inicio tiene que cumplir **todas** las reglas no
   vacías (intersección). Un inicio cumple una regla si existe una fila de su `weekday` con
   `window_start <= hora_local(inicio) <= last_start`. Dentro de una apertura especial solo se mira que
   haya regla para ese `weekday` (§3.3).
5. **Anticipación:** se descarta el inicio si `anticipation_min(inicio, now) < min_advance_min` (el mismo
   helper que `BookingService.create`; justo en el borde, se ofrece).
6. **Por puesto:** el intervalo `[inicio, inicio + duración)` no puede solapar (`&&` semiabierto, igual
   que el `EXCLUDE`) ningún `occupied[r]`, ningún bloqueo de `r` ni ningún bloqueo global.
7. Se devuelven los inicios con al menos un puesto libre, ordenados.

**Lo que ocupa** (lo arma el repositorio, no el motor): turnos vivos en `CONFIRMADO`, `RECIBIDO` y
`PENDIENTE_SEÑA` **con `hold_expires_at > now`** (mismo comparador que `hold_expired`). Un hold vencido
**no** ocupa aunque `expire_holds` todavía no lo haya pasado a `VENCIDO`.

**Casos borde** (cada uno es un test con reloj fijo en `tests/domain/test_availability.py`):

| # | Caso | Esperado |
|---|---|---|
| E1 | Turno de 120 min en franja 09–13 | último inicio 11:00 |
| E2 | Franjas 09–13 y 14–18, servicio de 240 min | solo 09:00 y 14:00 (no cruza el corte) |
| E3 | 1 puesto ocupado 10–11, servicio de 60 min | 10:00 no; 09:00 y 11:00 sí (semiabierto) |
| E4 | 2 puestos, uno ocupado 10–11 | 10:00 sí, con el puesto libre |
| E5 | Regla vie–sáb, consulta un martes | `[]` |
| E6 | Regla mañana 09–11 y tarde 14–16, servicio de 60 min | inicios 09:00–11:00 y 14:00–16:00, nada 12:00 |
| E7 | Dos servicios con reglas distintas | intersección; disjuntas → `[]` |
| E8 | Bloqueo global de día completo | `[]` |
| E9 | Bloqueo de un puesto de 2 | se ofrece con el otro |
| E10 | Apertura especial un domingo sin horario | se ofrecen sus inicios |
| E11 | Bloqueo dentro de una apertura | gana el bloqueo |
| E12 | `now` = 09:57, anticipación 5 | 10:00 no se ofrece (3 min); con `now` = 09:55, sí (5 min, borde) |
| E13 | Día = hoy + horizonte | se ofrece; hoy + horizonte + 1 → `[]` |
| E14 | Hold vencido en el horario | se ofrece |
| E15 | Zona del negocio distinta de UTC, franja hasta 23:00 | los inicios se calculan en hora local; nada se corre de día |
| E16 | Paso de 30 con franja que abre 09:15 | 09:15, 09:45, … (la grilla arranca en la franja) |

---

## 6. Superficie pública y abuso (T3 de 5.2)

| Riesgo | Defensa |
|---|---|
| Enumerar negocios por slug | 404 idéntico para inexistente y alta sin terminar (Z8); límite por IP. |
| Enumerar turnos por código | código de 6 caracteres de 31 símbolos (~887 M) **y** WhatsApp; 404 idéntico; 10/min por IP. |
| Cancelar un turno ajeno | exige código + WhatsApp del cliente del turno (D5-8). |
| Spam de reservas | 5/min y 20/día por IP; 2 activos por cliente (D5-4); los holds vencen solos (D5-1). |
| Pisar datos de un cliente existente | el `POST` no edita el cliente (§4.2 paso 4). |
| Fuga de datos de terceros | la disponibilidad devuelve solo horas libres; el lookup solo el turno propio; nunca nombre/teléfono de otro. |
| Reserva fuera de reglas con la API a mano | el `POST` valida contra el motor (Z6). |
| Dos clientes al mismo horario | el `EXCLUDE`; uno gana, el otro 409 (test de concurrencia PG). |
| Doble envío del formulario | `Idempotency-Key`. |

## 7. Construcción (T2) — un dueño por carpeta

| PR | Backend | Frontend |
|---|---|---|
| 5.1 | migración `0010`, `domain/availability.py`, `domain/booking_state.py` (transiciones), `domain/slug.py`, `AgendaService`, repositorio de agenda, `api/v1/agenda.py`, cambios en `services`/`settings`/`resources`, `seed_staging.py` | formulario de servicio ("Solo a mano" + reglas), pestaña "Bloqueos y aperturas", campos nuevos de "Seña y reservas", test de slugs reservados |
| 5.2 | `api/v1/public.py`, `PublicBookingService`, generador de código (`domain/booking_code.py`) | `app/(turnero)/[slug]/` (layout propio con branding; pasos tamaño → servicios → día → horario → datos → confirmación; "Mi turno"), cliente HTTP público sin redirect de sesión |
| 5.3 | `api/v1/bookings.py`, `api/v1/cancellations.py`, aprobar/rechazar/no asistió/incidencias en `BookingService`/`AgendaService` | `app/(protected)/turnos/` con pestañas Próximos, Pendientes de seña, Lista de espera, Señas a resolver; contador en el menú; `/turnos` en `PROTECTED_PREFIXES` |

5.2 y 5.3 tocan `BookingService` en métodos distintos; si los dos necesitan el mismo archivo, 5.3 lo
rebasea después de mergear 5.2.

## 8. PII y secretos

- Datos de prueba sintéticos (`@example.com`, teléfonos `+54 9 351 000-xxxx`, patentes `AA000AA`); el hook
  `pii-del-spec` corta PII real.
- La patente y el teléfono no van a logs ni a Sentry (`_PATENTE_VALUE_RE` y scrubbing existente). Los
  errores del turnero público no incluyen el body.
- Las instrucciones de seña (alias/CBU) son públicas por diseño: el dueño las escribe para mostrarlas.

## 9. Aceptación

| # | Prueba | Dónde |
|---|---|---|
| B1 | Tablas nuevas con receta RLS y aislamiento contra Postgres. | `tests/security/` |
| B2 | Endpoints con sesión: 404 ajeno idéntico al inexistente; matriz permiso × endpoint (§2). | `tests/api/`, `tests/meta/` |
| B3 | Motor: E1–E16 con reloj fijo. | `tests/domain/test_availability.py` |
| B4 | Transiciones nuevas y las de F3 intactas (tabla completa). | `tests/domain/test_booking_state.py` |
| B5 | Migración `0010`: upgrade, idempotencia, downgrade con corte, slug reservado existente corta. | `tests/persistence/test_migracion_0010_pg.py` |
| B6 | `public_tenant_by_slug`: vivo + alta terminada → id; anulado, sin alta o inexistente → NULL; `carwash_app` no ve `tenants` sin contexto. | test PG |
| B7 | `POST` público: reserva normal, con seña, en revisión (2 incidencias), tercer activo 409, servicio a mano o a cotizar 422, horario fuera de reglas 409, teléfono inválido 422, idempotencia, cliente existente sin nombre pisado. | `tests/api/` |
| B8 | Concurrencia: dos `POST` simultáneos al mismo horario; 1 puesto → uno 201 y otro 409; 2 puestos → dos 201 en puestos distintos. | test PG |
| B9 | Cancelar: código + WhatsApp correcto cancela con la clasificación D-005; WhatsApp ajeno o código inexistente → 404 idéntico; repetir es idempotente. | `tests/api/` |
| B10 | Incidencias: TARDIA, POSTERIOR, CANCELADO_DEMORA, NO_ASISTIO y marca manual cuentan; OPERATIVA, anuladas y > 90 días no. | `tests/application/` |
| B11 | Bandeja: reprogramar y cambiar de puesto (libre → OK; ocupado → 409; bloqueo → 409; fuera de reglas → OK con aviso); confirmar seña (también tardía con horario libre y con horario tomado → 409), aprobar (con y sin horario libre), rechazar, no asistió, resolver seña. | `tests/application/`, `tests/api/` |
| B12 | Rutas: cada carpeta de primer nivel del front es slug reservado; `/turnos` protegida. | `frontend` + `tests/meta/` |
| B13 | Front: flujo público completo con mocks (tamaño → … → confirmación, "Mi turno", cancelar); bandeja con sus acciones. | `frontend` (jest) |

**Checkpoint manual (T4, staging)** — del ROADMAP más lo de este contrato:

1. Reservar desde el celular en `/<slug>` y que el turno aparezca en la bandeja.
2. Un servicio con regla vie–sáb no ofrece horarios un martes.
3. Uno "solo a mano" no aparece; uno a cotizar muestra "Consultar por WhatsApp" y no deja reservar.
4. Un día bloqueado no ofrece nada; una apertura especial en un día cerrado sí.
5. Un cliente con 2 incidencias queda en lista de espera sin seña; aprobarlo lo pasa a pendiente de seña.
6. El hold vencido libera el horario; confirmar la seña antes de que venza lo confirma.
7. Cancelar con código y WhatsApp funciona; con otro WhatsApp, no.
8. Un tercer turno con el mismo WhatsApp se rechaza.
9. Cambiar un turno de horario y de puesto desde la bandeja; cambiar el orden de los puestos y el intervalo de la grilla, y ver que el turnero lo respeta.
10. El wizard y la configuración existentes siguen andando igual (regresión de F4).

## 10. Respuestas del dueño

D5-1 a D5-5 y D5-10 (2026-11-11). D5-6 a D5-9 son decisiones por defecto de este contrato: se confirman o se
cambian en el checkpoint.

## 12. Adenda del PR 5.1 — motor y configuración de la agenda (2026-11-11)

### Decisiones tomadas al construir

Donde el contrato era ambiguo se eligió la lectura más conservadora.

| # | Decisión |
|---|---|
| 5.1-1 | **Bloqueos y aperturas se cargan en hora local del negocio** (`date`, `end_date` opcional e inclusive, `start_time`/`end_time` opcionales; sin horas = día completo `[00:00, 00:00)`), no como instantes. El backend los convierte con `tenants.timezone` (Z9) y responde instantes UTC. Tope: un año por rango. El front no tiene que calcular zonas. |
| 5.1-2 | **El downgrade de `0010` deja `PENDIENTE_REVISION` y `RECHAZADO` en `booking_status`**: Postgres no saca valores de un enum sin recrear el tipo (y con él el `EXCLUDE` y los CHECK). Corta antes si algún turno vivo los usa; sin filas son inertes y volver a subir los reusa (`ADD VALUE IF NOT EXISTS`, en un bloque autocommit propio). El downgrade también corta con reglas, aperturas o marcas vivas, servicios `SOLO_A_MANO` o reservas configuradas distinto del default. |
| 5.1-3 | **`public_tenant_by_slug` necesita lectura del dueño** sobre `tenants` y `tenant_settings` (políticas `FOR SELECT TO carwash_owner`, `<tabla>_owner_public_lookup`), como `users_owner_auth_lookup` para el login: con `FORCE ROW LEVEL SECURITY` la función `SECURITY DEFINER` no vería filas. No alcanza al rol de runtime. Se ajustaron los tests de políticas y del dueño sin contexto. |
| 5.1-4 | **`PENDIENTE_REVISION` entra a `_ACTIVE` tal cual dice §3.5**: además de `CANCEL_BY_CLIENT`, habilita `CANCEL_BY_CLIENT_AFTER_START` y `CANCEL_OPERATIONAL` desde la lista de espera (las tres comparten el conjunto). |
| 5.1-5 | **`DELETE /resources/{id}` → 409 `IN_USE`** cuenta `PENDIENTE_SEÑA`, `CONFIRMADO`, `RECIBIDO`, `PENDIENTE_REVISION` **y `PENDIENTE_COTIZACION`** (también guarda el puesto), con `end_at > now` (un turno en curso también lo retiene), más estricto que "`start_at > now`" del contrato. |
| 5.1-6 | **El alta de un bloqueo devuelve los turnos activos que pisa** (`PENDIENTE_SEÑA`, `PENDIENTE_COTIZACION`, `PENDIENTE_REVISION`, `CONFIRMADO`, `RECIBIDO`) con el nombre del cliente, sin teléfono: el contacto queda para la bandeja de 5.3. No los cancela (R-T-052). |
| 5.1-7 | **Incidencias (D-014):** la ventana de 90 días se mide por `requested_at` (cancelaciones `TARDIA`/`POSTERIOR_AL_TURNO`), `start_at` (turnos `CANCELADO_DEMORA`/`NO_ASISTIO`) y `created_at` (marcas vivas), sin tope superior. Lo anulado no cuenta. |
| 5.1-8 | **`GET /agenda/availability` no filtra `SOLO_A_MANO` ni `A_COTIZAR`**: es la disponibilidad interna del negocio (que sí los agenda a mano). El filtro es del turnero público (5.2). Devuelve `slots` con los puestos libres en `sort_order`. |
| 5.1-9 | **`deposit_instructions` nunca se guarda vacía** (CHECK `1–500`; el PATCH convierte blanco en `null`). |
| 5.1-10 | **El formulario de alta de servicio no edita modo ni reglas**: nace `TURNERO` sin reglas y se ajusta con "Editar" (donde están el interruptor "Solo a mano" y el editor de ventanas). |
| 5.1-11 | **Test de slugs reservados en los dos lados:** el front tiene la lista espejo `src/lib/reservedSlugs.ts` (la usa el formulario del negocio para avisar antes de guardar) y su test de carpetas; el backend compara las dos listas y vuelve a leer las carpetas. |
| 5.1-12 | La pestaña "Bloqueos y aperturas" se ve con `AGENDA_VER` (o `TURNOS_GESTIONAR`); por eso `/configuracion` deja entrar también a quien solo tiene permisos de agenda (ve únicamente esa pestaña). El orden de los puestos ya era editable (D5-6): solo se aclaró el texto de ayuda. |
| 5.1-13 | **`GET /agenda/availability` no aplica horizonte ni anticipación mínima** (revisión T3b): el negocio agenda a mano a quien llama para dentro de tres semanas o ya está en el lavadero. Mira hasta un año. `AgendaService.availability(public=True)` las aplica: es lo que usa el turnero de 5.2. |
| 5.1-14 | **Bajarse de la lista de espera no es incidencia:** si el cliente cancela un `PENDIENTE_REVISION`, la cancelación queda `NORMAL` sin importar la anticipación. El turno nunca se aprobó ni reservó horario. |
| 5.1-15 | **Un solo conjunto de turnos "en pie"** (`ACTIVE_BOOKING_STATUSES` en `domain/booking_state.py`) para anular un puesto, anular un servicio y avisar al bloquear. Antes había tres copias, y la del servicio no tenía `PENDIENTE_REVISION`. Las fechas fuera de 2000–2200 dan 422 (antes, `9999-12-31` desbordaba y daba 500). |

### Revisión de código (T3b) — diferidos con motivo

| Hallazgo | Motivo |
|---|---|
| `_replace_rules` y `void_service` anulan a mano en vez de usar `BaseRepository.void`; `check_rules` corre dos veces | Es el mismo patrón que ya usa `void_service` con precios desde F4. Unificar la anulación toca todos los servicios y va en su propio PR. El front ya no manda reglas sin cambios. |
| `count_incidents` lee filas en vez de hacer `COUNT(*)`; `create_block` busca los clientes con N+1 | Con 90 días por cliente son pocas filas, y un bloqueo pisa decenas de turnos como mucho. Se pasa a `COUNT` en 5.2, donde el conteo corre en cada pedido público. |

## 14. Adenda del PR 5.3 — bandeja del dueño (2026-11-12)

### Decisiones tomadas al construir

Donde el contrato era ambiguo se eligió la lectura más conservadora. Sin migración (Z2).

| # | Decisión |
|---|---|
| 5.3-1 | **Rechazar deja su fila en `cancellations`** (`NEGOCIO`, `OPERATIVA`, `PENDIENTE_REVISION → RECHAZADO`, motivo, actor y hora): sin migración no hay otra columna para el motivo de un rechazo, y es la fila que ya guarda la decisión del negocio sobre un turno. `OPERATIVA` no cuenta como incidencia; sin seña cobrada nace `SIN_PAGO` y no aparece en "Señas a resolver". Rechazar dos veces devuelve la misma fila. |
| 5.3-2 | **Aprobar** recalcula la seña con la configuración vigente (el % de cada servicio o el del negocio, D4-17) y actualiza también el `deposit_bps` de cada ítem. Elige el puesto otra vez (D5-6): el primero por `sort_order` sin bloqueo que el `EXCLUDE` acepte; si un bloqueo cubre todos los puestos → 409 `BLOCKED`; si ninguno entra → 409 `SLOT_TAKEN` y el pedido sigue en lista de espera. No se aprueba un pedido cuyo horario ya pasó (422) ni uno con un servicio a cotizar (422: se agenda a mano). |
| 5.3-3 | **Reprogramar** al pasado es 422; cambiar **solo el puesto** de un turno que ya empezó, sí. Un `PENDIENTE_SEÑA` con el hold vencido se vence primero (perezoso) y entonces es 409 `CONFLICT`: corresponde confirmar la seña tarde. `outside_rules` mira franjas del día, aperturas especiales (las del puesto o globales) y reglas de los servicios del turno, **sin la grilla** (`slot_step_min`): el dueño elige cualquier minuto. Es la función pura `fits_schedule` de `domain/availability.py`. |
| 5.3-4 | **`POST /bookings/{id}/confirm-deposit`**: `Idempotency-Key` **obligatorio** (es la clave del pago: un reenvío devuelve el turno, 200). `amount_cents` opcional: sin importe, la seña pedida. Un solo método de servicio (`confirm_deposit_or_late`) vence el hold si corresponde y elige entre `confirm_deposit` y `confirm_late`; la respuesta dice `late`. Un turno sin seña pedida → 409 `CONFLICT`. |
| 5.3-5 | **Códigos de error nuevos:** `SLOT_TAKEN` (el `EXCLUDE`) y `BLOCKED` (un bloqueo). Una transición que el estado no admite → 409 `CONFLICT`. 5.2 también usa `SLOT_TAKEN`: si los dos PR lo agregan, es el mismo valor. |
| 5.3-6 | **Endpoints de lectura no listados en §4.1:** `GET /cancellations` (casos de seña abiertos, el más viejo primero, `AGENDA_VER`). `GET /bookings` filtra por `status` (repetible) y por **día local** `from`/`to` (inclusive) del inicio, como bloqueos y aperturas (5.1-1). `BookingSummary` trae cliente (nombre y WhatsApp), patente, servicios, seña cobrada y `vehicle_size_id` (para pedir la disponibilidad al reprogramar). |
| 5.3-7 | **Contador del menú** (`GET /bookings/counts`): `PENDIENTE_SEÑA` + `PENDIENTE_REVISION` que todavía no empezaron + casos de seña abiertos. "Pendientes de seña" muestra además los `VENCIDO` con seña de hoy en adelante (para la seña tardía), pero no los cuenta. El front lo refresca cada minuto y al volver a la pestaña. |
| 5.3-8 | **Las acciones sin pago** (aprobar, rechazar, cancelar, no asistió, marcar incidencia, reprogramar) aceptan `Idempotency-Key` opcional (`claim_idempotency_key`): un doble toque es `DUPLICATE_IDEMPOTENT`, que el front trata como hecho. Resolver una seña con devolución exige la clave (es la del pago `DEVOLUCION`). |
| 5.3-9 | **La marca manual de incidencia** cuelga del turno desde el que se marca (`booking_id`) y del cliente de ese turno. "Desmarcar" anula con `PEDIDO_DEL_USUARIO`. El detalle del turno trae las incidencias de 90 días y las marcas vivas del cliente. |
| 5.3-10 | **Lecturas en un servicio nuevo** (`BookingInboxService`, con su repositorio `repositories/inbox.py`) para no pisar `AgendaService`/repositorios de agenda que toca 5.2; las transiciones nuevas (`approve_review`, `reject_review`, `reschedule`, `confirm_deposit_or_late`) van en `BookingService`, al final de la clase. |

### Ronda de revisión (T3 y code-review, PR #25)

| # | Qué cambió |
|---|---|
| 5.3-11 | **Deadlock contra el `EXCLUDE` → 409 `SLOT_TAKEN`.** Dos escrituras concurrentes sobre el mismo puesto y horario pueden terminar en deadlock (`40P01`, cada una espera la fila sin commitear de la otra) en vez de `23P01`. `guarded_savepoint` suma el parámetro opt-in `deadlock_label`; con él, el deadlock sale como `SavepointConflictError` y el servicio lo traduce a `SlotTakenError`. Lo usan `create`, `confirm_late`, `approve_review` (prueba el puesto siguiente) y `reschedule`. Sin la etiqueta (todos los demás llamadores, p. ej. pagos) el comportamiento no cambia. |
| 5.3-12 | **`mark_no_show` pide `now` y rechaza un turno que no empezó** (`start_at > now` → `GuardFailedError`, 422). Cambia la firma (`now` obligatorio): se actualizaron sus llamadores. |
| 5.3-13 | **Un `VENCIDO` sin seña pedida** (`deposit_required_cents == 0`) en `confirm-deposit` es 409 `CONFLICT`, como dice 5.3-4 (antes caía en 422). |
| 5.3-14 | **El reenvío de una seña tardía devuelve `late: true`.** Sin columna nueva: la seña que confirma un `VENCIDO` se guarda con `payments.notes = "late-confirmation"` y el reenvío lo lee. |
| 5.3-15 | **"Sin resolver"** en Próximos: turnos de días anteriores que siguen `CONFIRMADO` (`status=CONFIRMADO&to=ayer`, sin tope inferior) con "No asistió" y "Cancelar". `RECIBIDO` no entra: ya tiene su trabajo en curso (F6) y el negocio no cancela un turno recibido (R-T-036). El backend no cambió: el filtro `to` sin `from` ya existía. |
| 5.3-16 | **Incidencias en la pantalla:** botón "Incidencias" en cada turno (con `AGENDA_VER`) con las marcas vivas del cliente y "Desmarcar" (`TURNOS_GESTIONAR`), sobre `GET /bookings/{id}` y `DELETE /incident-marks/{id}`. |
| 5.3-17 | **Claves de idempotencia por acción:** el front reusa la clave mientras la acción falla y la descarta tras el éxito (una segunda incidencia sobre el mismo turno ya no se toma por reenvío). **Errores por acción:** un 422 o un 409 dicen qué falló según la acción (no asistió antes de hora, aprobar un pedido vencido o con servicio a cotizar, seña sin importe, etc.). |
| 5.3-18 | `list_deposit_cases` pide los turnos en un solo lote (`InboxBookingRepository.by_ids`, con anulados), sin N+1. El medio de pago por defecto de los diálogos de seña se deriva en cada render (la lista puede llegar después). |

### Informativo (heredado)

- La `Idempotency-Key` es única por `(tenant, key)` **sin importar la acción** (F3, `idempotency_keys` y `payments.idempotency_key`): la misma clave usada en dos acciones distintas dará `DUPLICATE_IDEMPOTENT` o `IdempotencyKeyReusedError` en la segunda. El front arma una clave por acción y turno (`crypto.randomUUID()`), así que no ocurre en la práctica; un cliente HTTP externo tiene que usar claves distintas por operación.

### Queda abierto

- La seña que llega con el horario ya tomado (`record_deposit_after_slot_lost`) sigue sin botón: la bandeja responde 409 `SLOT_TAKEN` y el caso queda como hoy (A16 de F3, §4.1).
- El historial de quién movió qué en la agenda es de F6 (§4.1).
- `EXPIRE_REVIEW` (pedido en lista de espera cuya hora pasó sin decisión) no tiene job todavía: la bandeja solo los deja de mostrar y contar.
