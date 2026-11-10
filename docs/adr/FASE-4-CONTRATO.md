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
  flag). Todo lo demás: 403 con código `PASSWORD_CHANGE_REQUIRED`, cortado en el servidor desde
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
| `username` | **nueva**, text nullable, único global **también entre anulados** (como `email` desde F2: excepción a X7, porque el login busca sin tenant y un usuario dado de baja no se reasigna a otra persona), CHECK `^[a-z0-9._-]{3,40}$` |
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

---

## 11. Adenda del PR 4.2 (2026-11-04)

Resultado de leer el código antes de construir el 4.2. Lo que sigue **corrige o completa** §3.2, §4 y §5;
donde choque con ellos, manda esta adenda. Las tres primeras son decisiones del dueño; el resto son
discrepancias entre el contrato y el código verificadas.

### 11.1 Decisiones del dueño

| # | Decisión |
|---|---|
| D4-14 | **El 4.2 se parte en tres PRs, en orden:** **4.2.0** (solo `scripts/prepare_staging_0005.py` y su test, sin migración) → **4.2a** (backend: migración `0005`, servicios de F3, endpoints, `search.py`) → **4.2b** (front de `/configuracion`, una página con pestañas). |
| D4-15 | **Un turno a cotizar no vence solo: vence con su cotización.** `PENDIENTE_COTIZACION` deja de tener `hold_expires_at` (sale de `HOLD_STATUSES`, del CHECK `hold_obligatorio` y de `expire_holds`). La vigencia es `quotes.expires_at`. |
| D4-16 | **La cotización vale 24 horas.** Columna nueva `tenant_settings.quote_validity_hours` (int NN, default 24, > 0), editable; la cotización nace con `expires_at = ahora + quote_validity_hours`. Antes la vigencia salía del hold del turno (`booking_service.create` se lo pasaba a `quote_service` como `expires_at`); sin hold quedaría `None` y nada vencería nunca. |

### 11.2 Por qué existe el PR 4.2.0 (corrige §3.2, "Preparación de staging")

§3.2 dice que el script se corre "antes de mergear 4.2". No se puede: staging se despliega al mergear a
`main` (ADR-0012 §4) y la consola de Railway corre la imagen ya desplegada, así que el script no está ahí
hasta que se mergea el PR que lo trae; y ese mismo PR trae la `0005`, que **corta** si queda un precio NULL.
Con el script en su propio PR el orden es: mergear 4.2.0 → el dueño corre
`python scripts/prepare_staging_0005.py` → recién entonces mergear 4.2a. El script es descartable: se borra
una vez que `0005` corrió en staging.

### 11.3 Discrepancias contrato ↔ código

- `record_deposit` (§5) no existe: los métodos reales son `confirm_deposit` (público) y `_record_deposit`.
- `BLOCKING_BOOKING_STATUSES` (`domain/booking_state.py`) es la fuente única del `WHERE` del `EXCLUDE`: hay
  que sacar `PENDIENTE_COTIZACION` de ahí y recrear el `EXCLUDE` en la migración (Postgres no altera el `WHERE`).
- `job_service._receive_booking` también reconoce el ítem cotizado por `price_cents IS NULL`; §5 solo nombraba
  `_pass_to_booking`. Los dos pasan a reconocerlo por `pricing_mode`.
- `_pass_to_booking` hoy ignora al ítem cotizado en la seña; debe sumarlo con su `deposit_bps` guardado × acordado.
- `cancel_operational` pasa `DEFAULT_LATE_THRESHOLD_MIN` sin usarlo para clasificar: sobra al sacar la constante.
- `tenant_settings` es "sin anulación" y por eso no hereda `TenantScopedModel` (trae `VoidableMixin`): se arma con
  `Base + UUIDPrimaryKeyMixin + TenantMixin + TimestampMixin` y `UniqueConstraint("tenant_id")`.
- **`GET /auth/me` suma `onboardingCompleted` en el 4.2** (§4, fila 4.1: "llega en 4.2"), derivado de
  `tenant_settings.onboarding_completed_at IS NOT NULL`.
- **El `slug` se edita por `PATCH /settings`** (solo `OWNER`) aunque viva en `tenants`. Errores: formato, ya
  tomado (409) y palabras reservadas (`admin`, `api`, `login`, `registro`, `onboarding`, `configuracion`…),
  porque va en la URL pública de F5. `provision_tenant` reintenta con sufijo nuevo ante una colisión.
- **Lecturas "autenticado sin permiso"** (`GET /settings` y lecturas de catálogo): el meta-test A3 exige que toda
  ruta `/v1` declare rol o permiso. Se agrega la dependencia `require_authenticated()` (`api/v1/deps.py`), que deja
  la marca de autorización "cualquier usuario autenticado"; no se amplía la lista de excepciones del meta-test.

### 11.4 Lo que cambió al construir el 4.2a

- **El script de staging se borró.** `prepare_staging_0005.py` y su test salieron en el 4.2a: ya corrió en
  staging y el seed nuevo no genera precios NULL, así que su test ya no podía pasar. `seed_staging.py` carga el
  precio base del servicio a cotizar.
