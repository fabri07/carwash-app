# Plan de desarrollo — SaaS para lavaderos (working name: `carwash.app`)

## Context

Sola CleanCars opera hoy sobre `Sola CleanCars - Operación y Turnero 2026` (Google Sheet + Apps Script,
Drive ID `«ID-PRIVADO»`, modificado hoy). Ese prototipo no es una idea:
es un sistema en producción con reglas de negocio descubiertas operando — señas, demoras, comisiones del
Point, canales de origen, daños previos. El objetivo es convertirlo en un SaaS multi-tenant comercializable.

Ya existe Véktor (`~/dev/vektor/Vektor/`), un SaaS multi-tenant en producción con FastAPI + Next.js 15 +
Postgres, ~2.400 tests, CI en 3 workflows, Sentry instrumentado y deploy Railway+Vercel. **Reusamos su capa
de infraestructura; el dominio del lavadero se escribe de cero.** No es un fork ni un monorepo compartido:
repo nuevo, infra portada a mano.

Decisiones tomadas por el usuario en esta sesión:
- **Alcance de la primera entrega:** núcleo operativo — turnero público, agenda, operación rápida, caja,
  clientes/vehículos — **más un mínimo de costos** (gastos, retiros, empleados y el resultado operativo),
  porque sin eso el Sheet no se puede apagar. Comisiones automáticas, liquidaciones, analytics de empleados,
  dashboard avanzado, WhatsApp y Mercado Pago quedan fuera (v2, alimentados por el uso real).
- **Stack:** base Véktor (FastAPI + Next.js, Railway + Vercel).
- **Fuente de la spec:** la planilla de Drive, que yo extraigo.
- **Modo de trabajo:** equipos de agentes por fase, con checkpoint de aprobación humana al cierre de cada una.

**Definición de "primera entrega" (criterio de éxito único):** Sola CleanCars opera una semana completa
100% en la app, con el Sheet apagado como sistema operativo y conservado solo como backup histórico.
El MVP **no** se declara terminado porque "la app funciona".

**Orden de ejecución:**
`F1 Spec → F2 Infra → F3 Dominio → F4 Onboarding → F5 Turnero → F6 Operación → F7 Caja/CRM → F7B Costos → F8 Migración/go-live`

No se escribe un modelo SQL ni una pantalla hasta que `docs/spec/02-reglas-negocio.md` esté congelado:
ese archivo decide prácticamente todo lo que viene después.

> **Decisiones de producto tomadas durante la Fase 1** (contradicciones del sistema legacy resueltas
> por el dueño): ver `docs/spec/05-decisiones-producto.md`. Ese archivo **manda sobre el legacy**.

---

## Cómo trabajan los equipos de agentes (aplica a todas las fases)

Cada fase corre el mismo ciclo de 4 tiempos. El paralelismo es **real pero acotado a directorios disjuntos**
— es la única forma de que varios agentes escriban sin pisarse.

```
  T1 CONTRATO        1 agente · define el contrato de la fase (schemas, tipos, endpoints, acceptance tests)
        ↓            ← artefacto congelado: nadie lo cambia sin volver a T1
  T2 CONSTRUCCIÓN    2-3 agentes EN PARALELO, cada uno dueño de su carpeta
        ↓
  T3 VERIFICACIÓN    2 agentes EN PARALELO: tester de aislamiento + revisor adversarial
        ↓
  T3b REVISIÓN       /code-review sobre el diff completo de la fase  ← COMPUERTA
        ↓            ← la fase no llega a T4 con hallazgos abiertos
  T4 CHECKPOINT      humano (vos): corré la app, aprobá o devolvé a T2
```

**Reglas de armonía (no negociables):**
1. **Un dueño por carpeta.** Dos agentes nunca escriben el mismo archivo en el mismo tiempo.
2. **El contrato va primero.** El agente de T1 escribe los schemas Pydantic y los tests de aceptación
   *antes* de que nadie implemente. Backend y frontend construyen contra el mismo papel.
3. **El frontend no adivina tipos.** `openapi-typescript` genera `types/api.ts` desde el OpenAPI del
   backend (Véktor los escribe a mano — esa deuda no se hereda).
4. **El tester de aislamiento es un agente separado del implementador.** Nunca el mismo que escribió el
   código verifica que no filtre datos entre tenants.
5. **T3 puede devolver a T2, pero nunca a T1** dentro de la misma fase. Si el contrato estaba mal, la fase
   se reabre explícitamente con vos.
