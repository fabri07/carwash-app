# Contrato — Fase 4: configuración del negocio, cuentas y onboarding

**Fecha:** 2026-10-07 (revisado el 2026-10-09: respuestas del dueño y revisión externa, §10) · **Tiempo:** T1. Se congela al entrar a T2 de cada PR; cambiarlo después es
reabrir la fase con el dueño.

**Objetivo:** un negocio (lavadero o taller de detailing) se da de alta solo, configura su catálogo con
precios, su marca y su equipo, sin que nadie toque la base. Es la primera fase con **endpoints de
dominio y pantallas**.

**Fuentes:** `docs/ROADMAP.md` (Fase 4), `docs/GLOSARIO-RUBRO.md`, `docs/adr/FASE-3-CONTRATO.md`
(esquema vigente y respuestas del dueño en §9 y adendas), decisiones del dueño del 2026-10-07 (abajo).
Referencias de patrón: `fabri07/app-gim` (personalización) y `~/dev/vektor/Vektor` (registros).

---

## 0. Decisiones del dueño (2026-10-07)

| # | Decisión |
|---|---|
| D4-1 | **Criterio de éxito:** el MVP (antes de F9) termina cuando uno o dos negocios reales en beta operan una semana completa solo con la app. Sola CleanCars es fuente de reglas, no criterio. |
| D4-2 | **Admin + subcuentas.** El `OWNER` crea empleados (`STAFF`) y les asigna un **perfil de permisos**. La app trae Encargado, Cajero y Lavador; el admin los edita o crea otros. |
| D4-3 | **Login de empleados por usuario + contraseña.** El admin elige el usuario y una clave inicial; el email es opcional. El dueño sigue entrando con email. |
| D4-4 | **Seña global:** un interruptor en Configuración la activa o desactiva para todos los servicios, con un único %. |
| D4-5 | **Con seña activa, el auto se recibe aunque no se haya pagado:** lo pendiente queda en el saldo (ya es así en F3, §9 respuesta 3). |
| D4-6 | **Todo servicio tiene precio base por tamaño.** `A_COTIZAR` = "desde $X": el precio base es referencia y la cotización fija el final. Nada se crea sin precio. |
| D4-7 | **La seña de un ítem cotizado se calcula sobre el precio acordado**, al aceptar la cotización. |
| D4-8 | **Base de vehículos:** marca, modelo, tipo de carrocería y color del mercado argentino; cada negocio suma lo que falte. |
| D4-9 | **Ventas de productos sigue en F9.** |
| D4-10 | **Cajero y lavador suelen ser la misma persona** (no siempre): el perfil Cajero incluye todo lo de Lavador; un usuario tiene un solo perfil. |
| D4-11 | **Seña sugerida: 20%** al activarla. |
| D4-12 | **El empleado ve precios y totales** aunque no los edite: no hay permiso de lectura de precios. |
| D4-13 | **Un turno a cotizar no reserva horario.** Ocupa el puesto recién cuando el cliente acepta la cotización; si el horario ya se tomó, se acepta con otro. |

## 1. Decisiones transversales