- **Backfill de `bookings.price_cents`**: la `0005` deja a los turnos a cotizar existentes con la suma de los
  precios base de sus ítems (antes `NULL`). Los ítems con precio `NULL` no se completan: la migración **corta**
  y lista las filas, que es para lo que sirvió el script de staging.
- **`palette` y `font` nacen en `"default"`** hasta que el 4.5 defina el catálogo; en la base solo se valida el formato.
- **La `0005` no toca `quotes.expires_at`** (§11.1 preveía completarlo si era `NULL`): las cotizaciones nuevas
  nacen con `ahora + quote_validity_hours` y las existentes conservan su vencimiento.
- **Códigos de error nuevos**: `CODE_TAKEN` (código de tamaño o medio de pago repetido), `SLUG_TAKEN` (409) y
  `SLUG_RESERVED` (422).
- **`PUT` en CORS** (`allow_methods`), con test de preflight. Los endpoints del 4.2 usan `PATCH`, pero el
  contrato (§4) pedía el método para los siguientes PRs.
- **Búsqueda (`domain/search.py`)**: `normalize`, `edit_distance` (Damerau) y `matches(query, search_key,
  aliases=…)`. Los alias (sinónimos del glosario: "ppf", "sacar rayas") los aporta el llamador; el
  `glosario.json` llega en el 4.3.
- **Diferido**: anular un puesto, un tamaño o un medio de pago **no** revisa si está en uso (turnos futuros,
  precios vivos). Es soft-delete y no rompe datos, pero un puesto anulado con turnos futuros los deja sin puesto
  visible. Se resuelve con el 409 `IN_USE` cuando el 4.3 (precios por tamaño) y la agenda (F5) lo necesiten.

---

## 11.5 Adenda del PR 4.3 — servicios, glosario y plantillas (2026-11-05)

Backend del 4.3 (el front, `features/catalogo/`, va aparte). Completa §3.3 y §4; donde choque, manda esta
subsección.

### Qué plantilla es cada término

Regla única, fijada en `scripts/build_glosario.py` (`TEMPLATE_SECTIONS`) y probada en
`tests/meta/test_glosario.py`:

| Plantilla | Qué trae |
|---|---|
| `lavadero` | términos de tipo **`servicio` y `adicional`** de las secciones **1 (Lavado) y 2 (Interior)** del glosario |
| `detailing` | términos de tipo **`servicio` y `adicional`** de las secciones **1 a 4** (Lavado, Interior, Detailing exterior y corrección de pintura, Protección) |

Las **`tecnica`, `defecto`, `producto`, `insumo`, `modificador` y `entidad` nunca son plantilla**: solo
alimentan la búsqueda (sinónimos, defecto → servicio) o, en el caso de `producto`/`insumo`, F9. La plantilla
`lavadero` es un subconjunto de `detailing`. Cada sección del glosario es una **categoría** propuesta
(nombre = título de la sección, `sort_order` = número de sección); se crean solo si hace falta.

### Decisiones tomadas al construir