6. **Ninguna fase cierra en rojo.** CI verde (lint + tipos + tests + build + migración contra Postgres real)
   es condición de entrada al checkpoint, no un objetivo.
7. **Toda fase se revisa antes del checkpoint.** `/code-review` corre sobre el diff completo de la fase,
   y ningún hallazgo queda abierto sin resolver o sin diferir **con motivo escrito**. No se le lleva al
   humano código sin revisar: el checkpoint es para validar el producto, no para hacer de linter.
   En fases sin código el equivalente es **verificar los documentos contra la fuente** — así se descubrió
   que la cancelación automática por demora ya no existía en el código canónico, lo que cambió D-001.

Roles recurrentes: **Contrato** (Plan), **Backend**, **Frontend**, **Infra**, **Tester-aislamiento**,
**Revisor-adversarial** (`/code-review`), **Extractor** (lectura de fuentes externas).

---

## Fase 1 — Spec ejecutable desde Sola CleanCars

**Por qué primero:** construir el modelo de datos antes de leer las reglas reales es la forma más cara de
equivocarse. El Sheet ya contiene decisiones que ningún programador inventaría.

**Entradas.** La fuente canónica es **el proyecto Apps Script actualmente desplegado** — su lógica
(`guardarIngreso()`, `marcarFinalizado()`, cancelaciones, vencimientos, turnero), no el contenido de las
celdas. Las tres copias de Drive y la pestaña `TURNERO_PUBLICO` son **fuentes auxiliares** hasta que la
reconciliación diga cuál coincide con lo desplegado. La planilla aporta además el inventario de campos y los
datos reales. Pestañas detectadas: `RECEPCION`, `CONTROL_INICIO`, `TURNOS`, `REGISTRO_DIARIO`,
`CONFIGURACION`, `TURNERO_PUBLICO`.

> ⚠️ **Primer paso, bloqueante: reconciliar cuatro fuentes del mismo código.** Existen tres copias en Drive
> y ninguna es con certeza la desplegada:
>
> | Fuente | Tipo | Modificado |
> |---|---|---|
> | `Code.txt` (`1Hz1oqABpRILivAlnOvKVLQOt4o2c1oAh`) | texto plano, 24 KB | 2026-08-05 |
> | `CODE.gs` (`13MuY2mky4nbIMUloZSw_u2qpzAqmBpQ-ZY_iUbdveag`) | Google Doc, 13 KB | 2026-08-04 |
> | pestaña `TURNERO_PUBLICO` | dentro de la planilla | — |
> | **proyecto Apps Script desplegado** | — | **a exportar** |
>
> Las tres copias son de principios de agosto y **la planilla se modificó hoy**; además `Code.txt` casi
> duplica en tamaño a `CODE.gs`, así que son cortes distintos. **Acción tuya:** en el editor de Apps Script,
> `Archivo → Descargar`, y dejá los `.gs` en `docs/legacy/appsscript/`. La Fase 1 no avanza a T2 hasta que
> el diff de las cuatro fuentes esté hecho y sepamos cuál manda.

**Equipo:**
| T | Agente | Entregable |
|---|---|---|
| T1 | Reconciliador-fuentes | `docs/spec/00-fuente-canonica.md` — diff de las 4 copias, veredicto de cuál es la desplegada, lista de divergencias |
| T1 | Extractor-planilla | `docs/spec/01-inventario-hojas.md` — cada pestaña, sus columnas, tipos, fórmulas |
| T2 | Extractor-reglas ‖ Extractor-datos | `docs/spec/02-reglas-negocio.md` (Given/When/Then, cada regla citando **función:línea** del `.gs`, o hoja:celda si vive en una fórmula) ‖ `docs/spec/03-perfil-datos.md` (volúmenes, cardinalidades, calidad, huecos) |
| T3 | Revisor-adversarial | contradicciones, reglas ambiguas, y toda regla sin cita verificable al código |

**Reglas que ya se ven y hay que confirmar** (muestra del contenido leído):
- Demora > 20 min ⇒ `CANCELADO_DEMORA`, **la seña se conserva**.
- La comisión del Point es **costo interno**: el precio al cliente no cambia por pagar en una sola vez.
- IDs con forma `TUR-YYYYMMDD-XXXXXX` (turno) y `REG-YYYYMMDD-XXXXXX` (registro) — dos identidades distintas.
- Estados de turno incluyen `PRESENTE`; hay `SIN_TURNO` como ingreso válido.
- Obligatorios en recepción: nombre, WhatsApp, canal de origen, patente, tipo de vehículo, servicio,
  estado de pago, estado de turno, responsable. `Booking ID` y hora son obligatorios **solo** si llega
  con turno.
  > El texto de ayuda de la hoja también lista "medio previsto para el saldo" y "suciedad" como
  > obligatorios, pero **`guardarIngreso()` ya no los exige**: el medio de pago recién se conoce cuando el
  > cliente paga. La ayuda de la celda quedó desactualizada respecto del código. **Manda el código.** Esta
  > nota existe para que el agente de F3 no los convierta en `NOT NULL` — es exactamente el tipo de error
  > que se descubre tarde y caro.
