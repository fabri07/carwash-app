# Manifiesto de portado — Fase 2

**Fecha:** 2026-09-15 · **Fuente:** `~/dev/vektor/Vektor/` (**solo lectura**, no se modifica nada ahí)
**Destino:** `/Users/fabriziosola/carwash.app`, rama `fase-2-infraestructura`

Todas las rutas de origen fueron verificadas con `ls` / `wc -l` sobre el árbol real de Véktor. El número
entre paréntesis es la cantidad de líneas del archivo, para dimensionar el trabajo.
En la columna **Destino**, `igual` quiere decir *la misma ruta relativa, dentro de este repositorio*.

## Cómo se lee

**Acción:**
- `COPIAR` — entra tal cual. Solo cambian nombres (imports, `vektor` → `carwash`) y el encabezado.
- `ADAPTAR` — la columna de detalle dice **exactamente** qué se saca, qué se agrega y qué se conserva.
  Lo que no está listado como cambio, no cambia.
- `REESCRIBIR` — la idea de Véktor sirve, el código no. Se usa como referencia, no como base.
- `NO SE PORTA` — está acá para que nadie lo traiga "por las dudas", con el motivo.

**Dueño:** `backend`, `frontend` o `deploy` — los tres agentes de T2.

## Reglas de propiedad

1. **Ningún archivo tiene dos dueños.** Si no está en este manifiesto y hace falta, se crea en el
   directorio del dueño que corresponda; si cae en zona gris, se resuelve en T3, no improvisando.
2. **Territorios por defecto:**
   - `backend` → todo `backend/**` **excepto** lo que este manifiesto asigna a `deploy`.
   - `frontend` → todo `frontend/**` **excepto** lo que este manifiesto asigna a `deploy`.
   - `deploy` → `.github/**`, la raíz del repo, `backend/Dockerfile`, `backend/railway.toml`,
     `backend/scripts/**`, `vercel.json`.
3. **`backend/scripts/` es de `deploy`, entero.** El backend no crea esa carpeta. Lo que el backend
   necesite correr como comando va en `backend/app/cli/`.
4. **`docs/**` no es de nadie en T2.** Es de T1 (esto) y T4.

### Conflictos resueltos, con nombre

Los seis casos donde dos agentes necesitan el mismo archivo. La decisión es del creador; el otro **lo
consume y no lo edita**. Si necesita un cambio, lo pide en T3.

| Archivo | Crea y edita | Consume, no edita | Por qué |
|---|---|---|---|
| `backend/pyproject.toml` | `backend` | `deploy` (CI lo invoca) | Es el manifiesto del paquete. La config de ruff/mypy/coverage vive ahí y el piso de cobertura se declara **solo** ahí (ADR-0008). |
| `Makefile` (raíz) | `deploy` | `backend`, `frontend` | El roadmap se lo asigna a `deploy`. Es la fachada de comandos; no contiene lógica propia. |
| `backend/openapi.json` | `backend` (`make openapi`) | `frontend` (`npm run gen:api`) | El contrato lo produce quien lo implementa. |
| `frontend/src/types/api.generated.ts` | `frontend` | nadie | Generado desde el artefacto anterior. El paso de CI que detecta deriva es de `deploy`. |
| `docker-compose.yml` (raíz) | `deploy` | `backend` (`make dev`) | Infraestructura local. |
| Cabeceras HTTP | `frontend` en `next.config.ts` (CSP, `no-store` de `/sw.js`) · `backend` en `main.py` (security headers de la API) · `deploy` en `vercel.json` **solo** proyecto (regiones, build, rewrites) | — | Tres archivos pueden mandar cabeceras. Para que no se pisen, cada uno manda las suyas y `vercel.json` no manda ninguna de aplicación. |

---

## Backend — dueño: `backend`

### Núcleo de persistencia (la parte más cara de redescubrir)