| # | Decisión |
|---|---|
| Y6a | **`glossary_code` = slug del término normalizado con guiones bajos** (`"Tratamiento cerámico"` → `tratamiento_ceramico`). Lo genera el script, es único (el script falla si dos términos colisionan) y estable mientras no se renombre el término. Renombrar un término del `.md` cambia su código: los servicios ya creados conservan el viejo y dejan de heredar sus sinónimos. |
| Y6b | **El `.json` incluye todos los términos del `.md`** (también técnicas, productos, etc.), más el mapeo `defects` (§5) y las `phrases` (§12). Una *técnica* que un defecto nombra (`Descontaminado químico`, `Lijado en húmedo`) se reemplaza por el servicio del que es parte (`TECHNIQUE_PARENT`: *Descontaminado*, *Corrección de pintura*): quien busca un defecto quiere algo vendible. |
| 4.3-1 | **Un servicio vivo por `glossary_code`** (índice único parcial `ux_services_tenant_id_glossary_code`, entre vivos; los `NULL` no chocan). Es lo que hace idempotente a la plantilla incluso ante dos pestañas del wizard a la vez. El único también cubre la carrera de dos `apply` simultáneos (el perdedor informa `skipped`). |
| 4.3-2 | **`glossary_code` no entra por `POST /services`**: solo lo fija la plantilla (`POST /templates/apply`). Un servicio creado a mano es siempre propio (`glossary_code` `null`); si el body trae el campo, se ignora. El código no se edita por `PATCH`. |
| 4.3-3 | **`pricing_mode` se cambia por `PUT /services/{id}/prices`**, no por `PATCH` (la modalidad es parte del precio, §2.3: exige `PRECIOS_EDITAR`). Es opcional en el cuerpo. |
| 4.3-4 | **Cobertura de precios (A8):** crear un servicio y `PUT .../prices` exigen **un precio válido (> 0, sin NULL) para cada tamaño vivo**, sin repetir ni traer tamaños ajenos o inexistentes: si falta alguno, **422 `VALIDATION_ERROR`** con los códigos de los que faltan. Sin tamaños vivos no se puede crear un servicio (el wizard carga los tamaños antes, §6). Tope de precio: $ 100.000.000; duración máx. 14 días. |
| 4.3-5 | **Anular un servicio** (`DELETE`) exige `CATALOGO_EDITAR` **y** `PRECIOS_EDITAR` (anula también sus precios vivos, §2.3) y da **409 `IN_USE`** si lo usan turnos que no cerraron (`PENDIENTE_SEÑA`, `PENDIENTE_COTIZACION`, `CONFIRMADO`, `RECIBIDO`), cotizaciones sin resolver (`PENDIENTE`, `COTIZADO`) o jobs sin retirar (`PRESENTE`, `EN_PROCESO`, `FINALIZADO`, `COBRADO`), como un puesto con turnos futuros. Anular los precios es necesario; si no, `void_vehicle_size` (que cuenta precios vivos) impediría anular el tamaño para siempre. Anular una categoría con servicios vivos → 409 `IN_USE`. |
| 4.3-6 | **Búsqueda** (`GET /services/search?q=` y `GET /services?q=`, misma lógica): `domain/search.match_tier` sobre `search_key` y los alias del glosario si el servicio tiene `glossary_code` (sinónimos, nombre en inglés, frases del cliente de la §12, y el nombre y sinónimos de cada defecto que el servicio resuelve). Orden: clave idéntica, sinónimo idéntico, coincide con la clave, coincide con un sinónimo. Se filtra y ordena en Python sobre los servicios vivos del tenant (Y8). El nombre original del término sigue siendo alias aunque el negocio renombre el servicio. |
| 4.3-7 | **`POST /templates/apply`** va con `kind` en el cuerpo y no lleva `Idempotency-Key`: ya es idempotente por `glossary_code` (reaplicar devuelve `skipped`, no 409). Todo en una transacción. Solo valida y crea los ítems que **todavía no existen**; los que ya existen se informan como `skipped` (`GLOSSARY_CODE`) sin tocar sus precios ni exigir los del tamaño nuevo, así el cuerpo viejo de un reintento sigue valiendo. Un servicio **propio** con el mismo nombre (misma `search_key`) se respeta: `skipped` con motivo `NAME`, no se vincula al término y su sección no genera categoría si nada más se crea en ella. Un ítem fuera de la plantilla pedida, repetido o sin precios → 422 y no se crea nada. |
| 4.3-8 | **`GET /templates/{kind}`** devuelve las categorías con sus términos, sin precios, y `already_added` por término (para reanudar el wizard, §6): verdadero si hay un servicio vivo del término **o** con el mismo nombre (`search_key`). `kind` fuera de `lavadero`/`detailing` → 422. Solo `OWNER`, como `apply`. |
| 4.3-9 | **`search_key`** nace con un default de Python (`normalize(name)` de la misma fila) además de fijarlo el servicio; la `0006` rellena los servicios existentes con una copia congelada de `normalize` y un test la compara con la viva. La base no lo calcula (Y8): un `INSERT` crudo debe traerlo. |
| 4.3-10 | **`0006_servicios_categorias`**: idempotente; relleno con `NO FORCE`/`FORCE`; **el downgrade corta** (diciendo por qué) si hay alguna categoría o algún servicio con categoría o `glossary_code`, en vez de perderlos en silencio. |
| 4.3-11 | **`missing_price_size_ids`** en la respuesta de un servicio (alta, lectura, listado, búsqueda): los tamaños vivos sin precio vivo. Crear un tamaño **no** se bloquea (§6): el servicio queda marcado hasta cargar el precio con `PUT .../prices`. |
| 4.3-12 | **`GET /services?q=`** admite vacío o sin nada que buscar (`normalize(q)` vacío): se trata como sin filtro y lista todo. `GET /services/search` sigue exigiendo `q` no vacío. |
| 4.3-13 | **Búsqueda sin renormalizar:** los alias del glosario se guardan ya normalizados y `match_tier(wanted, search_key, aliases=…)` recibe todo normalizado; la consulta se normaliza una vez por request. |
| 4.3-14 | **Pocas consultas en `apply`:** tamaños y servicios vivos se leen una vez por lote y los precios de cada servicio nuevo se insertan juntos; 56 ítems × 4 tamaños son 7 `SELECT` (un test fija el tope). `PUT .../prices` lee los precios existentes en una consulta. |

---

## 11.7 Adenda del PR 4.4 — catálogo de vehículos (2026-11-10)

Backend y front del 4.4 (`features/vehiculos/`, pestaña "Vehículos" de Configuración). Completa §3.4 y
§4; donde choque, manda esta subsección. Migración `0008_catalogo_vehiculos` (sale de
`0007_sena_por_servicio`; si el 4.5 mergea antes con migración propia, se rebasa, Y2).

### La base