- Se registran: daños previos, objetos de valor, autorización de fotos.

**Checkpoint:** leés `02-reglas-negocio.md` y marcás cada regla como *correcta / mal entendida / falta*.
Ese documento es el contrato de todo lo que sigue.

---

## Fase 2 — Andamiaje: repo, infra portada y deploy vivo

**Objetivo:** un "hello world" autenticado y multi-tenant, desplegado en Railway+Vercel, con CI verde.
Cero dominio de lavadero. Si esta fase termina bien, todo lo demás es escribir features.

**Qué se porta de Véktor (identificado en la exploración, alto valor / bajo riesgo):**

| Pieza | Origen | Nota |
|---|---|---|
| `_savepoint.py` | `backend/app/application/services/_savepoint.py` | **La pieza #1.** Resuelve el flush implícito de `begin_nested()` y clasifica violaciones de unique en PG *y* SQLite. Muy caro de redescubrir. |
| `conftest.py` (engine session-scoped + savepoint-rollback + FakeRedis) | `backend/app/tests/conftest.py:107-497` | Suite de minutos en vez de horas. |
| `main.py` (factory + 5 middlewares + 5 handlers + `/health` `/ready`) | `backend/app/main.py` | Menos los handlers de dominio de Véktor. |
| `deps.py` (`get_current_user/tenant_id`, `require_role`, `client_ip`, `rate_limit_key`) | `backend/app/api/v1/deps.py` | `client_ip` resuelve un bug sutil de rate-limit detrás de proxy. |
| `utils/security.py` | JWT HS256 + bcrypt, valida el claim `type` | 74 líneas, copiar tal cual. |
| `persistence/db/{base,engine,session,alembic_url}.py` | incluye `PGJSONB`/`PGTEXTARRAY` con `.with_variant()` | Es lo que permite testear en SQLite. |
| `repositories/base.py` | `BaseRepository[ModelT]` con `tenant_id` obligatorio en toda firma | |
| `idempotency.py` + patrón `Header("Idempotency-Key")` → 409 `DUPLICATE_IDEMPOTENT` | | Crítico para móvil con red inestable. |
| `observability/{sentry,logger}.py` + `lib/sentryScrub.ts` | scrubbing por clave y por valor | |
| `Dockerfile`, `railway.toml`, `scripts/{start,migrate}.sh`, `migrate_preflight.py` | `preDeployCommand` fail-safe | `migrate_preflight.py` te dice contra qué base migró. |
| 3 workflows de CI + `pyproject.toml` (ruff/mypy strict/pytest/coverage) | incluye el paso `alembic upgrade head` contra Postgres real | Dos lecciones caras ya aprendidas. |
| `Makefile` + `docker-compose.yml` + el truco de `docker-compose.override.yml` | | |
| **Frontend:** `lib/api.ts` (refresh single-flight + `X-Trace-Id`), `authStore` + `AuthHydrationBoundary`, `queryClient.ts`, app shell `(protected)/layout` + `Sidebar` + `Header`, `Toast` + `toastStore`, `app/{error,global-error,loading}.tsx`, `jest.config.ts` | | |
| **`offlineQueueStore.ts` + `useOfflineSubmit.ts`** | `frontend/src/stores/` y `features/ingestion/` | **La pieza #2.** Cola offline idempotente que distingue red/4xx/5xx y trata 409 duplicado como éxito. Hecha a medida para cargar un lavado sin señal. |

**Deuda de Véktor que NO se hereda** (decisiones explícitas de esta fase):
1. **`TenantMixin` real** — Véktor redeclara `tenant_id` en 40+ modelos a mano.
2. **RLS de Postgres cableado de verdad.** Véktor creó las políticas pero nadie llama a
   `set_tenant_context()` en producción: el aislamiento depende 100% de que ningún repositorio se olvide de
   filtrar. Acá RLS es la primera red y los tests la segunda.
