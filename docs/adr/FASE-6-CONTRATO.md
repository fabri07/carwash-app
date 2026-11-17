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
