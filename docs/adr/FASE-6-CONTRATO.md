# Contrato — Fase 6: agenda interna y operación rápida

**Fecha:** 2026-11-16 · **Tiempo:** T1. Se congela al entrar a T2 de cada PR; cambiarlo después es reabrir
la fase con el dueño.

**Objetivo:** que el negocio opere el día entero desde el celular, sin salir de la app: ver la agenda del
día, recibir a un cliente, iniciar, finalizar y cobrar con el mínimo de toques, y cargar a mano lo que no
pasó por el turnero (turnos "solo a mano" y trabajos ya hechos).

**KPI de producto, medido en test:** menos de 10 segundos para registrar una acción operativa.
`LLEGÓ` = 1 toque · `INICIAR` = 1 toque · `FINALIZAR` = 1 toque · `COBRAR` = 2 toques.

**Fuentes:** `docs/ROADMAP.md` (Fase 6), `docs/adr/FASE-3-CONTRATO.md` (`jobs`, `job_events`,
`JobService`, máquina de estados), `docs/adr/FASE-5-CONTRATO.md` (`bookings`, bandeja, agenda),
`docs/spec/05-decisiones-producto.md` (D-003, D-007) y `docs/spec/02*-reglas-*.md` (local-only).

> **Lo que ya existe y no se rehace.** `JobService` (recibir, iniciar, finalizar, retirar, cancelar por
> demora, cobros, ajuste de precio, inspección) con `Idempotency-Key` por acción y `occurred_at` puesto por el
> cliente; la máquina de estados `domain/job_state.py`; `tenant_settings.tolerance_min` (20 por defecto);
> la cola offline `offlineQueueStore` + `useOfflineSubmit`; el service worker "tonto" (ADR-0011) y el
> manifest PWA; el permiso `JOBS_OPERAR`. **Lo que falta:** no hay ninguna ruta HTTP de `jobs` ni pantalla
> de operación.

---

## 0. Decisiones del dueño (2026-11-16)

| # | Decisión |
|---|---|
| D6-1 | **Agenda del día en columnas por puesto:** una columna por puesto, los turnos como bloques por hora. En el celular se desliza de lado. |
| D6-2 | **Cancelar por demora es un botón manual.** A los `tolerance_min` minutos (20 por defecto, editable) del turno aparece "Cancelar por demora" y alguien lo toca. El sistema **no** cancela solo. |
| D6-3 | **Carga retrospectiva con datos completos:** cliente, vehículo, servicio, inicio, fin y cobro (cobrado o pendiente). El cliente puede ser "sin nombre" y la patente es opcional. |

Decisiones por defecto (el dueño las puede cambiar antes de T2):
- La recepción completa (suciedad, daños, objetos, fotos) es un camino **secundario y opcional** (D-007):
  el flujo normal no la toca.
- Los eventos de una carga retrospectiva llevan la hora real que se cargó en `occurred_at` y quedan
  **marcados como carga manual**, para no confundirlos con toques en vivo.
- Cobrar usa el medio de pago preferido del negocio por defecto: 2 toques (abrir, confirmar).

---

## 1. Permisos

Se reusan los que existen; no hay migración de perfiles.

| Acción | Permiso |
|---|---|
| Ver agenda y operación | `AGENDA_VER` |
| Llegó, iniciar, finalizar, retirar, cancelar por demora | `JOBS_OPERAR` |
| Cobrar, anular un cobro | `COBROS_REGISTRAR`, `COBROS_ANULAR` |
| Cargar un turno "solo a mano" | `TURNOS_GESTIONAR` |
| Carga retrospectiva | `JOBS_OPERAR` + `COBROS_REGISTRAR` (si carga el cobro) |

Recurso ajeno devuelve **404, nunca 403**. Cada método nuevo se registra en
`tests/security/_intentos_servicios.py`.

---

## 2. Tablas (migración `0011_operacion_f6`, PR 6.1)

Mínima. Lo que ya existe cubre casi todo.

1. `jobs.entry_mode` (`EN_VIVO` | `RETROSPECTIVA`, default `EN_VIVO`), `NOT NULL`. Distingue las cargas
   hechas después del hecho; `job_events.event_metadata` suma `{"manual_entry": true}` en cada evento de la
   carga.