| Origen (Véktor) | Destino (carwash.app) | Acción | Dueño |
|---|---|---|---|
| `backend/app/application/services/_savepoint.py` (140) | `backend/app/persistence/db/_savepoint.py` | **COPIAR** — **la pieza #1.** Cambia solo la ubicación del módulo (es utilidad de DB, no de servicios) y los imports. Se conservan intactos `guarded_savepoint` (104-140) con el orden flush-fuera/savepoint/flush-adentro, y `unique_violation_classifier` (61-88) con la doble clasificación PG (`orig.constraint_name`) / SQLite (texto `UNIQUE constraint failed:`). **No tocar el orden de las operaciones**: el docstring 5-32 explica que `begin_nested()` flushea antes de crear el SAVEPOINT incluso con `autoflush=False`, y que en SQLite el bug es benigno — o sea, la suite verde no lo detecta. | `backend` |
| `backend/app/persistence/db/base.py` (57) | `backend/app/persistence/db/base.py` | **ADAPTAR** — se conservan `PGJSONB`/`PGTEXTARRAY` con `.with_variant()` (16-19), `TimestampMixin` (28) y `UUIDPrimaryKeyMixin` (50). **Se agrega** `naming_convention` a la `MetaData` (Véktor no tiene: `grep -rn naming_convention app/` → cero), sin la cual los nombres de CHECK y UNIQUE no son deterministas y los tests de ADR-0003/0004 no pueden afirmarlos. | `backend` |
| — (no existe en Véktor) | `backend/app/persistence/db/mixins.py` | **REESCRIBIR** — `TenantMixin` (ADR-0001) y `VoidableMixin` (ADR-0003). Es el archivo que Véktor no tiene y por eso repite `tenant_id` 51 veces en 37 archivos. | `backend` |
| `backend/app/persistence/db/engine.py` (27) | igual | **COPIAR** | `backend` |
| `backend/app/persistence/db/session.py` (31) | igual | **ADAPTAR** — se agrega el `SET LOCAL app.tenant_id = :tid` dentro de la transacción antes del `yield` (ADR-0002). Es la línea que Véktor nunca escribió. | `backend` |
| `backend/app/persistence/db/alembic_url.py` (61) | igual | **COPIAR** — `resolve_sync_url` (27-47) con la prioridad `DATABASE_URL_SYNC` > `DATABASE_URL`. Existe para que `env.py` y el preflight no puedan divergir; se porta entero por esa razón. | `backend` |
| `backend/app/application/db/tenant_context.py` (22) | `backend/app/persistence/db/tenant_context.py` | **ADAPTAR** — `SET app.current_tenant_id` (16-21) pasa a `SET LOCAL app.tenant_id`. Sin `LOCAL` el valor sobrevive al request y viaja con la conexión del pool al siguiente usuario. Y acá **sí se llama**: en Véktor `rg set_tenant_context` solo encuentra su docstring (`:13`) y un comentario (`middleware/tenant.py:16`). | `backend` |
| `backend/app/persistence/db/redis.py` (37) **+** `backend/app/persistence/db/redis_client.py` (17) | `backend/app/persistence/db/redis.py` | **ADAPTAR** — Véktor tiene **dos** `get_redis` distintos (uno con pool global, otro por request) y usa cada uno en lugares distintos. Se unifica en uno: pool global + dependency que no cierra el pool. | `backend` |
| `backend/app/persistence/repositories/base.py` (72) | igual | **ADAPTAR** — se conserva el `tenant_id` obligatorio en toda firma (`get_by_id` 30-37, `list_by_tenant` 39-51, `count_by_tenant` 62-72). **Se agrega:** filtro `voided_at IS NULL` por defecto con `include_voided=False` (ADR-0003), y `list_by_tenant` devuelve `(items, total)` para que el endpoint no pueda dar uno sin el otro (ADR-0006). **Se sacan** los `# type: ignore[attr-defined]` (33-34, 47, 69) acotando `ModelT` a un Protocol con `id`/`tenant_id`, posible recién ahora que la PK siempre se llama `id` (ADR-0004). | `backend` |
| `backend/alembic.ini` (48) | igual | **COPIAR** — con `script_location = app/persistence/migrations`. | `backend` |
| `backend/app/persistence/migrations/env.py` (68) | igual | **ADAPTAR** — se conservan `resolve_sync_url` (19-22) y `compare_type=True` en los dos modos (38, 48). **Se agrega** `compare_server_default=True` y la `naming_convention`. | `backend` |
| `backend/app/persistence/models/__init__.py` (123) | igual | **REESCRIBIR** — el patrón (importar todos los modelos para que el autogenerate los vea) se conserva; la lista es de este proyecto. | `backend` |
| `backend/app/persistence/models/user.py` (referencia) | `backend/app/persistence/models/{tenant,user,dummy_resource}.py` | **REESCRIBIR** — tres modelos, cero dominio de lavadero. `dummy_resource` existe solo para que el test cruzado de T3 tenga sobre qué correr. `user.py:21` (PK `user_id`), `:23` (`tenant_id` a mano) y `:32` (`role_code` como `Text`) son exactamente lo que **no** se copia. | `backend` |
| — | `backend/app/domain/roles.py`, `backend/app/domain/void.py` | **REESCRIBIR** — `Role` y `VoidReason` como `StrEnum` nativos (ADR-0005, ADR-0003). Referencia de qué **no** hacer: `backend/app/domain/user.py:13-17`, un Enum en minúsculas que nadie usa y que no incluye `SUPERADMIN`. | `backend` |
| — | `backend/app/persistence/migrations/versions/0001_*.py` | **REESCRIBIR** — migración inicial. Las políticas RLS se crean acá, para toda tabla con `TenantMixin`, con `ENABLE` + `FORCE` + `USING` + `WITH CHECK`. Referencia de forma (no de contenido): `…/versions/20260401_0001_security_fase0_rls_pending_actions_cipher.py:114,121-122`, que crea `USING` **sin** `WITH CHECK`. | `backend` |

### Aplicación y API