| # | Decisión | Por qué |
|---|---|---|
| Y1 | **Seis PRs** (4.1–4.6), cada uno con su ciclo T1→T4 abreviado (este contrato es el T1 común). 4.1 → 4.2 en serie; 4.3 ‖ 4.4 ‖ 4.5 en paralelo; 4.6 cierra. | Un PR por tema se revisa entero; la F3 en un solo PR tardó semanas en cerrar. |
| Y2 | **Una migración por PR**, numeradas por orden de **merge**. Los PRs paralelos rebasan su `down_revision` sobre el último mergeado antes de entrar (alembic no admite dos heads, ver PR #5). | Evita el doble head que ya pasó con `0002`/`0003`. |
| Y3 | **`provision_tenant(session, …)`** en `application/services/tenant_provisioning.py` es el único camino para crear un negocio. La usan `/auth/register`, los tests y `seed_staging.py`. **Crece por etapas:** 4.1 crea tenant, `OWNER` y los 3 perfiles; 4.2 suma `tenant_settings`; 4.4 suma la copia de vehículos. La migración de cada etapa completa también los tenants existentes. | Hoy el registro arma el tenant a mano en el endpoint; con 5 cosas que crear, tres copias divergen. Por etapas, cada PR se despliega solo. |
| Y4 | **Permisos como enum congelado** (`Permission`, como `Role`, ADR-0005) y guardados en `text[]` con CHECK de subconjunto. No hay tabla de permisos. | Los permisos los define el código (cada uno protege un endpoint); el admin solo elige cuáles da. |
| Y5 | **`OWNER` tiene todos los permisos implícitamente** y es el único que configura el negocio y el equipo. No hay permiso para "dar permisos". | Evita escalada: un empleado nunca puede darse más de lo que tiene. |
| Y6 | **El glosario vive en código** (`backend/app/data/glosario.json`, generado y verificado contra `docs/GLOSARIO-RUBRO.md`). Sin tablas. | ~200 términos de solo lectura, iguales para todos los negocios. Una tabla de plataforma sin `tenant_id` necesitaría su excepción de RLS y de meta-tests. |
| Y7 | **La base de vehículos se copia a cada tenant** al registrarse (tablas con RLS). | FKs compuestas simples (X4 de F3) y el negocio edita, oculta y agrega sin tablas de plataforma ni FKs polimórficas. ~40 marcas y ~400 modelos por tenant. |
| Y8 | **Búsqueda sin extensiones en F4.** Cada fila buscable guarda `search_key` (normalizado en Python con `domain/search.py::normalize`); la tolerancia a errores de tipeo se calcula en Python sobre el catálogo del tenant. `domain/search.py` entra en **4.2**, así 4.3 y 4.4 no dependen entre sí. `pg_trgm` llega en F6/F7 (clientes y patentes). | Catálogos chicos (decenas de servicios, cientos de modelos). No hace falta que el dueño cree `unaccent`/`pg_trgm` en Railway ahora. |
| Y9 | **Logo en Cloudflare R2, bucket público, clave por hash** (`logos/<tenant_id>/<sha256>.<ext>`). | Inmutable y cacheable sin ruta del backend ni lectura anónima de la base. El logo no es dato sensible. |
| Y10 | **`slug` en `tenants`** (único global entre vivos, `^[a-z0-9-]{3,40}$`, generado del nombre y editable). El resto de la configuración, branding incluido, en `tenant_settings` (1:1). | La URL pública (F5) identifica al tenant; lo demás es configuración. |
| Y11 | **Recurso ajeno → 404, permiso faltante → 403.** Un 403 solo se da sobre recursos del propio tenant. | Regla de F2: no filtrar existencia entre tenants. Dentro del tenant, el empleado sabe que el recurso existe. |
| Y12 | **Se borra `dummy_resources`** (X13 de F3) en el PR 4.1; sus tests HTTP de aislamiento se portan a `permission_profiles`. | Es el primer endpoint de dominio. |
| Y13 | **Formularios con react-hook-form + zod** (ADR-0010), tipos desde OpenAPI (ADR-0013), listados con paginación del servidor (`PaginatedResponse`, ADR-0006). De Véktor se porta `SmartTable`, `csv`, `period`; no su "traer todo". | Convenciones ya adoptadas. |

---

## 2. Permisos (PR 4.1)

### 2.1 Enum `Permission` (`app/domain/permissions.py`)

| Valor | Protege |
|---|---|
| `AGENDA_VER` | ver turnos y agenda |
| `TURNOS_GESTIONAR` | crear, reprogramar, cancelar turnos; cotizaciones |
| `JOBS_OPERAR` | recibir, iniciar, terminar, entregar |
| `COBROS_REGISTRAR` | registrar cobros y señas |
| `COBROS_ANULAR` | anular un cobro (C3 de F3: aparte, con motivo) |
| `CAJA_VER` | ver caja y saldos |
| `CATALOGO_EDITAR` | servicios, categorías, tamaños, puestos, vehículos |
| `PRECIOS_EDITAR` | precios por tamaño y recargos |
| `CLIENTES_VER` | ver clientes y vehículos |
| `CLIENTES_EDITAR` | alta y edición de clientes y vehículos |
| `GASTOS_REGISTRAR` | gastos (F7B) |
| `REPORTES_VER` | reportes y exportaciones |

Valores en MAYÚSCULA (X6 de F3); en la UI se muestran con etiqueta en castellano. Agregar uno es
cambio de código + migración del CHECK (meta-test igual a `tests/meta/test_roles.py`).

### 2.2 Perfiles por defecto (creados por `provision_tenant`, editables)

| Perfil | Permisos |
|---|---|
| Encargado | todos |
| Cajero | `AGENDA_VER`, `TURNOS_GESTIONAR`, `JOBS_OPERAR`, `COBROS_REGISTRAR`, `CAJA_VER`, `CLIENTES_VER`, `CLIENTES_EDITAR` |
| Lavador | `AGENDA_VER`, `JOBS_OPERAR`, `CLIENTES_VER` |

Cajero ⊇ Lavador: cuando la misma persona cobra y lava (lo usual, D4-10) usa Cajero; Lavador es para
cuando son personas distintas.

### 2.3 Reglas de autorización

- Los permisos se leen de la base **en cada request** (no viajan en el JWT): cambiar un perfil rige
  desde el request siguiente.
- Leer precios no exige permiso (D4-12). **Crear un servicio** (que siempre lleva precios, D4-6) exige
  `CATALOGO_EDITAR` **y** `PRECIOS_EDITAR`; el `PATCH` de un servicio no acepta campos de precio.
- **Cambio de clave obligatorio:** con `must_change_password` solo responden `GET /auth/me`,
  `POST /auth/change-password`, `POST /auth/logout` y `POST /auth/refresh` (el refresh conserva el
  flag). Todo lo demás: 403 con código `password_change_required`, cortado en el servidor desde
  `CurrentUser`, no en el frontend.
- **Reset de clave** (`/staff/{id}/reset-password`): sube `token_version` (revoca sesiones) y pone
  `must_change_password`.

---

## 3. Tablas

Todas con `TenantScopedModel`, RLS, `UNIQUE (tenant_id, id)` si son padres, FKs compuestas e índice
por FK (X4, X5 de F3). Unicidad "entre vivos" con índice parcial (X7).

### 3.1 PR 4.1 — cuentas

**`permission_profiles`** (nueva): `name` text NN (1–40, único entre vivos), `permissions text[]` NN
default `'{}'`, CHECK `permissions <@ ARRAY[<valores del enum>]::text[]`.

**`users`** (se altera):

| Columna | Cambio |
|---|---|
| `email` | pasa a **nullable**; sigue único global |
| `username` | **nueva**, text nullable, único global, CHECK `^[a-z0-9._-]{3,40}$` |
| `permission_profile_id` | **nueva**, FK compuesta; CHECK `(role = 'OWNER') = (permission_profile_id IS NULL)` |
| `must_change_password` | **nueva**, bool NN default false |
| — | CHECK `email IS NOT NULL OR username IS NOT NULL`; CHECK `role <> 'OWNER' OR email IS NOT NULL` |

**Migración `0004_cuentas_permisos`:** agrega las columnas nullable, crea los 3 perfiles en cada tenant
existente, asigna **Encargado** a los `STAFF` existentes (no pierden acceso) y recién después aplica el
CHECK `OWNER ↔ perfil`.

**`auth_lookup_user`** (SECURITY DEFINER de F2) se reemplaza por `auth_lookup_user(identifier)`: busca
por `lower(email)` o por `username`, mismo contrato de columnas devueltas. Login con un solo campo
`identifier`.

### 3.2 PR 4.2 — configuración y precios

**`tenants`** (se altera): `slug` text NN, único entre vivos, CHECK de formato (Y10). Backfill:
slug del nombre + sufijo corto.

**`tenant_settings`** (nueva, PK `id` + `UNIQUE (tenant_id)` para el 1:1 — ADR-0004 prohíbe `tenant_id` como PK; sin anulación):

| Columna | Tipo | Nota |
|---|---|---|
| `address`, `whatsapp_e164`, `instagram_url`, `welcome_text`, `cancellation_policy` | text nullable | `welcome_text` ≤ 280 |
| `deposit_enabled` | bool NN default false | D4-4 |
| `deposit_bps` | int NN default 2000 (D4-11) | CHECK `deposit_bps BETWEEN 1 AND 10000`; el % se conserva con la seña apagada |
| `tolerance_min` | int NN default 20, > 0 | antes parámetro de `cancel_for_delay` |
| `late_cancel_threshold_min` | int NN default 720, > 0 | antes `DEFAULT_LATE_THRESHOLD_MIN` |
| `hold_min` | int NN default 30, > 0 | retención de `PENDIENTE_SEÑA`: `hold_expires_at = now + hold_min` |
| `min_advance_min` | int NN default 5, ≥ 0 | anticipación mínima para reservar (D-002) |
| `palette`, `font` | text NN con default | valores del catálogo en código (PR 4.5); CHECK de formato, la lista la valida el schema |
| `logo_key` | text nullable | clave R2 (PR 4.5) |
| `onboarding_completed_at` | timestamptz nullable | PR 4.6 |

**`service_prices`** (se altera): sale `deposit_bps`; `price_cents` pasa a **NOT NULL** y > 0 (D4-6).

**`booking_items`** (se altera): suma `pricing_mode` snapshot NN; se quita el CHECK
`sin_precio_sin_sena`; `price_cents` pasa a NOT NULL (es el base; en un ítem cotizado el final vive en
la cotización). `deposit_bps` es **el único snapshot de la seña**: al reservar guarda el % del tenant si
la seña está activa y 0 si no; el ítem cotizado lo guarda igual pero su seña es 0 hasta aceptar (§5).

**`job_items`**: suma `pricing_mode` snapshot NN.

**`bookings`** (se altera, D4-13): `PENDIENTE_COTIZACION` sale del `WHERE` del `EXCLUDE` y del CHECK que
exige `hold_expires_at`: un turno a cotizar no ocupa el puesto. La vigencia de la cotización es su
propio `quotes.expires_at`.

**Migración `0005_configuracion_precios`:** crea `tenant_settings` y `slug` para cada tenant existente;
completa `pricing_mode` de `booking_items` y `job_items` desde `services`. Si queda algún precio NULL en
`service_prices`, `booking_items` o `job_items`, **corta antes de alterar nada** y lista las filas.

**Preparación de staging (antes de mergear 4.2):** `scripts/prepare_staging_0005.py`, idempotente, se
niega a correr si `APP_ENV != staging`. Pone precio base sintético al servicio a cotizar del seed y
completa los ítems cotizados con el precio acordado (o el base si no hay cotización aceptada). Lo corre
el dueño desde la consola de Railway, comando de una línea y sin secretos. Producción está en 0001 sin
datos: no se prepara.

### 3.3 PR 4.3 — servicios

**`service_categories`** (nueva): `name` (único entre vivos), `sort_order`.
**`services`** (se altera): `category_id` FK nullable, `glossary_code` text nullable (código del
término en `glosario.json`), `search_key` text NN.

### 3.4 PR 4.4 — vehículos

**`vehicle_makes`**: `name`, `search_key`, `is_custom` bool, `hidden_at` timestamptz nullable; único
entre vivos por `search_key`.
**`vehicle_models`**: `make_id` FK, `name`, `body_type` (enum nativo `vehicle_body_type`: `SEDAN`,
`HATCHBACK`, `RURAL`, `SUV`, `PICKUP`, `UTILITARIO`, `FURGON`, `MOTO`, `OTRO`), `search_key`,
`is_custom`, `hidden_at`; único entre vivos por `(make_id, search_key)`.
**`vehicle_colors`**: `name`, `search_key`, `is_custom`, `hidden_at`.
**`body_type_sizes`**: `body_type` (único entre vivos), `vehicle_size_id` FK: qué tamaño del negocio
sugiere cada carrocería.
**`vehicles`** (se altera): `make_id`, `model_id`, `color_id` FKs nullable. `brand_model` y `color`
quedan como texto libre de respaldo.

`body_type` es enum porque lo usa el código (sugerir tamaño); marcas, modelos y colores son datos.

### 3.5 PR 4.5 — personalización

Sin tablas nuevas: usa `tenant_settings.palette|font|logo_key`. Catálogos en código
(`app/domain/branding.py`): 6 paletas (fondo, primario, secundario, en OKLCH) y 5 tipografías.

---

## 4. Endpoints (prefijo `/v1`)

Todos con `response_model`, `ERROR_RESPONSES`, `CurrentUser`; listados con `PaginatedResponse`;
`POST` con `Idempotency-Key` opcional donde lo use la cola offline.

| PR | Método y ruta | Quién |
|---|---|---|
| 4.1 | `POST /auth/login` (`identifier`, `password`) | público |
| 4.1 | `POST /auth/change-password` | autenticado; obligatorio si `must_change_password` |
| 4.1 | `GET /auth/me` → suma `permissions: Permission[]` y `mustChangePassword` (`onboardingCompleted` llega en 4.2) | autenticado |
| 4.1 | `GET/POST /permission-profiles`, `GET/PATCH/DELETE /permission-profiles/{id}` | OWNER |
| 4.1 | `GET/POST /staff`, `GET/PATCH/DELETE /staff/{id}`, `POST /staff/{id}/reset-password` | OWNER |
| 4.2 | CORS suma `PUT` a `allow_methods` (`main.py`), con test del preflight | — |
| 4.2 | `GET/PATCH /settings` | GET: autenticado; PATCH: OWNER |
| 4.2 | CRUD `/business-hours`, `/payment-methods`, `/resources`, `/vehicle-sizes` | `CATALOGO_EDITAR` (lectura: autenticado) |
| 4.3 | CRUD `/service-categories`, `/services`; `PUT /services/{id}/prices` (todos los tamaños de una vez) | `CATALOGO_EDITAR`; precios: `PRECIOS_EDITAR`; `POST /services`: ambos (§2.3) |
| 4.3 | `GET /services/search?q=` | autenticado |
| 4.3 | `GET /templates/{lavadero|detailing}`; `POST /templates/apply` (servicios elegidos **con precio por tamaño**; idempotente por `glossary_code`: no duplica servicios vivos) | OWNER |
| 4.4 | `GET /vehicle-makes?q=`, `GET /vehicle-models?make_id=&q=`, `GET /vehicle-colors` | autenticado |
| 4.4 | `POST` / `PATCH` (ocultar, renombrar) de marcas, modelos, colores; `PUT /body-type-sizes` | `CATALOGO_EDITAR` |
| 4.5 | `POST /branding/logo` (multipart), `POST /branding/suggest-palette` (multipart) | OWNER |
| 4.6 | `POST /onboarding/complete` | OWNER; 409 si falta algún requisito (§6) |

Reglas de equipo: un `DELETE /staff/{id}` anula y sube `token_version` (cierra sus sesiones). Un
perfil con empleados vivos no se anula (409). El `OWNER` no se edita por `/staff`.

---

## 5. Cambios en servicios de F3 (PR 4.2)

| Lugar | Cambio |
|---|---|
| `catalog_service.py::check_price_coherence` | Sin `deposit_bps`. Precio NN > 0 en ambas modalidades. |
| `_items.py::catalog_items` / `CatalogItem` | Siempre hay fila de precio. Snapshot `deposit_bps` = % del tenant si `deposit_enabled`, 0 si no; un ítem `A_COTIZAR` no lleva seña al reservar (D4-7). Redondeo por ítem, como en F3. |
| `booking_service.create` | Lee `tenant_settings`; aplica `min_advance_min`; `hold_expires_at = now + hold_min` solo para `PENDIENTE_SEÑA`. Con ítem cotizado nace `PENDIENTE_COTIZACION` **sin ocupar el puesto** (D4-13). Precio total: suma de bases; el ítem cotizado se identifica por `pricing_mode`. |
| `booking_service.confirm_late`, `record_deposit` | La guarda deja de apoyarse en `price_cents IS NULL`: un turno con ítem `A_COTIZAR` exige su cotización `ACEPTADO`, aunque haya precio base. |
| `booking_service` (cancelación tardía) | `late_cancel_threshold_min` de `tenant_settings`; sale la constante de `domain/booking_state.py:31`. |
| `job_service.cancel_for_delay` | `tolerance_min` de `tenant_settings`. |
| `quote_service._pass_to_booking` | Reconoce el ítem por `pricing_mode`; precio total = otros + acordado; la seña del ítem cotizado = **su `deposit_bps` guardado al reservar** × acordado (no el % vigente), redondeo por ítem. Ocupa el puesto en ese momento: si el `EXCLUDE` rechaza, 409 `slot_taken` y la cotización queda `COTIZADO` para aceptarse con otro horario. |
| `job_service.receive` | Sin cambio de regla: recibe con seña impaga (D4-5); exige cotización `ACEPTADO` para el ítem cotizado (ya lo hace). **Recibir sin seña no agrega un cargo: saldo = precio final − pagos netos.** |
| `scripts/seed_staging.py` | Usa `provision_tenant`; el servicio a cotizar con precio base. |

Las funciones de dominio siguen recibiendo los valores como parámetro (regla de F3); quien los lee de
`tenant_settings` es el servicio de aplicación.

---

## 6. Wizard de alta (PR 4.6)

Pasos: negocio → personalización → tamaños y carrocerías → servicios (desde plantilla, con precio) →
puestos y horarios → medios de pago → seña → empleados (opcional).

`POST /onboarding/complete` exige: ≥ 1 tamaño, ≥ 1 servicio, **precio para cada servicio × tamaño
vivo**, ≥ 1 puesto, ≥ 1 franja horaria, ≥ 1 medio de cobro. Si falta algo, 409 con la lista. El
`OWNER` sin onboarding completo es redirigido a `/onboarding`; un `STAFF` ve un aviso.

Cada paso se guarda en el servidor al avanzar; el wizard se reanuda en el primer paso incompleto, y
repetir un paso no duplica (plantillas idempotentes por `glossary_code`).

Agregar un tamaño nuevo después del alta deja servicios sin precio para ese tamaño: el endpoint lo
permite, pero la UI marca esos servicios y reservar con ese tamaño falla con `CatalogIncoherentError`
hasta cargar el precio (regla de F3, sin cambio).

---

## 7. Construcción (T2) — un dueño por carpeta

| PR | Backend | Frontend |
|---|---|---|
| 4.1 | `domain/permissions.py`, `api/v1/{auth,deps,permission_profiles,staff}.py`, `application/services/{tenant_provisioning,staff_service}.py`, migración | `components/ui/*` (select, checkbox, switch, dialog, tabs, table, stepper), `components/data/SmartTable.tsx`, `lib/{csv,period,money}.ts`, `app/(protected)/configuracion/equipo/`, `middleware.ts`, `Sidebar.tsx` |
| 4.2 | `persistence/models/{tenant,catalog,agenda,job}.py`, `application/services/{settings_service,booking_service,job_service,quote_service,_items,catalog_service}.py`, `api/v1/{settings,catalog}.py`, `domain/search.py`, `scripts/prepare_staging_0005.py`, migración | `app/(protected)/configuracion/` |
| 4.3 | `data/glosario.json`, `scripts/build_glosario.py`, `api/v1/services.py`, `application/services/template_service.py` | `features/catalogo/` |
| 4.4 | `data/vehiculos_ar.json`, `persistence/models/vehicle_catalog.py`, `api/v1/vehicles_catalog.py` | `features/vehiculos/` |
| 4.5 | `domain/branding.py`, `infrastructure/storage.py` (R2 + memoria), `api/v1/branding.py` | `app/(protected)/configuracion/mi-negocio/`, `lib/branding.ts` |
| 4.6 | `api/v1/onboarding.py` | `app/(protected)/onboarding/`, `features/onboarding/` |

---

## 8. PII y secretos

- `vehiculos_ar.json` y `glosario.json`: datos públicos del rubro, sin PII.
- Tests: usuarios, emails y patentes **sintéticos** (hook `pii-del-spec`).
- `username` no va a Sentry; `set_user` sigue solo con id.
- Credenciales de R2 (`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`,
  `R2_PUBLIC_BASE_URL`): las carga el dueño en Railway; nunca en el repo ni en el chat. Sin ellas, el
  endpoint de logo responde 503 y el resto de la app funciona.

---

## 9. Aceptación

| # | Prueba | Dónde |
|---|---|---|
| A1 | Cada tabla nueva con receta RLS (`_poblar_dominio.py`) y aislamiento verificado contra Postgres. | `tests/security/` |
| A2 | Cada endpoint: 404 ajeno idéntico byte a byte al 404 inexistente; `tenant_id` del body ignorado. | `tests/security/test_aislamiento_tenants.py` |
| A3 | Un empleado sin el permiso recibe 403 en su tenant; con el permiso, 2xx. Matriz permiso × endpoint generada desde el enum (ningún endpoint de dominio sin `require_permission` o `require_role`). | `tests/meta/`, `tests/api/` |
| A4 | Un empleado no puede editar perfiles, staff ni settings (OWNER only), ni darse permisos. | `tests/api/` |
| A5 | Login por email y por usuario; con `must_change_password` solo responden `me`, `change-password`, `logout` y `refresh` (§2.3). Anular o resetear un empleado cierra sus sesiones. Cambiar un perfil rige en el request siguiente. `CATALOGO_EDITAR` sin `PRECIOS_EDITAR` no crea servicios ni toca precios. | `tests/api/` |
| A6 | `provision_tenant` crea lo de su etapa (Y3); la migración de cada etapa completa los tenants existentes (`0004` asigna Encargado a los `STAFF`); dos tenants no comparten filas. | `tests/application/`, `tests/persistence/` |
| A7 | Seña: apagada → `deposit_required_cents = 0`; prendida al 20% → 20% por ítem de precio fijo; ítem cotizado sin seña hasta aceptar y con seña sobre el acordado al aceptar, **con el % guardado al reservar aunque el tenant lo haya cambiado**. Seña pagada en parte. Recibir con seña impaga funciona y el saldo es precio final − pagos netos. | `tests/application/` |
| A7b | Cotización: turno a cotizar no ocupa el puesto; `confirm_late` y la seña rechazan un turno con cotización pendiente aunque haya precio base, y con cotización vencida; aceptar con el horario tomado → 409 `slot_taken` y la cotización sigue `COTIZADO`. Reservar con menos de `min_advance_min` falla. | `tests/application/` |
| A8 | No se crea precio NULL; no se aplica una plantilla sin precio para cada tamaño; `onboarding/complete` da 409 con un servicio sin precio. | `tests/api/` |
| A9 | Búsqueda: "ceramico" → Tratamiento cerámico; "ppf" → Film de protección de pintura; "sacar rayas" → Corrección de pintura; "detaling" tolera el error. | `tests/domain/` |
| A10 | Vehículos: elegir modelo sugiere el tamaño por `body_type_sizes`; una marca agregada por el tenant A no aparece en B. | `tests/application/` |
| A11 | Logo: rechaza no-imagen, > 2 MB, < 200×200; sugiere paleta por color dominante; sin credenciales R2 → 503. | `tests/api/` |
| A12 | `glosario.json` coincide con `docs/GLOSARIO-RUBRO.md` (test de deriva). | `tests/meta/` |
| A13 | `make check`, `make test-cov` ≥ 80%, `make test-pg`, `make openapi-check`, `npm run build`; staging verde. | CI |

| A14 | CSV de `SmartTable`: "Exportar esta página" (cliente, BOM) desde 4.1; "Exportar todo lo filtrado" lo genera el backend con los mismos filtros, llega con los primeros registros (F7). | `frontend` |

**Checkpoint por PR (a mano, en staging):** solo lo que ese PR expone. En 4.1: crear un Cajero, entrar
con su usuario, cambio de clave obligatorio y menú filtrado por permisos. "No puede anular cobros" se
prueba en 4.1 con la matriz automática (A3); el recorrido a mano llega con la pantalla de cobro (F6/F7).

**Checkpoint F4 (a mano, en staging):** alta de un lavadero y de un taller de detailing con el wizard
(cortándolo a mitad y reanudando); 5 servicios con precio; logo con paleta sugerida; prender y apagar
la seña; cargar un auto por marca y modelo y ver el tamaño sugerido; buscar "ceramico", "ppf" y
"sacar rayas".

---

## 10. Respuestas del dueño (2026-10-09)

1. Perfiles por defecto: sí, sabiendo que cajero y lavador suelen ser la misma persona → D4-10.
2. Seña sugerida: 20% → D4-11.
3. El empleado ve precios aunque no los edite → D4-12.
4. (Pregunta nueva) Reserva de horario de un turno a cotizar: no reserva → D4-13.

La revisión externa del contrato (2026-10-09) sumó, verificado contra el código: alta por etapas (Y3),
un solo snapshot de seña (§3.2, §5), guarda explícita de cotización en `confirm_late` (§5), migraciones
con procedimiento y numeración por merge (§3), PK de `tenant_settings` (ADR-0004), `PUT` en CORS (§4),
excepciones del cambio de clave (§2.3), `search.py` en 4.2 (Y8), anticipación mínima de 5 min (D-002),
wizard reanudable (§6) y alcance del CSV (A14).