2. Sin tablas nuevas. La agenda interna reusa `bookings` (con `source` interno y código `INT-…`); la
   carga retrospectiva reusa `jobs` + `job_items` + `job_events` + `payments`.

La migración es idempotente, con `NO FORCE`/`FORCE` RLS en los rellenos (ver
`carwash-migraciones-force-rls`) y test `test_migracion_0011_pg.py`.

---

## 3. Endpoints (prefijo `/v1`, 6.1)

### 3.1 Agenda y operación

- `GET /agenda/day?date=` → turnos y jobs del día por puesto, con estado, hora, cliente (nombre y
  patente solo para quien tiene `AGENDA_VER`), servicios y saldo. Paginación única de ADR-0006 donde
  corresponda.
- `GET /jobs/{id}`, `GET /jobs/{id}/events` (línea de tiempo).

### 3.2 Acciones del job (todas con `Idempotency-Key` y `occurred_at`)

- `POST /jobs` — recibir (con `booking_id` o ingreso sin turno).
- `POST /jobs/{id}/start`, `/finish`, `/pick-up`, `/cancel-delay`.
- `POST /jobs/{id}/payments`, `POST /jobs/{id}/payments/{pid}/void`.
- `POST /jobs/{id}/inspection` (opcional).
- Un doble toque con la misma clave devuelve el mismo resultado; con otra clave sobre un estado ya
  superado, **409** con código estable (`INVALID_TRANSITION`).

### 3.3 Agenda interna y carga retrospectiva

- `POST /bookings/manual` — turno "solo a mano" cargado por el dueño: cliente, vehículo, servicios, horario,
  puesto, seña y estado de pago. Usa `BookingService.create` (el `EXCLUDE` decide el choque).
- `POST /jobs/retrospective` — un trabajo ya hecho: cliente, vehículo, servicios, `started_at`,
  `finished_at`, cobro (medio y monto, o pendiente). Crea el job y sus eventos de una sola vez, con
  `entry_mode = RETROSPECTIVA`, y cae en la agenda de **su fecha**, no en la de hoy.

---

## 4. Frontend (6.2 y 6.3)

- **6.2 — Agenda y operación** (`app/(protected)/agenda/`, `app/(protected)/operacion/`): agenda en columnas
  por puesto (D6-1), pantalla operativa de botones grandes con los 4 toques, carga de turno a mano, carga
  retrospectiva y la recepción opcional.
- **6.3 — Offline y táctil:** toda acción operativa va por `useOfflineSubmit` (el id del ítem de la cola **es**
  la `Idempotency-Key`; un 409 duplicado cuenta como éxito); targets táctiles ≥ 44 px en estas pantallas;
  PWA instalable (el manifest y el service worker ya existen: se verifica, no se rehace).
- La hora de cada toque es la del dispositivo en el momento del toque (`occurred_at`), no la de la
  sincronización.

---

## 5. Reglas y casos borde

- **Cancelar por demora:** el botón aparece cuando `ahora − scheduled_at ≥ tolerance_min`; el servidor
  vuelve a validarlo con el reloj del negocio (las guardas ya están en `check_delay_cancellation`). Retener
  la seña sigue la política de la Fase 5 (`DEPOSIT_RETAINED`).
- **Carga retrospectiva:** `finished_at ≥ started_at`, ambos en el pasado; el puesto no se valida contra el
  `EXCLUDE` (es historia, no ocupación futura), pero se registra el puesto si se informa.
- **Cobro pendiente:** el job queda `FINALIZADO` con saldo > 0, visible en la agenda como "debe".
- **Offline:** dos toques del mismo botón en modo avión se sincronizan **una sola vez**; si entre medio otro
  dispositivo ya cambió el estado, el segundo recibe 409 y la pantalla lo muestra sin perder nada.
- **Zona horaria:** "hoy" y "ayer" son del negocio (`tenants.timezone`), no del navegador.

---

## 6. Construcción (T2) — un dueño por carpeta

```
6.0 contrato (docs) → 6.1 backend: migración 0011 + API jobs/agenda → 6.2 frontend agenda y operación ‖ 6.3 offline y táctil
```