| Origen (Véktor) | Destino (carwash.app) | Acción | Dueño |
|---|---|---|---|
| `backend/app/main.py` (318) | igual | **ADAPTAR** — **se conservan:** `SlowAPIMiddleware` (77), `CORSMiddleware` (83-99), `security_headers` (102-115), `request_logger` (118-157), handlers de `RequestValidationError` (165-191) y `ValidationError` (193-203), catch-all a Sentry (240-261), `/health` (264-266) y `/ready` (268-283) con `_check_database_ready` (288-301) y `_check_redis_ready` (304-315). **Se sacan:** `TenantMiddleware` (80) — su trabajo lo hace el `SET LOCAL` de `session.py` — y los handlers de dominio `InsufficientStockError` (205-222) y `SaleProductNotFoundError` (224-238). **Se agrega:** `/health` devuelve `{status, env, commit, db_fingerprint}` (ADR-0012) y la comprobación de `Origin` en métodos mutadores (ADR-0009). | `backend` |
| `backend/app/bootstrap.py` (145) | igual | **ADAPTAR** — el arranque tolerante (uvicorn levanta aunque DB o Redis estén caídos, para que `/health` responda y Railway no cicle). Se saca lo de Celery. | `backend` |
| `backend/app/api/v1/deps.py` (306) | igual | **ADAPTAR** — **se conservan:** `get_current_user` (37) con sus tags de Sentry (`set_tag("tenant_id", …)` en 72 y `set_user` sin email en 73), `get_current_tenant_id` (78), `client_ip` (125-194) y `rate_limit_key` (197-205). **`client_ip` se porta entero y sin tocar:** resuelve un bug real —detrás del edge de Railway, `get_remote_address` devolvía siempre la IP del edge y el `5/hour` era un techo **global**, no por visitante (comentario 41-46 y docstring 167)—, confía en `X-Real-IP` **solo** en producción (189) y avisa una vez por proceso si falta (139-156). **Se cambia:** el token se lee de la cookie, no del header `Authorization` (ADR-0009); `require_role` (100) se retipa `*roles: Role` (ADR-0005). **Se sacan:** `get_current_tenant` (83), `require_open_registration` (208), el step-up con PIN (`_require_pin_window` 236, `require_modify_access` 246, `require_owner_stepup` 264) y `ensure_tenant_not_under_maintenance` (286). | `backend` |
| `backend/app/utils/security.py` (73) | igual | **COPIAR** — JWT HS256 (`encode` 39, `decode` 66-68), bcrypt con `rounds=12` (18) y la validación del claim `type` (44, 51, 69) que impide usar un refresh token como access token. 73 líneas, entra tal cual. | `backend` |
| `backend/app/utils/datetime_utils.py` (33) | igual | **COPIAR** — `utcnow()` con `UTC` explícito. | `backend` |
| `backend/app/utils/pagination.py` (17) | `backend/app/api/v1/pagination.py` | **ADAPTAR** — se conserva el clamp `[1, 200]` (12) y se convierte el dataclass en una dependencia de FastAPI (`Annotated[PaginationParams, Depends()]`), que es lo que lo vuelve obligatorio. En Véktor este archivo es **código muerto**: `rg "PaginationParams" app` devuelve una sola línea, su propia definición. | `backend` |
| `backend/app/application/services/idempotency.py` (97) | `backend/app/application/idempotency.py` | **ADAPTAR** — se conserva el mecanismo: INSERT directo sin SELECT previo, confiando en el unique index para la carrera, dentro de `guarded_savepoint` (46-76). **Se cambia:** tabla propia `idempotency_keys` en vez de reusar `operation_fingerprints` con prefijo `idem:` (33, 42-43), que era un apaño para compartir tabla con el dedup del agente LLM. | `backend` |
| `backend/app/api/v1/sales.py:216,223` (patrón) | `backend/app/api/v1/errors.py` | **REESCRIBIR** — helper único para `Header("Idempotency-Key")` y para el `409 {"code": "DUPLICATE_IDEMPOTENT"}`. En Véktor el `raise HTTPException(409, …)` está copiado **11 veces** a mano (`sales.py:223,305,422`, `expenses.py:190,251`, `products.py:373`, `purchases.py:48`, `suppliers.py:170,286`, `customers.py:166`). El contrato exacto del 409 importa: `useOfflineSubmit` lo trata como éxito. | `backend` |
| `backend/app/schemas/common.py` (43) | igual | **ADAPTAR** — se conservan `PaginatedResponse[T]` (21-29) y `CamelModel` (12-18). **Se activan** `ErrorDetail` (36-39) y `ErrorResponse` (42-43), que en Véktor están definidos y **no se usan en ningún archivo**, y se agrega un `ErrorCode` cerrado (ADR-0013). | `backend` |
| `backend/app/api/v1/auth.py` (351) | igual | **REESCRIBIR** — registro, login, refresh, logout, `/me`. Se toma la forma (schemas, códigos de estado, refresh con rotación) y se descarta todo lo de Véktor: OAuth de Google, verificación de email, solicitudes de acceso, definición de contraseña. Emite cookies `HttpOnly`, no tokens en el body (ADR-0009). | `backend` |
| `backend/app/api/v1/router.py` (89) | igual | **REESCRIBIR** — dos routers: `auth` y `dummy_resources`. | `backend` |
| — | `backend/app/api/v1/dummy_resources.py` | **REESCRIBIR** — CRUD mínimo sobre el recurso dummy, con `PaginatedResponse` (ADR-0006) e `Idempotency-Key` en el POST. Es el sujeto del test cruzado de T3. | `backend` |
| `backend/app/config/settings.py` (575) | igual | **REESCRIBIR** — se conserva la forma (`pydantic-settings`, `get_settings()` cacheado, la property `is_production` de la que depende `client_ip:189`). Se descartan las 575 líneas de flags de rollout de Véktor; quedan ~20 variables. | `backend` |
| `backend/app/observability/logger.py` (149) | igual | **COPIAR** — `configure_logging` (32-83) con JSON en prod y consola en dev, `bind_request_context` (90-113) con `tenant_id`/`user_id`/`trace_id` en contextvars. Nombres genéricos, cero dominio. | `backend` |
| `backend/app/observability/sentry.py` (166) | igual | **ADAPTAR** — **se conservan:** `init_sentry` (136) con el no-op si no hay DSN (139-143), `_scrub_event` (64-95) que limpia headers, query string, vars del stacktrace, breadcrumbs y extra, y `_traces_sampler` (107-133) con la comparación **exacta** de path para excluir `/health` y `/ready` (el comentario 111-115 documenta el bug de usar `in`, que mataba el sampling de rutas de negocio). **Se cambia el vocabulario del scrubbing:** `_SENSITIVE_KEY_RE` (40-45) pierde `cuit`, `monto`, `supplier_name`, `nombre_proveedor` y gana `patente`, `dominio`, `telefono`, `nombre_cliente`; `_CUIT_VALUE_RE` (48) se reemplaza por `_PATENTE_VALUE_RE` (`AA123BB` y `AAA123`) — **la patente es el PII fuerte de este dominio, el equivalente al CUIT en Véktor**; `_DNI_VALUE_RE` (49) se conserva. | `backend` |
| `backend/app/observability/trace.py` (22) | igual | **COPIAR** | `backend` |
| `backend/app/observability/metrics.py` (172) | — | **NO SE PORTA** — fuera del alcance de la Fase 2. | — |
| `backend/pyproject.toml` (config de tools) | igual | **ADAPTAR** — se conservan `[tool.ruff]` (1-4), `[tool.ruff.lint]` con `select`/`ignore` (6-11), los `per-file-ignores` (13-15), `[tool.mypy] strict = true` (17-21) y los overrides de terceros (23-25) y de `migrations` (40-43). **Se agrega:** `[tool.ruff.format]` (ADR-0007), `requires-python = ">=3.12,<3.13"` (Véktor no tiene sección `[project]` en absoluto) y `[tool.coverage.report] fail_under = 80`, que es el **único** lugar donde vive el número (ADR-0008). | `backend` |
| `backend/requirements.txt` (68) + `requirements-dev.txt` (27) | `backend/pyproject.toml` `[project.dependencies]` + `backend/uv.lock` | **REESCRIBIR** — `uv` está disponible en el entorno; el lock reemplaza a los dos `requirements`. Sin Celery, sin Redis-como-broker, sin OCR, sin OpenAI/Anthropic. | `backend` |
| `backend/.env.example` (81) | igual | **ADAPTAR** — se conserva el formato; quedan las variables de la Fase 2. **Nunca** apunta a una base remota (ver el caso de `docker-compose.override.yml` más abajo). | `backend` |
| — | `backend/app/cli/dump_openapi.py` | **REESCRIBIR** — vuelca `create_app().openapi()` a `backend/openapi.json` (ADR-0013). | `backend` |
| — | `backend/openapi.json` | **REESCRIBIR** (generado, versionado) — lo produce `backend`, lo consume `frontend`. | `backend` |
| `backend/scripts/verify_rls.py` (62) | `backend/app/tests/security/test_rls_politicas_pg.py` | **REESCRIBIR** — la idea (comprobar que RLS está puesto) sirve; la implementación no: solo mira `pg_tables.rowsecurity` (45-46), se corre a mano contra prod y **daría verde en Véktor hoy**, con el contexto de tenant sin setear. Pasa a ser un test de pytest con marca `postgres` que además verifica `FORCE` y el `WITH CHECK` (ADR-0002). | `backend` |