`backend/app/data/vehiculos_ar.json`: **38 marcas, 396 modelos y 16 colores** del mercado argentino
(autos de los últimos ~20 años, utilitarios, furgones y motos comunes), cada modelo con su carrocería.
Datos públicos, sin PII (§8). La forma del archivo es fija (`makes[].name/models[].name/body_type`,
`colors[]`) y `tests/domain/test_base_de_vehiculos.py` verifica que no haya duplicados por
`search_key` (marcas entre sí, modelos dentro de su marca, colores), que todo nombre entre en los
CHECK de largo y que la `0008` congele el mismo enum y la misma `normalize`.

### Decisiones tomadas al construir

| # | Decisión |
|---|---|
| 4.4-1 | **`VehicleBodyType` vive en `domain/vehicle_catalog.py`**, no en `domain/enums.py`: aquel es el vocabulario congelado de la F3 (`0003`) y un test exige que cada `*_ENUM` de ese archivo esté en la `0003`. La `0008` congela sus valores (`BODY_TYPES`) y un test los compara con el enum vivo. |
| 4.4-2 | **Ocultar no es anular.** `hidden_at` saca la fila de los selectores y se deshace con `PATCH {"hidden": false}`; la fila sigue viva y **conserva su nombre en el único** (crear "Alfa Romeo" con la marca oculta es 409). No hay `DELETE` (§4 solo pide `POST`/`PATCH`): un vehículo cargado en F6 puede apuntar a la fila. Un modelo de una marca oculta tampoco sale en los selectores. |
| 4.4-3 | **Únicos entre vivos por `search_key`** (`normalize(name)`, Y8): "Citroen" y "Citroën" son la misma marca, "HILUX" choca con "Hilux" en Toyota pero no en Ford. Colores también únicos por clave (§3.4 no lo decía; sin eso se repetirían "Gris" y "gris"). Repetido → 409 `NAME_TAKEN`, como servicios y categorías. `search_key` con default de Python y fijado por el servicio, como en 4.3-9. |
| 4.4-4 | **Lectura** (`GET` de las tres listas y de cada fila, `GET /body-type-sizes`): cualquier usuario autenticado (`require_authenticated()`). **`?include_hidden=true`** trae las ocultas (la pantalla de administración); cualquiera puede pedirlo, no es un dato sensible. `?q=` vacío o sin nada buscable = sin filtro (como 4.3-12). `GET /vehicle-models` acepta **`make_id` opcional**: sin marca busca en todas, y la consulta también matchea "marca modelo" ("toyota hilux"). Un `make_id` ajeno o inexistente es **404** (no una lista vacía), idéntico byte a byte. Con `q` se rankea **todo** el alcance en Python, sin tope, así el `total` es exacto (unas 450 filas por negocio). |
| 4.4-5 | **Escritura** (`POST`/`PATCH` de marcas, modelos y colores; `PUT /body-type-sizes`): `CATALOGO_EDITAR`. Lo creado por la API es siempre `is_custom = true` (el campo del cuerpo se ignora). El `PATCH` de un modelo cambia nombre, carrocería u ocultamiento, **no la marca** (se crea el modelo en la marca correcta y se oculta el otro). `POST` con `Idempotency-Key` opcional; el `PUT` es idempotente y no la lleva. |
| 4.4-6 | **A10 como campo, no como endpoint:** cada modelo devuelve `suggested_vehicle_size_id` (y `make_name`, para listar sin marca). Sale de `body_type_sizes` unida a los tamaños **vivos**: un tamaño anulado deja de sugerirse aunque la fila siga apuntándolo (no se tocó `void_vehicle_size`). |
| 4.4-7 | **`PUT /body-type-sizes` reemplaza el mapa entero** (`{"items": [{body_type, vehicle_size_id \| null}]}`): una carrocería ausente o con `null` deja de sugerir (su fila se anula). Carrocería repetida o tamaño que no es un tamaño vivo del negocio (ajeno, anulado o inexistente: el mismo mensaje) → 422 `VALIDATION_ERROR`. `GET` y `PUT` devuelven **siempre las 9 carrocerías** en el orden del enum, con `PaginatedResponse` (ADR-0006). **Dos `PUT` a la vez se serializan** con `FOR UPDATE` sobre la fila de `tenant_settings` del negocio (en un negocio sin filas en el mapa no hay otra cosa que bloquear); si igual chocaran en el único vivo por carrocería, 409 `CONFLICT` reintentable, nunca 500. |
| 4.4-8 | **`body_type_sizes` nace vacía** en `provision_tenant` y en la `0008`: al registrarse el negocio no tiene tamaños, y adivinar por código (`AUTO`, `SUV`…) en los existentes dejaría filas que nadie eligió. La carga el negocio en Configuración → Vehículos → "Carrocería y tamaño", y el paso "tamaños y carrocerías" del wizard (4.6). |
| 4.4-9 | **Copia de la base:** `provision_tenant` llama a `copy_vehicle_base` (tres `INSERT` en lote, ~450 filas; `ProvisionedTenant.vehicle_base` informa los conteos). La `0008` copia la base a **cada tenant que todavía no tiene marcas**; uno que ya tiene no se toca, así re-aplicar no duplica ni "resucita" lo renombrado. La migración lee una **copia congelada** (`migrations/data/0008_vehiculos_ar.json`), no el archivo vivo; un test compara hoy las dos. Si la base cambia, no se toca la copia: los negocios existentes se completan con una migración de datos nueva. |
| 4.4-10 | **`vehicles.make_id/model_id/color_id`**: FKs compuestas `(tenant_id, x_id)` como todas (X4; el meta-test no admite otra forma). Que el modelo sea de la marca **lo valida la aplicación en F6**, que es quien crea vehículos; este PR no tiene endpoint de vehículos. Los existentes quedan en `NULL` con su texto libre. |
| 4.4-11 | **Downgrade de la `0008` corta** (diciendo qué y cuántas) si perdería algo **vivo** que hizo el negocio (lo anulado ya está descartado y no frena): marcas, modelos o colores propios, ocultos o **editados respecto de la base** (nombre, clave o carrocería distintos de los del archivo; los modelos de una marca renombrada también cuentan), filas de `body_type_sizes`, o vehículos con marca, modelo o color del catálogo. La copia intacta de la base se borra sin preguntar. Corre con `NO FORCE`/`FORCE`, como 0006 y 0007. |
| 4.4-12 | **Front:** `features/vehiculos/` (tabla genérica `VehicleCatalogTable` con búsqueda, "mostrar ocultas", alta, renombre y ocultar/mostrar; `BodyTypeSizesForm`; `VehiclePicker`, el selector marca → modelo → color con tamaño sugerido que usará F6, solo con filas visibles y que borra el modelo al cambiar de marca). Pestaña "Vehículos" en `/configuracion` para quien tiene `CATALOGO_EDITAR`. El selector filtra los modelos por la marca elegida y deshabilita el campo mientras llegan (con `keepPreviousData` quedaban a mano los de la marca anterior). Filtrar por una marca oculta lista sus modelos con `include_hidden` y lo avisa. El mapa de carrocerías descarta tamaños que ya no están vivos, y quitar un tamaño invalida la caché `["vehicles"]`. Reuso: `useCatalogEditor` + `useStepBackOnEmptyPage` (`hooks/useCatalogEditor.ts`, diálogo, guardado con replay y paso atrás de página) los usan `CatalogTab` y `VehicleCatalogTable`; `vehicles.service` usa el `crud` de `config.service` (con mapeo de filtros) y hay un solo `useAllVehicleSizes` (`features/configuracion/queries`). |