| PR | Agente | Dueño de |
|---|---|---|
| 6.1 | Backend | `backend/app/api/v1/{jobs,agenda_day}.py`, servicios y migración `0011` |
| 6.2 | Frontend-operación | `frontend/src/app/(protected)/{agenda,operacion}/`, `features/operacion/` |
| 6.3 | Frontend-offline | `useOfflineSubmit`, `offlineQueueStore`, tamaños táctiles, tests de modo avión |

6.2 y 6.3 salen de `main` después de mergear 6.1. Un solo agente por carpeta; nadie escribe el mismo
archivo.

## 7. T3 y T3b

- T3: un tester **distinto** del implementador: modo avión, doble toque, reconexión, transiciones inválidas
  de estado, aislamiento entre negocios y permisos de cada ruta nueva.
- T3b: `/code-review high` por PR; **1 ronda de revisión y 1 de arreglos**, declaradas de antemano.
- Merge: squash a `main`; staging se despliega en cada merge.

## 8. PII y secretos

Datos sintéticos (`@example.com`, teléfonos `+54 9 351 000-xxxx`); nunca copiar PII real del spec. La patente
va en el scrubbing de Sentry (`_PATENTE_VALUE_RE`) y no se loguea. Sin secretos en el repo.

## 9. Aceptación

| # | Criterio |
|---|---|
| A1 | `GET /agenda/day` devuelve turnos y jobs del día por puesto, en la zona del negocio; un negocio ajeno da 404. |
| A2 | Llegó, iniciar y finalizar son 1 toque; cobrar son 2; cada uno es idempotente con su `Idempotency-Key`. |
| A3 | Una transición inválida devuelve 409 `INVALID_TRANSITION` y no cambia el estado. |
| A4 | "Cancelar por demora" aparece desde `tolerance_min` y el servidor lo rechaza antes; no hay cancelación automática. |
| A5 | `POST /bookings/manual` crea un turno "solo a mano" con el `EXCLUDE` decidiendo el choque (409 `SLOT_TAKEN`). |
| A6 | `POST /jobs/retrospective` crea el job con sus eventos con la hora real y `entry_mode = RETROSPECTIVA`; aparece en la agenda de su fecha. |
| A7 | En modo avión, un lavado completo registrado se sincroniza al volver la señal **una sola vez**, con la hora real de cada toque. |
| A8 | Los targets táctiles de las pantallas de operación miden ≥ 44 px. |
| A9 | Cada ruta nueva exige su permiso y pasa los tests de aislamiento. |
| A10 | `make check`, `make test-cov` (≥ 80 %), `make test-pg`, `make openapi-check` y `npm run build` en verde. |

## 10. Checkpoint manual (T4)

En el celular, con modo avión activado, registrás un lavado completo; al volver la señal aparece en la base
**una sola vez** y la línea de tiempo de `job_events` muestra los cuatro toques con la hora en que los
hiciste. Además cargás un lavado de ayer con su hora real y aparece en la agenda de ayer, no en la de hoy.

## 11. Fuera de la Fase 6

- Caja diaria, clientes y fidelidad (F7); gastos y retiros (F7B); avisos por WhatsApp (F7C).
- Mercado Pago, cotización web, TyC versionados.
- Arrastrar y soltar turnos en la agenda: se mueve con "Reprogramar" de la bandeja de la Fase 5.
- Quién movió qué en la agenda (auditoría visible) y el botón de seña tras perder el horario
  (`record_deposit_after_slot_lost`): diferidos desde 5.3 (adenda §14), entran acá solo si el dueño los pide.

---

## 12. Adenda del PR 6.1 (backend) — decisiones al construir

Lo que el contrato dejó abierto o contradecía el código, resuelto con lo conservador. Nada de esto
cambia las decisiones del dueño (§0).

**12.1 Base.** La migración `0011_operacion_f6` solo agrega `jobs.entry_mode` (enum `job_entry_mode`,
`NOT NULL`, default `EN_VIVO`). No hizo falta nada más: ni tablas, ni índices, ni relleno (los jobs
existentes quedan `EN_VIVO` por el default), así que la subida no necesita `NO FORCE`. La bajada sí
lo usa para contar y **corta** si hay jobs `RETROSPECTIVA` vivos. Diferido: un índice por
`jobs(tenant_id, arrived_at)` para la agenda del día; con decenas de jobs por día no hace falta, y
se agrega cuando una medición lo pida.