### Tests

| Origen (Véktor) | Destino (carwash.app) | Acción | Dueño |
|---|---|---|---|
| `backend/app/tests/conftest.py` (583; núcleo 107-497) | igual | **ADAPTAR** — **la pieza que convierte una suite de horas en una de minutos, se porta con cuidado.** **Se conservan textualmente:** `FakeRedis` (54-104) con TTL por `time.monotonic()`; `db_engine` session-scoped (128-150) con SQLite in-memory + `StaticPool` y **el truco de aiosqlite** (135-145: `dbapi_connection.isolation_level = None` en el evento `connect` + `BEGIN` explícito en el evento `begin`) sin el cual la emulación de transacciones de sqlite3 comitea sola y el rollback por test no funciona; `db_session` (153-167) con transacción externa + `AsyncSession(join_transaction_mode="create_savepoint")` y `trans.rollback()` final; `isolated_db_engine` (232-245) para los tests que necesitan commits reales; el override de bcrypt a `rounds=4` (35-41); `celery_eager`; `client` (472-497); y los fixtures de bytes de archivo (502-583). **Se sacan:** `_pin_window_key`/`TEST_PIN` (43-51), `_f5_unique_index_names`/`legacy_pre_f5_schema` (170-229, atados a una migración concreta de Véktor) y `mock_score_trigger` (461-466). **Se reescriben** los fixtures de dominio (251-443: `sample_tenant`, `sample_user`, `auth_headers`, `second_tenant`, `second_auth_headers`, `viewer_headers`) como `tenant_a`/`tenant_b`, `owner`/`staff` y **cookies** en vez de headers (ADR-0009). El par `second_tenant` + `second_auth_headers` es el andamio del test cruzado: se porta la idea entera. | `backend` |
| — | `backend/app/tests/conftest_pg.py` | **REESCRIBIR** — engine y sesión contra **Postgres real**, marca `postgres`, con el rol `carwash_app` (no dueño, sin `BYPASSRLS`). **SQLite no tiene RLS**, así que los tests de ADR-0002 no pueden vivir en la suite rápida. El servicio de Postgres ya existe en el CI (`.github/workflows/ci-backend.yml:27-40`). | `backend` |
| `.github/workflows/ci-backend.yml:137-172` (referencia) | `backend/pyproject.toml` marcador `postgres` | **ADAPTAR** — Véktor corre los tests marcados `postgres` en un paso aparte y **secuencial** (`-n 0`), con una nota extensa sobre por qué no se pueden paralelizar. Misma decisión acá. | `backend` |
| — | `backend/app/tests/meta/*.py` | **REESCRIBIR** — los verificadores de ADR-0001, 0003, 0004, 0006, 0007, 0008 y 0013. Son los que convierten estas ADRs en algo comprobable en vez de una intención. | `backend` |