### Verificación sin Docker

`make test-pg` no corrió en la máquina del implementador (Docker apagado): lo corre el CI. Los tests
`postgres` nuevos (`test_migracion_0008_pg.py`, `test_aislamiento_vehiculos_pg.py`, el conteo de
políticas de `test_migraciones_idempotentes_pg.py`) se escribieron igual. Además se probó la `0008`
contra un Postgres 16 real embebido (sin `btree_gist`, con un esquema mínimo de `tenants`,
`vehicle_sizes` y `vehicles` con RLS forzado y dueño no superusuario): upgrade, re-upgrade, RLS del rol
de runtime, los cortes del downgrade, downgrade limpio y vuelta a subir; y `copy_vehicle_base` más
`VehicleCatalogService` con el rol de runtime.

---

## 11.8 Adenda del PR 4.5 — personalización: paleta, tipografía y logo (2026-11-10)

Completa §3.5, §4, §7, §8 y A11; donde choque, manda esta subsección. **Sin migración**: usa
`tenant_settings.palette|font|logo_key`, que ya existen desde la `0005`.

### Catálogo (`app/domain/branding.py`)

| Paleta (`code`) | Nombre | Para |
|---|---|---|
| `default` | Agua | el tema actual de la app (azul agua) |
| `bosque` | Bosque | verde |
| `oceano` | Océano | azul marino |
| `terracota` | Terracota | naranja quemado |
| `competicion` | Rojo competición | rojo |
| `grafito` | Grafito | logos en blanco y negro |

| Tipografía (`code`) | Familia |
|---|---|
| `default` | la del sistema (no descarga nada) |
| `inter` | Inter |
| `manrope` | Manrope |
| `plus_jakarta` | Plus Jakarta Sans |
| `outfit` | Outfit |

### Decisiones tomadas al construir