**12.2 Cancelar por demora: `>`, no `≥` (§5).** El contrato dice "ahora − scheduled_at ≥
tolerance_min", pero la guarda que ya existe (`check_delay_cancellation`) exige que la demora, en
minutos enteros (floor), **supere** la tolerancia. Se mantiene la guarda (más estricta): el botón
aparece a `tolerance_min + 1` minutos del turno. La API lo informa en `cancel_delay_from` (en `GET
/jobs/{id}` y en cada renglón de `GET /agenda/day`) para que la pantalla no recalcule la regla.
**Reloj del servidor:** `POST /jobs/{id}/cancel-delay` usa `min(occurred_at del cliente, ahora del
servidor)`: un `occurred_at` del futuro no adelanta la tolerancia, y uno legítimamente anterior
(tocado sin señal) se respeta. No hay ningún camino automático.

**12.3 Códigos de error.** Se suma `INVALID_TRANSITION` al catálogo `ErrorCode` (409): la acción no
está permitida desde el estado actual y **no cambia nada**. Una clave de idempotencia usada por otra
operación es 409 `CONFLICT` (nunca `DUPLICATE_IDEMPOTENT`: la cola offline lo cuenta como éxito y
perdería el toque). Las guardas del dominio (demora que no supera la tolerancia, retirar con deuda un
cliente nuevo, cobrar de más, horas inválidas) son 422 `VALIDATION_ERROR` con el mensaje de sistema,
como en la bandeja. El choque de un turno a mano es 409 `SLOT_TAKEN`.

**12.4 Idempotencia por ruta.** Las acciones del job reusan la de `JobService` (la clave vive en
`job_events`): el reenvío devuelve **200 con el mismo resultado** y sin efectos. `POST /jobs` y
`POST /jobs/retrospective` devuelven 201 también en el reenvío. `POST /bookings/manual` usa
`claim_idempotency_key` como el resto de la bandeja: el reenvío es 409 `DUPLICATE_IDEMPOTENT`.
`Idempotency-Key` es **obligatoria** en todas (422 si falta) y `occurred_at` también, con zona.

**12.5 Carga retrospectiva.** `JobService.record_retrospective` compone recibir, iniciar, finalizar y
cobrar con las horas reales (no reescribe la máquina de estados): `started_at` es también la
llegada y `finished_at` la hora del cobro. Mientras corre, el job nace `RETROSPECTIVA` y cada evento
(también el `JOB_SETTLED` encadenado) lleva `manual_entry: true`; la marca se apaga al terminar, así
un toque en vivo posterior sobre ese job (retirarlo) no la hereda. Las claves de cada paso derivan de
la del llamador (`:received`, `:started`, `:finished`, `:paid`): reenviar la carga entera devuelve el
job sin tocar nada, aun con el reloj atrasado. Reglas: `finished_at >= started_at`, ninguna en el
futuro (el servidor pone `now`), servicio a cotizar no se carga (422), el puesto se registra sin
validar ocupación. El servicio y su precio salen del **catálogo de hoy** (no hay forma de cargar el
precio de aquel día). Cobro: `payment: null` = pendiente (queda `FINALIZADO`, "debe"); sin `amount_cents`
= el total; parcial queda `FINALIZADO` con el resto. Cargar el cobro exige además `COBROS_REGISTRAR`
(403 si falta).

**12.6 Cliente y auto por nombre, teléfono y patente.** No existen rutas de clientes ni de
vehículos y la pantalla no tiene `customer_id`/`vehicle_id`: `POST /jobs` (sin turno),
`/jobs/retrospective` y `/bookings/manual` reciben `customer_name`, `customer_phone` y `plate`.
`OperationService` los resuelve con `CustomerService`/`VehicleService` (el teléfono identifica al
cliente; la patente, al auto). Sin nombre ni teléfono el **job** queda sin cliente y el **turno** a
mano queda a nombre de "Sin nombre"; sin patente, un job crea un auto anónimo (el job exige auto) y un
turno no lleva auto. Un reenvío de la clave **no** crea clientes ni autos de más. Recibir un turno que
vino de la web sin patente crea el auto anónimo en ese momento.