---

## Frontend — dueño: `frontend`

| Origen (Véktor) | Destino (carwash.app) | Acción | Dueño |
|---|---|---|---|
| `frontend/src/lib/api.ts` (149) | igual | **ADAPTAR** — **se conserva lo caro:** el refresh **single-flight** (`let _refreshPromise` en 15, reutilizado en 117-131, limpiado en el `.finally()` 129-131) que hace que N respuestas 401 simultáneas disparen **un** refresh y no N; el `X-Trace-Id` por request con `crypto.randomUUID()` (25-27) y su uso en breadcrumbs de Sentry (31, 62, 75); el logout ante 401 sin refresh (109-111). **Se cambia:** `withCredentials: true` y se elimina el header `Authorization` armado desde el store (19-21) — los tokens ya no los ve el JS (ADR-0009); el refresh se dispara por el 401 de la API, no por el `exp` leído en el cliente. **Se saca:** el step-up con PIN (428 `PIN_REQUIRED`, 82-98). | `frontend` |
| `frontend/src/stores/authStore.ts` (78) | igual | **ADAPTAR** — se conserva la forma (zustand + `persist`, `partialize`). **Se saca del estado persistido `token` y `refreshToken`** (16-17, 63-69): quedan en cookie `HttpOnly`. En `localStorage` solo sobrevive el `user` para pintar la UI sin parpadeo. Es el cambio que vuelve honesto al `middleware.ts`. | `frontend` |
| `frontend/src/components/auth/AuthHydrationBoundary.tsx` (21) | igual | **ADAPTAR** — 21 líneas; el patrón (no renderizar hasta que zustand rehidrató, para evitar el mismatch de SSR) se conserva entero. Lo que hidrata ya no es el token, es el usuario. | `frontend` |
| `frontend/middleware.ts` (31) | igual | **REESCRIBIR** — el de Véktor es un no-op declarado: arma `PROTECTED_PATHS` (4-12), calcula `isProtected` (16) y devuelve `NextResponse.next()` igual (26), con el `TODO` en 20-24 explicando por qué. Acá redirige de verdad (ADR-0009). Se conserva el `matcher` (29-31). | `frontend` |
| `frontend/src/lib/queryClient.ts` (15) | igual | **COPIAR** | `frontend` |
| `frontend/src/stores/offlineQueueStore.ts` (45) | igual | **COPIAR** — **la pieza #2.** Store zustand `persist` con `QueuedItem[]` (`id` usado como `Idempotency-Key`, `kind`, `payload`, `attempts`, `lastError`) y `version: 1`. Solo cambia la clave de storage. | `frontend` |
| `frontend/src/features/ingestion/useOfflineSubmit.ts` (240) | `frontend/src/hooks/useOfflineSubmit.ts` | **ADAPTAR** — **se conserva la lógica entera, que es el valor:** `isNetworkError` (82-84, `isAxiosError && !e.response`) vs `isClientError` (87-90, 400-499) vs el resto tratado como 5xx transitorio (175-183); en `flush`, la red **corta el loop sin gastar intento** (159-162) mientras 4xx/5xx incrementan `attempts` y descartan a los 5 (`MAX_FLUSH_ATTEMPTS`, 93, 170-183) para no quedarse con ítems veneno; y `isDuplicate` (96-100) que trata el `409` + `detail.code === "DUPLICATE_IDEMPOTENT"` **como éxito** (156-158 en flush, 203-205 en submit) — es el contrato que el helper del backend tiene que respetar. **Se cambia:** sale de `features/ingestion/` (dominio de Véktor) a `hooks/`, y se le saca el acoplamiento a las queries de ingestión. | `frontend` |
| `frontend/src/app/(protected)/layout.tsx` (70) | igual | **ADAPTAR** — app shell. Se saca el `EconomicTicker` y la lógica de rutas de dashboard de Véktor. | `frontend` |
| `frontend/src/components/layout/Sidebar.tsx` (286) | igual | **REESCRIBIR** — 286 líneas atadas a las 13 rutas de Véktor. Se toma la estructura (colapsable, responsive, item activo) y se reconstruye sobre shadcn `sheet` para móvil (ADR-0010). | `frontend` |
| `frontend/src/components/layout/Header.tsx` (134) | igual | **ADAPTAR** — se conserva el menú de usuario y el logout; se saca lo de Véktor. | `frontend` |
| `frontend/src/stores/toastStore.ts` (26) | igual | **ADAPTAR** — se conserva el **contrato** (tipos, duración, cola). La presentación pasa a `sonner` (ADR-0010). | `frontend` |
| `frontend/src/components/ui/Toast.tsx` (132) | — | **NO SE PORTA** — lo reemplaza `sonner` de shadcn. El contrato sobrevive en `toastStore`. | — |
| `frontend/src/components/ui/EmptyState.tsx` (86) | `frontend/src/components/ui/empty-state.tsx` | **ADAPTAR** — no tiene equivalente en shadcn y el dashboard vacío del checkpoint lo necesita. | `frontend` |
| `frontend/src/components/ui/` — los otros 18 | — | **NO SE PORTA** — `Button`, `Input`, `Select`, `Modal`, `Tabs`, `Tooltip`, `Table`, `Card`, `Badge`, `SmartTable`, `StatCard`, `TableSearch`, `PeriodFilter`, `PinGateModal`, `UploadSizeHint`, `VektorLogo`, `VerticalIcons`, `index.ts`. Los reemplaza shadcn/ui (ADR-0010); `Modal`, `Select`, `Tabs` y `Tooltip` son justamente donde Radix resuelve lo difícil (foco, `Escape`, `aria-*`). | — |
| `frontend/src/app/error.tsx` (62), `global-error.tsx` (68), `loading.tsx` (32) | igual | **ADAPTAR** — error boundaries de App Router con reporte a Sentry. Se cambia el texto y la marca. | `frontend` |
| `frontend/src/app/layout.tsx` (66) | igual | **ADAPTAR** — se agregan el `<link rel="manifest">`, `apple-touch-icon` y las meta de iOS (ADR-0011), y el registro del service worker detrás de `NEXT_PUBLIC_SW_ENABLED`. | `frontend` |
| `frontend/src/lib/sentryScrub.ts` (97) | igual | **ADAPTAR** — mismo cambio de vocabulario que el `sentry.py` del backend: fuera CUIT, dentro patente. Los dos scrubbers tienen que coincidir o el PII se filtra por el lado que quedó viejo. | `frontend` |
| `frontend/src/app/(public)/login/`, `register/` | igual | **REESCRIBIR** — dos formularios con `react-hook-form` + `zodResolver` (ADR-0010). Véktor los tiene con `safeParse` a mano llamado tres veces en el mismo componente (`features/auth/LoginForm.tsx:62,:97,:128`). | `frontend` |
| `frontend/src/validation/auth.ts` (3-5) | igual | **ADAPTAR** — se conserva el schema de zod; pasa a usarse vía resolver y a ser la fuente del tipo con `z.infer`. | `frontend` |
| `frontend/src/services/auth.service.ts` | igual | **ADAPTAR** — se conserva el patrón de un servicio HTTP por dominio; los tipos salen del generado (ADR-0013). | `frontend` |
| `frontend/src/types/api.ts` (390) | `frontend/src/types/api.generated.ts` + `frontend/src/types/api.ts` (alias) | **REESCRIBIR** — las 390 líneas escritas a mano (`:21` `TokenResponse`, `:28` `AuthUserResponse`, `:38` `UserResponse`, `:44` `MeResponse`, `:58` `AuthResponse`, y ~24 más) se reemplazan por el archivo generado con `openapi-typescript`. `api.ts` queda solo con alias cortos y con tipos que **no** vienen de la API. | `frontend` |
| `frontend/jest.config.ts` (15) | igual | **ADAPTAR** — se conserva `nextJest`, `testEnvironment: "jsdom"`, el `moduleNameMapper` de `@/` (8) y `modulePathIgnorePatterns` de `.next` (10). **Se agrega** `coverageThreshold.global` en 80 para las cuatro métricas (ADR-0008); Véktor no tiene ninguno. | `frontend` |
| `frontend/jest.setup.ts` (4) | igual | **COPIAR** — incluido el `scrollIntoView` que jsdom no implementa. | `frontend` |
| `frontend/next.config.ts` (33) | igual | **ADAPTAR** — se agregan las cabeceras de la app: CSP y `Cache-Control: no-store` para `/sw.js` (ADR-0011). | `frontend` |
| `frontend/tailwind.config.ts` (115) | igual | **ADAPTAR** — se conserva la estructura de tokens; los valores `vektor-*` se reemplazan por los de este proyecto y se suma el preset de shadcn. | `frontend` |
| `frontend/tsconfig.json` (27) | igual | **COPIAR** — `strict`, paths `@/*`. | `frontend` |
| `frontend/package.json` (47) | igual | **ADAPTAR** — se agregan `react-hook-form`, `@hookform/resolvers`, `@radix-ui/*`, `class-variance-authority`, `tailwind-merge`, `sonner`, `openapi-typescript`, y los scripts `gen:api` y `test:cov`. | `frontend` |
| `frontend/.env.local.example` (2) | igual | **ADAPTAR** | `frontend` |
| — | `frontend/components.json` | **REESCRIBIR** — config de shadcn (ADR-0010). | `frontend` |
| — | `frontend/public/manifest.webmanifest`, `public/icons/*`, `public/sw.js` | **REESCRIBIR** — no hay nada que copiar: `frontend/public/` de Véktor tiene `.gitkeep`, `doodles/` y `screenshots/`, y `grep "next-pwa\|workbox" package.json` da cero (ADR-0011). | `frontend` |
| `frontend/src/app/(protected)/dashboard/` | `frontend/src/app/(protected)/dashboard/page.tsx` | **REESCRIBIR** — el dashboard vacío del checkpoint. Un `EmptyState` y nada más: **cero dominio de lavadero**. | `frontend` |