| # | Decisión |
|---|---|
| 4.5-1 | **Cada paleta trae fondo, primario, secundario y `on_primary`** (el color del texto sobre el primario), en OKLCH. Un test exige contraste WCAG AA (≥ 4.5) de `on_primary` sobre el primario y ≥ 7 del texto del tema sobre el fondo, y que los colores estén dentro de sRGB. Los códigos solo se agregan: renombrar uno deja a los negocios que lo eligieron con un valor que el `PATCH` ya no acepta. |
| 4.5-2 | **`default` reproduce el tema de `globals.css`, con el primario un punto más oscuro** (`hsl(199 89% 38%)` → `37%`): con texto blanco encima daba 4.44 de contraste, debajo de AA. Es el único cambio visual para quien no personaliza. |
| 4.5-3 | **El catálogo se expone en `GET /branding/catalog`** (cualquier usuario autenticado, `require_authenticated`), no dentro de `GET /settings`: es igual para todos y el front lo cachea sin vencimiento. Cada color va en dos formas: `oklch(...)` y `hex`. El front usa el hex porque el tema de shadcn está en triplets HSL (`hsl(var(--primary))`); pasar todo el tema a OKLCH queda para cuando haga falta. |
| 4.5-4 | **`PATCH /settings` valida `palette` y `font` contra el catálogo** (422 `VALIDATION_ERROR` si no existe; mayúsculas se normalizan). La base sigue validando solo el formato (§11.4): agregar una paleta no requiere migración. |
| 4.5-5 | **`GET /settings` suma `logo_url`** = `R2_PUBLIC_BASE_URL` + `/` + `logo_key`; `null` sin logo o sin esa variable. `logo_key` sigue en la respuesta y **no** se mueve por `PATCH` (lo mueven solo `POST`/`DELETE /branding/logo`). |
| 4.5-6 | **Validación del logo por contenido, con Pillow** (`application/services/branding_service.py`): PNG, JPEG o WebP (SVG no: puede traer scripts y no tiene píxeles para sugerir); ≤ 2 MB; ≥ 200×200. Contra bombas de descompresión se mira el tamaño **declarado** antes de decodificar y se corta en **4096 píxeles por lado** (una foto de celular entra), y la advertencia de Pillow pasa a error. La decodificación corre en el threadpool y la imagen se achica (`draft` en JPEG, `reduce` en el resto) antes de convertirla a RGBA. La extensión y el `Content-Type` del objeto salen del formato detectado, no de lo que manda el navegador. |
| 4.5-7 | **Códigos de error nuevos**: `INVALID_IMAGE` (422), `IMAGE_TOO_SMALL` (422), `FILE_TOO_LARGE` (413: archivo > 2 MB, un lado > 4096 o cuerpo HTTP > 2,5 MB, ver 4.5-16) y `STORAGE_UNAVAILABLE` (503). |
| 4.5-8 | **Sugerencia de paleta**: de una miniatura de 64×64 se descartan los píxeles transparentes; entre los de color (croma ≥ 0,04, ni casi blancos ni casi negros) se agrupa por tono en 12 sectores y se promedia en OKLab el más poblado (un logo azul sobre fondo blanco da azul, no gris). Se elige la paleta cuyo primario está más cerca en OKLab con la claridad a la mitad (un celeste y un azul marino son "la misma marca"). Un color sin croma (logo monocromo) va a la paleta neutra (`grafito`); una imagen toda transparente, a `default`. |
| 4.5-9 | **`ObjectStorage` en `app/infrastructure/storage.py`**, inyectado con `Depends(get_storage)`: `R2Storage` (boto3) o `None` si falta alguna de las cinco variables → 503; los tests lo reemplazan con `InMemoryStorage` (nunca red). **boto3 y no firma propia con `httpx`**: es el cliente que Cloudflare documenta para R2 y el que ya usa app-gim; una firma SigV4 propia sería criptografía nuestra que solo se prueba contra R2 real. Sus llamadas son síncronas y corren en el threadpool. El cliente es uno por proceso, creado bajo un `threading.Lock` y desde una `boto3.session.Session()` propia (la sesión por defecto de boto3 no es segura entre hilos). Con la configuración a medias (alguna de las cinco variables, no todas) se avisa una vez en el log (`storage.r2_partially_configured`). |
| 4.5-10 | **Objetos inmutables**: `Cache-Control: public, max-age=31536000, immutable` (la clave lleva el hash). Subir la misma imagen da la misma clave y no duplica. Toda escritura de `logo_key` toma la fila con `FOR UPDATE`. Al **reemplazar o quitar** el logo, el objeto anterior se borra en una tarea de fondo, después de responder, **con la fila tomada** y solo si el logo vigente ya no es esa clave (nunca si la anterior es igual a la nueva). Como la subida guarda la clave **antes** de subir el objeto (4.5-17), en la carrera A → B → A el borrado tardío de A o ve que A volvió (y no borra) o termina antes de que A se guarde, y la subida de A sube el objeto después. Si el borrado falla queda un huérfano público e inofensivo; no hay limpieza de huérfanos (diferido). |
| 4.5-11 | **`DELETE /branding/logo`** (OWNER, 204) funciona sin R2: la base deja de apuntar al logo; el objeto se borra solo si hay R2. **`POST /branding/suggest-palette`** tampoco necesita R2; el front lo usa como respaldo cuando la subida da 503, para que el dueño reciba la sugerencia igual. |
| 4.5-12 | **Front**: pestaña propia **Personalización** en `/configuracion` (solo el dueño), en `features/configuracion/` (§7 decía `app/(protected)/configuracion/mi-negocio/`; el 4.2b armó la configuración como una página con pestañas). `lib/branding.ts` escribe las variables (`--background`, `--primary`, `--primary-foreground`, `--ring`, `--brand-secondary`, `--font-brand`) **en `<html>`** para que las vean también diálogos y toasts (se montan en portales); `default` no escribe nada y queda el tema. Se aplica desde el layout protegido y se quita al salir de él. El secundario queda disponible como `--brand-secondary` (clase `brand-secondary`) para la página pública de F5; la app interna no lo usa todavía. |
| 4.5-13 | **Tipografías con `next/font/google`, `preload: false`**: las cuatro se declaran como variables CSS en `<html>` (layout raíz) y el navegador baja solo la elegida. Un meta-test del backend (`tests/meta/test_tipografias_del_front.py`) exige que cada código del catálogo tenga su fuente en `frontend/src/lib/fonts.ts`. |
| 4.5-14 | **Dependencias nuevas del backend**: `pillow`, `python-multipart` (formularios multipart de FastAPI) y `boto3`. |
| 4.5-15 | **Variables de R2** (`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_PUBLIC_BASE_URL`) en `config/settings.py` y `.env.example`, vacías. Las carga el dueño en Railway (staging y producción, cada uno con su bucket o al menos su token); nunca en el repo ni en el chat. |
| 4.5-16 | **Tope del cuerpo antes de autenticar** (`app/api/body_limit.py`, middleware ASGI puro, por dentro del CORS): FastAPI lee el multipart antes de resolver las dependencias, así que sin esto un anónimo podía mandar gigas. En `/v1/branding/*`, un `Content-Length` > 2,5 MB (o inválido) se rechaza con 413 `FILE_TOO_LARGE` sin leer el cuerpo; sin `Content-Length` (chunked) se cuentan los bytes y al pasar el tope se corta la lectura y la respuesta se reemplaza por el mismo 413. |
| 4.5-17 | **La subida no retiene una conexión durante la I/O.** La sesión del request no admite seguir después de un `commit` (está dentro de `session.begin()`), así que: se autentica; se hace `commit` de esa sesión (devuelve la conexión al pool) y no se usa más; Pillow corre en el threadpool; la clave se guarda en una transacción corta propia (`get_session_factory`, dependency nueva en `persistence/db/session.py` que los tests apuntan a la sesión del test); recién entonces se sube a R2 sin conexión tomada; si R2 falla, otra transacción corta vuelve al logo anterior (salvo que otro pedido ya lo haya cambiado) y se responde 503. Costo: durante la subida (segundos) `logo_url` apunta a un objeto que todavía no existe. `suggest-palette` también suelta la conexión antes de decodificar. |