3. **Un solo soft-delete** (`voided_at` + `void_reason` + CHECK de coherencia). Véktor tiene tres flavors.
4. **PK siempre `id`.** Véktor mezcla `id`, `tenant_id`, `user_id`, `token_id`.
5. **Roles como Enum**, no strings sueltos.
6. **Una sola convención de paginación** (`PaginatedResponse` siempre).
7. **`ruff format` + `.pre-commit-config.yaml` desde el commit 1.** Véktor ya no puede formatear nunca.
8. **Cobertura arranca en 80%** y solo puede subir (Véktor quedó en 60%).
9. **Auth por cookie httpOnly + middleware de Next real.** El `middleware.ts` de Véktor es un no-op
   deliberado porque el token vive en localStorage.
10. **shadcn/ui + react-hook-form** en vez de 18 componentes y formularios zod-manual a mano.
11. **PWA desde el día 1** — en Véktor no existe nada que copiar.
12. **Staging real**, no solo prod + previews.
13. `openapi-typescript` en vez de tipos copiados a mano.

**Equipo:**
| T | Agente | Dueño de |
|---|---|---|
| T1 | Contrato-infra | `docs/adr/` — las 13 decisiones de arriba, una ADR corta cada una |
| T2 | Infra-backend ‖ Infra-frontend ‖ Infra-deploy | `backend/` ‖ `frontend/` ‖ `.github/`, `Dockerfile`, `railway.toml`, `Makefile`, `docker-compose.yml` |
| T3 | Tester-aislamiento ‖ Revisor-adversarial | test cruzado de tenants sobre el recurso dummy ‖ revisión de auth y secretos |

**Checkpoint:** entrás a la URL de Vercel, te registrás, ves un dashboard vacío. CI verde. Sentry recibe un
error de prueba con el tag `tenant_id` puesto.

---

## Fase 3 — Modelo de dominio y aislamiento probado

**Objetivo:** el esquema completo del núcleo operativo, migrado, con RLS activo y aislamiento verificado
recurso por recurso. Sin UI todavía.

**Entidades** (derivadas de la spec de Fase 1, no inventadas):
`businesses` (el tenant/lavadero) · `users` · `customers` · `vehicles` · `vehicle_sizes` · `services` ·
`service_prices` (servicio × tamaño → precio **nullable**, duración, % de seña) · `business_hours` ·
**`resources`** (puestos de lavado / bays) · `bookings` (turno) · `jobs` (el lavado) · **`job_events`** ·
**`job_inspections`** · **`cancellations`** · **`quotes`** · `payments` · `cash_movements`.

> `cancellations` y `quotes` entran por D-005 y D-006 (`docs/spec/05-decisiones-producto.md`): el sistema
> actual ya tiene cancelación por el cliente con devolución de seña, y un segundo modelo de negocio de
> servicios a cotizar sin precio fijo. Ninguno estaba en el roadmap original.

**Puntos finos que salen de la spec, no del sentido común:**
- `bookings` y `jobs` son **dos tablas**, no una. El Sheet ya distingue `TUR-` de `REG-`: un ingreso
  `SIN_TURNO` crea un job sin booking. Colapsarlas obliga a inventar turnos fantasma.
- **La ocupación se modela como intervalo sobre un recurso, no como slot.** Los servicios duran 60, 90, 120
  o 300 minutos y puede haber varios puestos de lavado. `bookings` lleva `resource_id` + `[start_at, end_at)`
  y la garantía la da Postgres con una **restricción de exclusión por solapamiento**:

  ```sql
  CREATE EXTENSION IF NOT EXISTS btree_gist;
  ALTER TABLE bookings ADD CONSTRAINT bookings_no_overlap
    EXCLUDE USING gist (
      business_id WITH =,
      resource_id WITH =,
      tstzrange(start_at, end_at, '[)') WITH &&
    ) WHERE (status NOT IN ('CANCELADO', 'CANCELADO_DEMORA', 'NO_ASISTIO'));
  ```

  Así un trabajo de 14:00–16:00 bloquea todo el intervalo, y un lavadero con 3 puestos acepta 3 en paralelo.
  El `WHERE` es necesario: un turno cancelado debe liberar su franja. Un lavadero de un solo puesto es
  simplemente `resources` con una fila — no hay caso especial.
- Estado del job como máquina de estados explícita con transiciones validadas en el dominio, incluyendo
  `CANCELADO_DEMORA` y **`RETIRADO`** (ver `docs/spec/05-decisiones-producto.md`, D-003). La máquina
  termina en `COBRADO → RETIRADO`, y quedarse en `COBRADO` es la señal útil de "terminado sin retirar".