---

## Deploy, CI e infraestructura — dueño: `deploy`

| Origen (Véktor) | Destino (carwash.app) | Acción | Dueño |
|---|---|---|---|
| `backend/Dockerfile` (2143 B) | `backend/Dockerfile` | **ADAPTAR** — se conserva el multi-stage `builder`/`dev`/`runtime` (2, 18, 40), el usuario no-root `appuser` (40-72), el healthcheck sobre `/health` (69-70) y el `CMD ["sh", "scripts/start.sh"]` (72). **Se cambia:** `pip` → `uv`, y se saca `tesseract-ocr` de la stage `dev` (18-37). Sigue en `python:3.12-slim` — el pin es explícito (ADR-0007), porque el Python del sistema de la máquina de desarrollo es 3.14. | `deploy` |
| `backend/railway.toml` (456 B) | `backend/railway.toml` | **ADAPTAR** — se conservan `builder = "dockerfile"` (2), `preDeployCommand = "sh scripts/migrate.sh"` (9), `startCommand = "sh scripts/start.sh"` (10), `healthcheckPath = "/health"` (11) y `restartPolicyType = "on_failure"` (12). **Se agrega** el servicio de staging (ADR-0012). El `preDeployCommand` es el fail-safe: corre una vez antes de que la versión nueva reciba tráfico, y si falla, la vieja sigue sirviendo. | `deploy` |
| `backend/scripts/migrate.sh` (37) | igual | **ADAPTAR** — se conservan `set -eu` (15) y los dos primeros pasos: preflight (19) y `alembic upgrade head` (22). **Se saca** el paso 3 (`seed_vertical_fields.py`, 36), que es de Véktor. | `deploy` |
| `backend/scripts/migrate_preflight.py` (91) | igual | **COPIAR** — **pieza de alto valor.** Solo `SELECT`, **nunca corta el deploy** (el `try/except` de 87-91 garantiza `sys.exit(0)`): imprime de qué variable salió la URL (48), host/puerto/base sin credenciales (49), `current_database`/`current_user`/`current_schema`/`search_path` (52-61) y **todas** las tablas `alembic_version` visibles en cualquier schema, avisando si hay más de una (64-82). Resuelve la URL con el mismo `alembic_url.py` que `env.py` (35) justamente para que no puedan divergir. Existe por un incidente real donde no se podía saber contra qué base se había migrado. | `deploy` |
| `backend/scripts/start.sh` (17) | igual | **ADAPTAR** — se saca el despacho por rol (`VEKTOR_RUNTIME`/`SERVICE_TYPE`/`RAILWAY_SERVICE_NAME`, 4-6): en la Fase 2 solo hay servicio web. | `deploy` |
| `backend/scripts/start_web.sh` (10) | igual | **COPIAR** — uvicorn con `$PORT`/`$UVICORN_WORKERS` (10). **Y no corre migraciones** (comentario 7-8): eso pasó al `preDeployCommand`, para que corran una vez por deploy y no una por réplica. Esa separación se porta tal cual. | `deploy` |
| `backend/scripts/{start_worker.sh, start_beat.sh, worker_healthcheck.py}` | — | **NO SE PORTA** — no hay Celery en la Fase 2. | — |
| `backend/Procfile` (279 B) | — | **NO SE PORTA** — vestigial: el `startCommand` de `railway.toml` le gana. Mantener dos definiciones del arranque es cómo se desincronizan. | — |
| `backend/Makefile` (6812 B) | `Makefile` (raíz) | **ADAPTAR** — **se conservan:** `dev`/`dev-bg`/`stop`/`logs`/`shell` (13-26), `migrate`/`migrate-down`/`migrate-create`/`migrate-history` (29-47), `db-reset` (49-52), `test`/`test-cov`/`test-fast`/`test-watch`/`test-file` (75-88), `lint`/`fix`/`typecheck`/`check` (91-122), `build`/`clean`/`install`/`setup` (125-142). **Se saca** el target `format` que **falla a propósito** (94-110) y su hermano `format-normalize-global` (115-117): acá `make format` formatea (ADR-0007). **Se saca** el número de cobertura de `test-cov` (78-79): sale de `pyproject.toml` (ADR-0008). **Se agregan** `openapi`, `gen-api` y `check-envs` (ADR-0012, ADR-0013). | `deploy` |
| `backend/docker-compose.yml` (4514 B) | `docker-compose.yml` (raíz) | **ADAPTAR** — se conservan `postgres` (postgres:16-alpine, 2-19), `redis` (21-34) y `backend` (36-57). **Se sacan** `celery-worker` (59-92), `celery-beat` (94-120) y el servicio `test` (122-135). | `deploy` |
| `backend/docker-compose.override.yml` (1722 B) | `docker-compose.override.example.yml` | **REESCRIBIR** — **el "truco" se porta invertido, a propósito.** En Véktor el `.env` apunta a **Neon producción** y el override (gitignoreado) lo pisa con el Postgres local, aprovechando que `environment:` le gana a `env_file:`. O sea: *sin* el override, `make dev` le pega a la base de producción. Acá el default es local y **ningún archivo por defecto puede alcanzar una base remota**; el override queda como ejemplo para el caso inverso, que es el excepcional. Se conserva el aviso de la trampa secundaria (15-25): el esquema de la URL (`postgresql+asyncpg://` vs `postgresql://`) no es intercambiable entre alembic y los scripts. | `deploy` |
| `.github/workflows/ci-backend.yml` (8261 B) | igual | **ADAPTAR** — **se conservan:** triggers con `paths: backend/**` (3-13), el servicio **Postgres 16 real** (27-40) con healthcheck `pg_isready` —está ahí porque los tests de concurrencia usan primitivos que en SQLite son no-op (comentario 23-26)—, `ruff check .` (69-70), `mypy app` (72-74), pytest con cobertura (77-104), **el paso `alembic upgrade head` contra Postgres real** (110-128, con el comentario 106-109: valida la migración, no el `create_all` del ORM) y el paso secuencial `-n 0` para los tests marcados `postgres` (137-172). **Se agrega:** `ruff format --check .` **antes** del lint (ADR-0007) y los tests de RLS. **Se cambia:** caché de `uv` en vez de `pip` (54-60); el `--cov-fail-under=60` de la línea 103 **se elimina del workflow** (sale de `pyproject.toml`, ADR-0008). | `deploy` |
| `.github/workflows/ci-frontend.yml` (3277 B) | igual | **ADAPTAR** — **se conserva el orden deliberado**: Jest **primero** (46-60 — el comentario documenta un incidente real de 8 días con 9 suites en rojo sin que nadie se enterara por no tener este paso), después `tsc` (62-64), ESLint (66-68) y `next build` (70-74). **Se agrega:** el paso de deriva de tipos generados (ADR-0013) y la cobertura con umbral. | `deploy` |
| `.github/workflows/ci-mcp.yml` (4746 B) | — | **NO SE PORTA** — no hay servicio MCP. (Su `--cov-fail-under=15` de `ci-mcp.yml:94` queda como recordatorio de a dónde llega un piso que se fija donde está el código.) | — |
| — | `.github/workflows/deploy-staging.yml`, `deploy-prod.yml` | **REESCRIBIR** — no existen en Véktor: `grep -rni staging .github/ backend/railway.toml frontend/.vercel/` da cero. Staging desde `main`; prod desde tag, con GitHub Environment y revisor (ADR-0012). | `deploy` |
| — | `.pre-commit-config.yaml` (raíz) | **REESCRIBIR** — no existe en Véktor. `ruff-format`, `ruff --fix`, `end-of-file-fixer`, `trailing-whitespace`, `check-merge-conflict`, `detect-private-key`, y prettier/eslint para el frontend (ADR-0007). | `deploy` |
| — | `.python-version` (raíz) | **REESCRIBIR** — `3.12`. El Python del sistema es 3.14; sin pin, el entorno local y el de CI divergen desde el primer día. | `deploy` |
| — | `vercel.json` | **REESCRIBIR** — solo config de proyecto (build, regiones, rewrite de `/api/*` a la API). **Ninguna cabecera de aplicación**: esas van en `next.config.ts`, que es del `frontend`. | `deploy` |
| — | `backend/scripts/seed_staging.py` | **REESCRIBIR** — datos sintéticos, dos tenants de mentira. **Prohibido** restaurar un dump de producción (ADR-0012, Ley 25.326). Referencia de forma: `backend/scripts/seed_demo_data.py` de Véktor. | `deploy` |
| — | `scripts/check_envs.sh` (raíz) | **REESCRIBIR** — el verificador de ADR-0012: `/health` de staging y de prod, `env` distinto, `db_fingerprint` distinto, y el JWT de staging rechazado en prod. | `deploy` |
| `backend/pyrightconfig.json` | — | **NO SE PORTA** — `typeCheckingMode: "basic"` conviviendo con `mypy strict`. Dos verificadores de tipos con dos varas distintas es cómo se aprende a ignorar al que grita de más. | — |
| `backend/alembic/` (dir sin `versions/`) | — | **NO SE PORTA** — remanente muerto del `alembic init` original, no referenciado por `alembic.ini`. Su `env.py` tiene `target_metadata = None`. | — |
| `backend/worker/`, `backend/beat/`, `mcp_server/` | — | **NO SE PORTA** — sin Celery ni MCP en la Fase 2. | — |

---

## Lo que **no** es de T2

Estos archivos aparecen para que ningún agente de T2 los cree. Son de T3, y el `CLAUDE.md` del proyecto lo
exige: *"El tester de aislamiento es un agente distinto del implementador."*

| Archivo | Dueño |
|---|---|
| `backend/app/tests/security/test_aislamiento_tenants.py` — el test cruzado entre dos tenants sobre el recurso dummy, 404 y no 403 | **T3 · Tester-aislamiento** |
| Revisión de auth, cookies, CORS, CSP y secretos | **T3 · Revisor-adversarial** |
| `docs/adr/**` | **T1** (esto) |

## Orden sugerido de arranque

Las tres pistas son independientes salvo en un punto: el `openapi.json`. `frontend` puede empezar por el
shell, el manifiesto de PWA y los componentes de shadcn sin esperar a nadie, pero **no puede generar sus
tipos hasta que `backend` commitee el primer `backend/openapi.json`**. Ese es el único momento de
sincronización de T2, y conviene que el backend lo produzca temprano aunque el contrato todavía tenga solo
`auth` y `dummy-resources`.