---

## 11.9 Adenda del PR 4.6 — wizard de alta (2026-11-10)

Completa §4 (fila 4.6), §6 y §7; donde choque, manda esta subsección. Usa
`tenant_settings.onboarding_completed_at`, que existe desde la `0005`; la única migración,
`0009_onboarding_existentes` (sale de `0008_catalogo_vehiculos`), es de datos (4.6-8).

### Endpoints

| Ruta | Quién | Qué hace |
|---|---|---|
| `GET /onboarding/status` | OWNER | `completed`, `completed_at`, `resume_step`, `steps[]` (`step`, `required`, `done`) y `missing[]` |
| `POST /onboarding/complete` | OWNER | exige los requisitos de §6 y fija `onboarding_completed_at`; responde el mismo estado |

Pasos (`OnboardingStep`, en el orden de §6): `BUSINESS`, `BRANDING`, `SIZES`, `SERVICES`, `SCHEDULE`
(puestos y horarios), `PAYMENTS`, `DEPOSIT`, `STAFF`. Requisitos (`OnboardingRequirement`) y su paso:
`VEHICLE_SIZE` → `SIZES`; `SERVICE` y `SERVICE_PRICES` → `SERVICES`; `RESOURCE` y `BUSINESS_HOURS` →
`SCHEDULE`; `PAYMENT_METHOD` → `PAYMENTS`. Vocabulario y reglas puras en `app/domain/onboarding.py`.

### Decisiones tomadas al construir