- **`job_events` es append-only y es la fuente de verdad de las transiciones.** `jobs.status` es un caché
  del último evento, no el registro. Con una PWA que reenvía acciones desde una cola offline, guardar solo
  el estado actual tira a la basura información que no se puede reconstruir después:

  ```text
  job_events
    id · business_id · job_id
    event_type · from_status · to_status
    occurred_at          ← cuándo pasó de verdad, no cuándo llegó al server
    actor_user_id
    idempotency_key      ← UNIQUE (business_id, idempotency_key)
    metadata (JSONB)
  ```

  Tres cosas que esto habilita y que sin ello se pierden: reconstruir la línea de tiempo real
  (`PRESENTE 14:03 → EN_PROCESO 14:08 → FINALIZADO 15:37 → COBRADO 15:45`), medir duraciones reales por
  servicio y por responsable sin construir nada más, y **debuggear la sincronización offline** — que es
  donde van a aparecer los bugs raros.

  Dos detalles que no son opcionales: `occurred_at` lo pone el **cliente** (el momento del toque) y es
  distinto de `created_at` (cuando el server lo recibió); con red intermitente pueden separarse horas.
  Y el `idempotency_key` con unique por negocio es lo que hace que un reenvío de la cola offline no
  duplique el evento — es la misma clave que ya viaja en el header, ahora persistida donde importa.
- La comisión del medio de pago se guarda en `payments` como **costo interno**, separada del precio al
  cliente.
- **La inspección es opcional y vive aparte.** El Sheet marca suciedad, daños previos, objetos de valor y
  autorización de fotos como obligatorios, pero la decisión de producto (Fase 6) es que la recepción
  completa sea un camino secundario. Todo eso va en `job_inspections` (0..1 por job), **nullable**, y un job
  se abre y se cierra sin tocarla. Los únicos obligatorios en el alta rápida son los que identifican al
  cliente y al trabajo: patente, tipo de vehículo, servicio y responsable. *No reconstruir `CONTROL_INICIO`.*
- Unique compuestos siempre con `business_id` (patente única *por lavadero*, no global).

**Equipo:**
| T | Agente | Dueño de |
|---|---|---|
| T1 | Contrato-dominio | ERD + `docs/spec/04-modelo-datos.md` + la máquina de estados |
| T2 | Modelos+migraciones ‖ Repositorios+servicios | `persistence/models/`, `migrations/` ‖ `repositories/`, `application/services/` |
| T3 | Tester-aislamiento ‖ Revisor-adversarial | por **cada** recurso: `TestXTenantIsolation` con read/list/patch/delete → **404 (nunca 403)** + test de `/count` + test de unique-por-tenant ‖ revisión del esquema |

**Convenciones de migración a adoptar desde acá** (probadas en Véktor, 97 migraciones):
nombre `YYYYMMDD_NNNN_descripcion.py` · docstring que explica el **porqué** · **idempotencia obligatoria**
(el `preDeployCommand` puede correr dos veces) · additive-first · preflight con mensaje accionable.

**Checkpoint:** `make test` verde, `alembic upgrade head` limpio contra Postgres, y un test que prueba que
el lavadero B no ve nada del lavadero A en ningún recurso.

---

## Fase 4 — Configuración del negocio y onboarding

**Objetivo:** que un dueño de lavadero pueda dar de alta su negocio solo, sin que vos toques la base.

Alcance: datos del negocio (nombre, logo, dirección, WhatsApp, horarios, medios de pago, % de seña,
política de cancelación) · tamaños de vehículo · catálogo de servicios con precio y duración por tamaño ·
**wizard de alta guiado**, no un formulario de configuración.

El onboarding no es cosmético: es el primer filtro de adopción. Un dueño de lavadero no es técnico.
Arranca con una plantilla de servicios precargada que puede editar, no con una tabla vacía.

**Equipo:**
| T | Agente | Dueño de |
|---|---|---|
| T1 | Contrato-API | schemas + endpoints + OpenAPI congelado |
| T2 | Backend-config ‖ Frontend-wizard ‖ Diseño-UI | `api/v1/`, `services/` ‖ `app/(protected)/configuracion/`, `features/onboarding/` ‖ design system, shadcn, tokens |
| T3 | Tester-aislamiento ‖ Revisor-adversarial | | |

**Checkpoint:** creás un lavadero ficticio de cero y cargás 5 servicios sin ayuda.

---

## Fase 5 — Turnero público y señas