**12.7 Turno "solo a mano" (§3.3).** Fuente `AGENDA_INTERNA`, canal `CARGA_MANUAL` por defecto, código
`INT-XXXXXX` (mismo alfabeto que `TUR-`, pero `normalize_booking_code` no lo reconoce: el cliente no
lo consulta ni cancela por la web). `BookingService.create` suma `enforce_advance=False`: la
anticipación mínima es del turnero, no de la agenda interna; el turno sí tiene que ser **a futuro**
(lo pasado es carga retrospectiva). `deposit` registra la seña ya cobrada con `confirm_deposit` (clave
`:deposit`) y deja el turno `CONFIRMADO`; sin él, si el negocio pide seña, queda `PENDIENTE_SEÑA` con su
hold. Una seña en un turno que no la pide es 422; cargarla exige `COBROS_REGISTRAR`.

**12.8 Cobrar en 2 toques.** El contrato habla del "medio de pago preferido del negocio", pero ese
dato no existe en la base y §2 pide una migración mínima: la API pide `payment_method_id` y la pantalla
(6.2) elige el default de su lista de medios. Sin `amount_cents` cobra el saldo completo. Si dos
dispositivos cobran "el saldo" a la vez, uno gana y el otro recibe 422 (no hay sobrecobro).

**12.9 Agenda del día (§3.1).** `GET /agenda/day?date=` devuelve `resources` (una columna por puesto,
en el orden del negocio) y `entries` planas con `resource_id` (nulo = "sin puesto"): los **jobs** del
día más los **turnos que todavía no tienen job** en `CONFIRMADO`, `PENDIENTE_SEÑA`,
`PENDIENTE_COTIZACION` y `NO_ASISTIO`; un turno con job se muestra una sola vez, como job. Cancelados,
vencidos y lista de espera no son operación (siguen en la bandeja). El día de un job es el de su turno
(`scheduled_at`) o, sin turno, el de su llegada: por eso una carga retrospectiva cae en su fecha. No se
pagina (un día es una pantalla): tope de 500 por tipo y `truncated: true` si se pasa. Los renglones traen
nombre y teléfono del cliente y la patente (el permiso `AGENDA_VER` de la ruta); `GET /jobs/{id}/events`
sí es paginado (ADR-0006). Las respuestas de las acciones devuelven el job completo (con saldo y
cobros), así la pantalla no necesita otro pedido.

**12.10 Permisos.** Como §1. Detalle: recibir y la recepción opcional son `JOBS_OPERAR`; ver el job y
su línea de tiempo, `AGENDA_VER`. La tabla ruta → permiso está congelada en
`tests/meta/test_permisos_operacion.py`.

**12.11 Hallazgos de T3 (tester distinto del implementador).** 195 tests adversariales nuevos en
`backend/app/tests/adversarial_f6/` (incluye concurrencia real en Postgres). Sin 500, sin fugas entre
negocios, sin sobrecobro ni eventos duplicados. Cuatro hallazgos:

- **H1 `occurred_at` del futuro (arreglado).** Un reloj de celular adelantado más de 5 minutos **se
  acota a la hora del servidor** en todas las acciones (no 422: rechazarlo perdería el toque en la cola
  offline). Así un ingreso sin turno no desaparece de la agenda de hoy.
- **H3 seña mayor al total (arreglado).** `POST /bookings/manual` rechaza (422) una seña que supera el
  precio del turno.
- **H2 `occurred_at` anterior al evento previo (diferido).** La Fase 3 (X10) acepta a propósito horas
  "imposibles" entre dispositivos con relojes distintos; el orden que manda es el de registro. Tests
  marcados `xfail(strict=True)`. Reabrir si el dueño ve líneas de tiempo incoherentes en uso real.
- **H3b seña menor a la pedida (diferido).** `confirm_deposit` (F5) solo exige `> 0`; si el dueño
  cobra una seña parcial, es su decisión. `xfail(strict=True)`.