| # | Decisión |
|---|---|
| 4.6-1 | **Formato del 409**: el envelope de siempre más una lista, `{"detail": {"code": "ONBOARDING_INCOMPLETE", "message": …, "missing": [{"requirement", "step", "service_ids"}]}}` (código de error nuevo). `missing` tiene la misma forma en el 409 y en `GET /onboarding/status`. `service_ids` solo viene lleno en `SERVICE_PRICES`: los servicios vivos sin precio vivo para algún tamaño vivo (A8). El OpenAPI lo declara como `OnboardingIncompleteResponse`; `ApiError` suma el parámetro `extra` para los campos propios de un error. |
| 4.6-2 | **Solo cuenta lo vivo.** Un tamaño, servicio, precio, puesto, franja o medio anulado no cumple nada; un precio que apunta a un tamaño anulado no suma ni falta. **Medio de cobro** = medio vivo con `for_income = true`: uno solo de egresos no alcanza. Los servicios `A_COTIZAR` también necesitan su precio base (D4-6). |
| 4.6-3 | **Idempotente y sin vuelta atrás**: con el alta ya terminada, `complete` responde 200 con la fecha original aunque después falte algo (anular el último puesto no "des-termina" el alta; el estado sí lo informa en `missing`). La fila de `tenant_settings` se toma con `FOR UPDATE` y los requisitos se cuentan con ella tomada: dos "Terminar" a la vez quedan con **una** fecha (test contra Postgres). |
| 4.6-4 | **Reanudación sin columna de progreso.** Un paso con requisito está hecho si no le falta ninguno. Uno opcional (negocio, personalización, seña, empleados) está hecho si guardó algo propio (un dato de contacto; paleta, fuente o logo distintos del default; la seña prendida; un empleado vivo) **o** si algún paso posterior tiene datos. `resume_step` = el primer paso no hecho (todos hechos → `STAFF`, desde donde se termina). Así un negocio recién creado arranca en `BUSINESS`, quien ya cargó tamaños no vuelve al negocio, y un obligatorio incompleto nunca se saltea aunque haya datos después. Costo aceptado: un opcional que se dejó como venía y sin nada posterior se vuelve a ofrecer (no se pierde nada). |
| 4.6-5 | **El front reusa las pantallas de Configuración, Catálogo y Equipo** (`BusinessTab`, `BrandingTab`, `VehicleSizesTab` + `BodyTypeSizesForm`, `TemplatesTab` + `ServicesTab`, `ResourcesTab` + `BusinessHoursTab`, `PaymentMethodsTab`, `DepositTab`, `StaffTab`). Las pestañas de formulario aceptan `submitLabel` y `onSaved` ("Guardar y seguir" guarda y avanza); las listas guardan en cada alta y el paso tiene "Siguiente", que relee el estado y **no avanza** si el paso obligatorio sigue incompleto (muestra qué falta). Qué paso es obligatorio y cuál está hecho lo dice **el servidor** (`steps[].required` y `steps[].done`; el `Stepper` acepta `done` por paso): el front solo aporta los textos. Los opcionales se pueden saltar. "Terminar" llama a `complete`; ante el 409 lleva al primer paso que falta, muestra ahí **solo lo suyo** y los otros pasos con faltantes como enlaces (la lista se conserva al ir de uno a otro). Como las pestañas reusadas no avisan al wizard, este se suscribe a la caché de React Query y relee el estado (con 250 ms de pausa) cuando se actualiza cualquier dato del negocio (subir el logo cambia "Saltar este paso" por "Seguir sin cambios"). Hasta que llega la sesión del servidor (`permissionsLoaded`) muestra "Cargando…": con el store vacío no le dice al dueño "El alta la hace el dueño", y el primer render coincide con el del servidor. Al terminar se invalida `["auth", "me"]` y se marca el store. El logo es opcional y su 503 lo maneja la pestaña de 4.5 (sugiere la paleta igual). |
| 4.6-6 | **Redirección desde el layout protegido** con `onboarding_completed` de la sesión (`/auth/me`, login, registro): el `OWNER` sin alta va a `/onboarding` desde cualquier pantalla del shell, y con el alta terminada `/onboarding` lo devuelve al inicio; antes de que llegue la sesión no se redirige. Un **`STAFF` no se redirige**: ve un aviso arriba de cada pantalla mientras el alta no termine, y si entra a `/onboarding` ve "El alta la hace el dueño" (los endpoints le dan 403). El middleware del edge no cambia: no ve la base. |
| 4.6-7 | **Los pasos siguen editables después del alta** desde Configuración, Catálogo y Equipo; el wizard no tiene estado propio que mantener. Agregar un tamaño después deja servicios sin precio (§6): `GET /onboarding/status` los lista en `SERVICE_PRICES`, pero el alta ya terminada no cambia. |
| 4.6-8 | **Negocios que ya existían: migración `0009_onboarding_existentes`.** Sin ella, todo negocio anterior al PR (staging incluido) quedaría encerrado en el wizard con `onboarding_completed_at` en `NULL`. La `0009` marca `now()` **solo** a los que ya cumplen los requisitos de §6, con la misma regla que el servicio (solo filas vivas; medio con `for_income`); los demás pasan por el wizard, que reanuda donde estén. Solo toca filas en `NULL` (idempotente; una fecha previa no se pisa). Corre con `NO FORCE`/`FORCE` sobre las siete tablas que lee o escribe, como 0007 y 0008. **Downgrade no-op**: la fecha es verdadera y no hay marca que distinga lo que puso la migración de un "Terminar" real; volverla a `NULL` mandaría al wizard a negocios que ya operan. **`seed_staging.py`** cierra el alta de cada negocio sembrado con `OnboardingService.complete()` (el catálogo del seed cumple: tamaños, servicios con precio para cada uno, puestos, franjas y "Efectivo" de cobro); si algún día no cumpliera, lo dice y sigue. Tests contra Postgres: `test_migracion_0009_pg.py` y `test_seed_staging.py`. |
| 4.6-9 | **Una sola regla de cobertura de precios.** El alta usa `ServiceCatalogService.price_coverage()`, que arma el resultado con los mismos `_live_sizes`, `_prices_by_service` y `_missing_sizes` que dan `missing_price_size_ids`. El resto de los requisitos (puesto, franja, medio de cobro, empleado) va en **un** `SELECT` de `EXISTS` (`_any(model, *where)`): `GET /onboarding/status` hace 6 `SELECT` en total (sesión, configuración, tamaños, servicios, precios y los `EXISTS`), y un test fija el tope. |