**Objetivo:** `app.com/solacleancars` funcionando. El cliente reserva sin instalar nada.

Flujo: elegir vehículo → servicio → fecha → horario disponible → datos → reservar. Sin login del cliente.

**Además, por D-005: cancelación por el cliente.** Botón de arrepentimiento, clasificación
`NORMAL`/`TARDIA`/`POSTERIOR` por anticipación (umbral 720 min) y estados de resolución de seña
(`DEVOLUCION_PENDIENTE`, `EN_REVISION`). Es la parte del sistema con mayor exposición legal: maneja
plata de terceros y su devolución. La clasificación es una regla temporal — se testea con reloj congelado.

**Y por D-006: servicios a cotizar.** Un servicio puede no tener precio fijo; el cliente pide turno y el
precio se define después. Queda por resolver de dónde sale la duración de un servicio sin precio, porque
el motor de disponibilidad la necesita para ocupar el intervalo.

**Lo difícil de esta fase es la disponibilidad**, no la UI. El motor de slots parte de los horarios del
negocio, resta las ocupaciones vigentes por recurso y proyecta los huecos donde entra la duración del
servicio elegido — que no es constante: 60, 90, 120 o 300 minutos. Un lavadero con 3 puestos ofrece 3
reservas simultáneas; uno con 1 puesto, una.

Dos clientes reservando franjas que se solapan al mismo tiempo es la condición de carrera central, y se
resuelve **en la base** con la restricción de exclusión `EXCLUDE USING gist` definida en la Fase 3, nunca
con un `SELECT` previo de disponibilidad. Cuando salta, Postgres devuelve un `ExclusionViolation`;
`_savepoint.py` de Véktor es la herramienta para atraparlo dentro de un SAVEPOINT sin abortar la transacción
externa — hay que **extender su `unique_violation_classifier` para cubrir también la violación de exclusión**,
que Véktor no contempla porque nunca la necesitó.

La granularidad de presentación (mostrar horarios cada 30 min) es una decisión de UI sobre un modelo
continuo, no una discretización del modelo de datos.

Señas: se calcula el monto según el % configurado y se registra el estado. **La pasarela de pago
(Mercado Pago) queda fuera de esta entrega** — la seña se marca como pagada/pendiente manualmente. Modelar
`payments` desde ahora pensando en la integración evita el refactor de la Fase 9 del plan original.

**Equipo:**
| T | Agente | Dueño de |
|---|---|---|
| T1 | Contrato-turnero | algoritmo de disponibilidad documentado + casos borde |
| T2 | Backend-disponibilidad ‖ Frontend-público ‖ Tester-concurrencia | motor de slots sobre intervalos ‖ `app/(public)/[slug]/` ‖ reservas solapadas simultáneas, servicios de distinta duración, 1 vs N puestos |
| T3 | Tester-aislamiento ‖ Revisor-adversarial | el turnero es **público**: superficie de abuso (rate limit, enumeración de slugs, spam de reservas) | |

**Checkpoint:** reservás desde el celular en la URL pública real y el turno aparece en la base.

---

## Fase 6 — Agenda interna y operación rápida

**El corazón del producto y el diferenciador más difícil de copiar.**

KPI de producto, medido en test: **menos de 10 segundos para registrar una acción operativa.**
`LLEGÓ` = 1 toque · `INICIAR` = 1 toque · `FINALIZAR` = 1 toque · `COBRAR` = 2 toques.

Alcance: vista de agenda diaria con estados · pantalla operativa de botones grandes · recepción completa
(la de la Fase 1: suciedad, daños, objetos, fotos, adicionales) como **camino opcional y secundario** —
el Sheet ya aprendió eso y lo dice explícitamente: *"el flujo normal es RECEPCION → Guardar ingreso →
Marcar inicio; no hace falta completar estas verificaciones"* · la regla de `CANCELADO_DEMORA` a los 20 min.

**Dos requisitos que no son opcionales acá:**
1. **PWA instalable + offline.** Un lavadero tiene señal intermitente. Se porta `useOfflineSubmit` +
   `offlineQueueStore` de Véktor, donde el id del item de la cola **es** la `Idempotency-Key` y un 409
   duplicado se trata como éxito.
2. **Targets táctiles ≥ 44px.** El app shell de Véktor usa 32-36px: está pensado para escritorio adaptado,
   no para alguien con las manos mojadas. Se rehacen los tamaños.

**Equipo:**
| T | Agente | Dueño de |
|---|---|---|
| T1 | Contrato-operación | máquina de estados en endpoints + guion de los 4 toques |
| T2 | Backend-jobs ‖ Frontend-operación ‖ Frontend-PWA | transiciones, idempotencia ‖ `app/(protected)/operacion/` ‖ manifest, service worker, cola offline |
| T3 | Tester-offline ‖ Revisor-adversarial | modo avión, doble toque, reconexión ‖ transiciones inválidas de estado | |

**Checkpoint:** en el celular, con modo avión activado, registrás un lavado completo; al volver la señal
aparece en la base **una sola vez**, y la línea de tiempo de `job_events` muestra los cuatro toques con la
hora real en que los hiciste, no la hora en que se sincronizaron.

---

## Fase 7 — Caja, clientes y vehículos

**Objetivo:** cerrar el circuito del dinero y el historial del cliente.

Caja: registrar seña, saldo, medio de pago (efectivo, transferencia, tarjeta, Mercado Pago, pendiente),
comisión del medio como costo interno, cobro parcial y posterior. Cálculo de facturado / cobrado /
pendiente / caja del día.

CRM: ficha de cliente con sus vehículos y su historial (cantidad de lavados, facturado, última visita).
Búsqueda por patente y por teléfono — es como el operario realmente busca.

**Deliberadamente fuera de esta fase:** gastos, retiros, empleados y dashboard — entran en F7B, acotados.
Fuera de la entrega por completo: comisiones automáticas, liquidaciones y analytics de productividad.

**Equipo:**
| T | Agente | Dueño de |
|---|---|---|
| T1 | Contrato-caja | invariantes del dinero: un cobro nunca se duplica, nunca se pierde, siempre cuadra |
| T2 | Backend-pagos ‖ Frontend-caja ‖ Frontend-CRM | ‖ ‖ |
| T3 | Tester-contable ‖ Revisor-adversarial | cuadratura con datos generados ‖ redondeo, concurrencia, anulaciones | |

**Checkpoint:** cargás un día completo de lavados y el total de caja coincide con la suma manual.

---

## Fase 7B — Costos mínimos: gastos, retiros y empleados

**Por qué está en la primera entrega y no en v2:** el criterio de éxito es que el Sheet se apague. Hoy esos
procesos ya viven en el Sheet, así que dejarlos afuera obligaría a Sola CleanCars a seguir abriéndolo —
y el MVP no se podría declarar terminado. Es la fase más chica de todas y existe únicamente para cerrar
esa brecha.

**Alcance, deliberadamente mínimo — cuatro tablas planas y una cuenta:**
- `expenses`: fecha, categoría, descripción, importe.
- `owner_withdrawals`: fecha, importe, motivo.
- `employees`: empleado, estado.
- `employee_payments`: importe, período, modalidad (por hora / por día / % por vehículo), estado.
- Dashboard básico, que es el **Diferenciador 1** del plan original:

  ```
  ingresos − comisiones − gastos − pagos a empleados = resultado operativo
  resultado operativo − retiros del dueño        = dinero disponible
  ```

  Que los retiros queden **fuera** del resultado es el punto entero: confundir caja con rentabilidad es
  exactamente lo que hacen los sistemas para comercios chicos.

**Lo que NO entra, y hay que resistir:** cálculo automático de comisiones por lavado, liquidaciones
generadas, productividad por empleado, tiempos promedio, márgenes por servicio. Todo eso necesita datos de
uso real que todavía no existen. `employee_payments` se carga a mano; asignar el responsable a cada job ya
ocurre desde la Fase 6, así que la materia prima para automatizarlo en v2 se acumula sola desde el día 1.

**Equipo:**
| T | Agente | Dueño de |
|---|---|---|
| T1 | Contrato-costos | schemas + la fórmula del resultado, con sus casos borde (mes sin gastos, retiro mayor al resultado) |
| T2 | Backend-costos ‖ Frontend-costos-y-dashboard | `api/v1/expenses,withdrawals,employees` ‖ `app/(protected)/{gastos,empleados,resumen}/` |
| T3 | Tester-contable ‖ Revisor-adversarial | que el resultado cuadre y que los retiros **no** lo alteren ‖ | |

**Checkpoint:** cargás una semana real de gastos y un pago a un empleado, y el resultado operativo coincide
con tu cálculo a mano.

---

## Fase 8 — Migración de Sola CleanCars y go-live

**Objetivo:** el Sheet se apaga como sistema operativo.

1. **Script de migración** Sheet → Postgres: clientes, vehículos, turnos, registros, servicios, pagos,
   gastos, retiros y empleados. Idempotente y re-ejecutable (se va a correr muchas veces antes de salir bien).
2. **Reconciliación**: reporte automático que compara conteos y totales Sheet vs base y falla si no cuadran.
   Sin esto la migración es un acto de fe.
3. **Segundo tenant de prueba** cargado con datos sintéticos. El primer stress test real de aislamiento es
   cuando entra el segundo lavadero — con uno solo, los bugs de tenancy no se ven.
4. **Hardening**: `/security-review`, rate limits del turnero público, scrubbing de Sentry con
   `_PATENTE_VALUE_RE` (**la patente es el PII fuerte de este dominio**, equivale al CUIT en Véktor),
   backups verificados con una restauración de prueba.
5. **Runbook operativo**: qué hacer si se cae un sábado a las 8am. Aunque seas vos, escrito.
6. **Semana en paralelo**: Sheet y app conviviendo. Después, Sheet a solo-lectura.

**Equipo:**
| T | Agente | Dueño de |
|---|---|---|
| T1 | Contrato-migración | mapeo campo a campo Sheet → tabla, con las transformaciones |
| T2 | Migrador ‖ Reconciliador ‖ Hardening | script ‖ reporte de diferencias ‖ security review + rate limits |
| T3 | Tester-segundo-tenant ‖ Revisor-adversarial | | |

**Checkpoint (el que importa):** una semana operando sin volver al Sheet.

---

## Verificación

**Continua, en cada fase:**
```bash
cd backend && make check && make test-cov     # ruff + mypy strict + pytest ≥80%
cd frontend && npm test && npm run type-check && npm run build
```
CI en GitHub Actions replica esto y además corre `alembic upgrade head` contra un Postgres real y los tests
marcados `postgres` en secuencial (`-n 0`) — las dos lecciones caras heredadas de Véktor.

**End-to-end por fase (a mano, en el checkpoint):**
- F2: registro + login en la URL de Vercel; error de prueba llega a Sentry con `tenant_id`.
- F3: `make test` con los tests de aislamiento de cada recurso en verde.
- F4: alta de un lavadero de cero, sin tocar la base.
- F5: reserva desde el celular en la URL pública.
- F6: **lavado completo en modo avión**, sincronizado sin duplicar.
- F7: día completo cargado, caja cuadrada contra suma manual.
- F7B: semana de gastos + un pago a empleado; resultado operativo coincide con tu cálculo a mano.
- F8: reporte de reconciliación en cero diferencias + una semana de operación real.

**Los cuatro tests que no pueden faltar nunca** (los que atrapan los bugs caros):
1. Aislamiento cruzado por recurso → 404, nunca 403.
2. Idempotencia: misma `Idempotency-Key` dos veces → 409; la misma key en otro tenant → 201.
3. Reservas **solapadas** simultáneas sobre el mismo recurso → una gana, la otra recibe conflicto limpio;
   y el caso complementario: la misma franja en **otro** recurso del mismo lavadero entra sin problema.
4. Un job se abre, se cierra y se cobra **sin** crear nunca un `job_inspection`.

---

## Archivos y rutas clave

- **Proyecto nuevo:** `/Users/fabriziosola/carwash.app/` (vacío hoy) → `backend/`, `frontend/`, `docs/`.
- **Fuente de infraestructura:** `~/dev/vektor/Vektor/backend/` y `~/dev/vektor/Vektor/frontend/`
  (referencia de solo lectura — **nunca se modifica**).
- **Fuente de dominio:** Sheet `«ID-PRIVADO»` (+ `.gs` exportados a
  `docs/legacy/appsscript/` si los conseguís).
- **Spec generada:** `docs/spec/01..04`, decisiones en `docs/adr/`.

## Lo que este plan deja explícitamente afuera

Comisiones automáticas y liquidaciones · analytics de empleados (productividad, tiempos promedio) ·
dashboard avanzado (ticket promedio, recurrencia, márgenes por servicio) · Mercado Pago ·
WhatsApp Cloud API · fotos antes/después · multi-sucursal · planes y facturación del SaaS · app nativa.

Todo eso entra en v2 **guiado por el uso real de Sola CleanCars y del segundo lavadero**, no por supuestos.
El modelo de datos se deja preparado para que ninguno requiera una migración destructiva: `resources` ya
soporta N puestos, `cash_movements` tiene un `kind` extensible, y cada job guarda su responsable desde la
Fase 6 — así la materia prima para automatizar comisiones se acumula sola mientras tanto.
