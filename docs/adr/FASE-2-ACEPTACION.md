# Criterio de aceptación — Fase 2

**Fecha:** 2026-09-15 · **Objetivo de la fase:** un *hello world* autenticado y multi-tenant, desplegado en
Railway y Vercel, con CI en verde. **Cero dominio de lavadero.**

Esta es la lista que hay que pasar para llegar al checkpoint humano (T4). Reglas:

- **Cada comprobación es ejecutable.** Si no se puede correr, no cuenta.
- **Ninguna fase cierra en rojo.** Una comprobación que falla no se "difiere": se arregla o se documenta
  por escrito como deuda aceptada, con la firma del checkpoint.
- Las comprobaciones 1 a 9 corren en CI. Las 10 a 14 corren **contra la URL desplegada**, porque hay cosas
  —cookies `Secure`, cabeceras, el manifiesto servido— que solo son ciertas en producción.
- `A1` … `A14` son los identificadores para citar en el acta del checkpoint.

---

## Parte I — Lo que tiene que pasar en CI

### A1 · Formato y lint, backend y frontend

```bash
cd backend  && uv run ruff format --check . && uv run ruff check .
cd frontend && npx prettier --check . && npm run lint
uv run pre-commit run --all-files
```

**Pasa si:** los tres comandos salen con 0. **Verifica:** ADR-0007.
El `ruff format --check` es el que importa: es el paso que Véktor nunca pudo agregar
(`.github/workflows/ci-backend.yml:69-70` corre solo `ruff check .`).

### A2 · Tipos, backend y frontend

```bash
cd backend  && uv run mypy app          # strict = true
cd frontend && npm run type-check       # tsc --noEmit, strict
```

**Pasa si:** cero errores, y **cero `# type: ignore` nuevos sin comentario de motivo**.
**Verifica:** ADR-0005 (`require_role("OWNER")` con un string literal tiene que ser un error de tipos, no
un warning), ADR-0013.

### A3 · Suite completa con el piso de cobertura en 80%

```bash
cd backend  && uv run pytest --cov=app          # fail_under sale de pyproject.toml
cd frontend && npm run test:cov                 # coverageThreshold.global = 80
```

**Pasa si:** las dos suites en verde y ninguna cae bajo 80% en statements, branches, functions y lines.
**Verifica:** ADR-0008.
**Además:** `backend/app/tests/meta/test_compuerta_de_cobertura.py` tiene que pasar — es lo que impide que
el número esté declarado en tres archivos distintos y se desincronice, y lo que ata el piso histórico de
`backend/.coverage-floor`.

### A4 · Los verificadores de las ADRs de modelo

```bash
cd backend && uv run pytest app/tests/meta/ -v
```

**Pasa si:** pasan los nueve archivos de `app/tests/meta/`. Son los que convierten las ADRs en algo
comprobable:

| Test | ADR | Qué atrapa |
|---|---|---|
| `test_modelo_tenant.py` | 0001 | una tabla sin `tenant_id`; un modelo que lo redeclara; una columna `tenant_id` con distinto nullable/índice/FK que las demás |
| `test_soft_delete.py` | 0003 | una columna `deleted_at`/`is_active`/`archived_at`; un `voided_at` sin su `void_reason`; una tabla anulable sin el CHECK de coherencia |
| `test_claves_primarias.py` | 0004 | una PK que no se llama `id`; `tenant_id` como PK; una tabla puente con atributos propios |
| `test_paginacion.py` | 0006 | un GET que devuelve `list[...]`; una colección con envelope propio; un `limit` sin el tope de 200 |
| `test_tooling_contract.py` | 0007 | falta `[tool.ruff.format]`; el CI no corre `format --check`; el pin de Python no es 3.12 en los cuatro lugares |
| `test_compuerta_de_cobertura.py` | 0008 | el piso bajó; el número está repetido fuera de `pyproject.toml`; un `pragma: no cover` sin motivo |
| `test_contrato_openapi.py` | 0013 | un endpoint sin `response_model`; `/openapi.json` accesible en producción |
| `test_pipeline_de_deploy.py` | 0012 | prod se puede desplegar desde un push a rama; staging no sale de `main` |
| `test_roles.py` | 0005 | el `StrEnum` de Python y el tipo `role` de Postgres no coinciden; hay un literal de rol en el código |

### A5 · `alembic upgrade head` limpio contra Postgres real

No contra SQLite, y no contra `Base.metadata.create_all()`. Contra un Postgres 16 de verdad, desde una base
**vacía**, igual que en el deploy.

```bash
docker run -d --name pg-acep -e POSTGRES_PASSWORD=carwash -e POSTGRES_USER=carwash \
           -e POSTGRES_DB=carwash -p 5433:5432 postgres:16
until docker exec pg-acep pg_isready -U carwash; do :; done

export DATABASE_URL=postgresql://carwash:carwash@localhost:5433/carwash
cd backend
uv run python scripts/migrate_preflight.py          # dice contra qué base va a migrar
uv run alembic upgrade head                         # ← tiene que salir 0
uv run alembic check                                # ← sin deriva entre modelos y migraciones
uv run alembic downgrade base && uv run alembic upgrade head   # ida y vuelta completa
```

**Pasa si:** los cuatro comandos salen con 0, **sin warnings de tipo desconocido**, y el `alembic check`
no reporta diferencias entre los modelos y el esquema migrado.
**El `downgrade base` es parte del criterio:** una migración que no se puede revertir es una migración que
no se puede ensayar.
**Verifica:** ADR-0002 (las políticas RLS se crean en la migración), ADR-0012.

### A6 · Idempotencia de la cadena de migraciones

Véktor aprendió esto caro: el 2026-09-12 un `alembic_version` que decía una versión y un esquema que ya
tenía la columna de la siguiente abortó un deploy entero con `DuplicateColumn` (`CLAUDE.md:83`). El
`preDeployCommand` corre en **cada** deploy.

```bash
cd backend && uv run pytest app/tests/persistence/test_migraciones_idempotentes_pg.py -v -m postgres
```

**Pasa si:** el test aplica la cadena, **retrocede el stamp sin tocar el esquema** y la vuelve a aplicar sin
error. (Correr `upgrade` dos veces no prueba nada: alembic saltea por versión y no ejecuta nada.)

### A7 · RLS está puesto, forzado y cableado

```bash
cd backend && uv run pytest app/tests/security/test_rls_politicas_pg.py -v -m postgres -n 0
```

**Pasa si:**
1. Toda tabla con `tenant_id` tiene `relrowsecurity` **y** `relforcerowsecurity` en `true`.
2. Toda política tiene `USING` **y** `WITH CHECK` sobre `app.tenant_id` — el `WITH CHECK` es el que impide
   *escribir* en el tenant ajeno, y las políticas de Véktor no lo tienen.
3. Dentro de un request, `current_setting('app.tenant_id', TRUE)` devuelve el tenant del token.
4. Al cerrar la transacción, el valor **no sobrevive** — si alguien cambia `SET LOCAL` por `SET`, este
   punto se pone rojo y evita que el tenant se filtre por el pool de conexiones.
5. Un `SELECT count(*)` crudo, **sin cláusula `tenant_id`**, devuelve 0 para el tenant ajeno. Es lo que
   prueba que RLS es una red propia y no un adorno sobre el `WHERE` del repositorio.

**Verifica:** ADR-0002.
**No cuenta como aprobado** un chequeo tipo `backend/scripts/verify_rls.py:45-46` de Véktor, que solo mira
`pg_tables.rowsecurity`: con el agujero descrito, ese script da verde.

### A8 · Aislamiento cruzado entre dos tenants — **404, nunca 403**

El test lo escribe el **Tester-aislamiento de T3**, que es un agente distinto del que implementó el
recurso. Corre contra Postgres real, porque contra SQLite no hay RLS y el test probaría solo el filtro del
repositorio.

```bash
cd backend && uv run pytest app/tests/security/test_aislamiento_tenants.py -v -m postgres -n 0
```

**Pasa si**, con el tenant A dueño del recurso `dummy` y el tenant B autenticado con su propia cookie:

| Acción de B sobre el recurso de A | Esperado |
|---|---|
| `GET /v1/dummy-resources/{id_de_A}` | **404** |
| `PATCH /v1/dummy-resources/{id_de_A}` | **404** |
| `DELETE /v1/dummy-resources/{id_de_A}` | **404** |
| `GET /v1/dummy-resources` (listado) | 200, y el `id` de A **no** está en `items`; `total` no lo cuenta |
| `POST /v1/dummy-resources` con `"tenant_id": "<el de A>"` inyectado en el body | 201, y el recurso queda bajo **B** — el `tenant_id` sale del token, nunca del cuerpo |
| `GET /v1/dummy-resources/{id_inexistente}` | **404**, y el cuerpo es **byte a byte igual** al del recurso ajeno |

La última fila es la que hace que el criterio sea "404 y no 403" de verdad: si el mensaje, el código de
error o el tiempo de respuesta difieren entre "no existe" y "no es tuyo", el endpoint sigue filtrando la
existencia del recurso ajeno, aunque el número de estado sea 404.

```python
def test_no_se_distingue_ajeno_de_inexistente(client, cookies_b, id_de_a):
    ajeno       = await client.get(f"/v1/dummy-resources/{id_de_a}", cookies=cookies_b)
    inexistente = await client.get(f"/v1/dummy-resources/{uuid4()}",  cookies=cookies_b)
    assert ajeno.status_code == inexistente.status_code == 404
    assert ajeno.json() == inexistente.json()
```

**Verifica:** ADR-0002, ADR-0001.

### A9 · Auth por cookie, CSRF y el middleware que corta

```bash
cd backend  && uv run pytest app/tests/api/test_auth_cookies.py -v
cd frontend && npm test -- middleware.test.ts sin-token-en-localstorage.test.ts
```

**Pasa si:** el login responde `Set-Cookie` con `HttpOnly`, `Secure` y `SameSite=Lax`; el cuerpo **no**
contiene los tokens; un método mutador con `Origin` ajeno da **403**; el middleware de Next devuelve 307 a
`/login` sin cookie y deja pasar con ella; y ningún archivo de `src/{stores,lib,features/auth}` menciona
`localStorage`.
**Verifica:** ADR-0009.

### A10 · Build de producción del frontend y tipos sin deriva

```bash
cd backend  && uv run python -m app.cli.dump_openapi && git diff --exit-code openapi.json
cd frontend && npm run gen:api && git diff --exit-code src/types/api.generated.ts
cd frontend && npm run build
```

**Pasa si:** los dos `git diff --exit-code` salen con 0 (el contrato commiteado está al día) y
`next build` termina sin errores ni warnings de tipos.
**Verifica:** ADR-0013.

---

## Parte II — Lo que tiene que pasar en la URL desplegada

Nada de esta parte se puede dar por aprobado desde local. `Secure`, las cabeceras y el manifiesto servido
solo son ciertos en el deploy real.

### A11 · Los dos ambientes existen y no comparten base

```bash
make check-envs
```

**Pasa si:** `/health` de staging responde `env: "staging"` y el de prod `env: "production"`; los
`db_fingerprint` **difieren**; y un token emitido por staging da **401** contra prod.
**Verifica:** ADR-0012.

### A12 · El flujo completo, a mano, en la URL de Vercel

Esto lo hace una persona, en un navegador, en la URL pública. Es el checkpoint del roadmap.

1. Abrir `https://app.carwash.app` en una ventana privada.
2. **Registrarse** con un email nuevo → redirige al dashboard.
3. El dashboard está **vacío**: un `EmptyState`, cero dominio de lavadero, cero datos de mentira.
4. En las herramientas del navegador: la cookie de sesión figura como `HttpOnly` ✓ y `Secure` ✓, y
   `localStorage` **no contiene ningún token**.
5. Cerrar sesión → vuelve a `/login`. Escribir `/dashboard` a mano en la barra → redirige a
   `/login?next=%2Fdashboard` **sin haber renderizado nada del dashboard**.
6. **Iniciar sesión** con las credenciales del paso 2 → vuelve al dashboard vacío.
7. Instalar la app desde el menú del navegador en un celular → aparece el ícono y abre a pantalla completa,
   sin barra de direcciones.

Y su equivalente automatizable, para que la próxima vez no dependa de que alguien se acuerde:

```bash
API=https://api.carwash.app; APP=https://app.carwash.app; J=$(mktemp)

# registro → la cookie es httpOnly+Secure+Lax y el body no trae el token
curl -fsS -c "$J" -D - -o /tmp/body.json -X POST "$API/v1/auth/register" \
     -H 'Content-Type: application/json' -d '{"email":"acep@ejemplo.com","password":"…","tenant":"Acep"}' \
  | grep -i '^set-cookie:' | grep -q 'HttpOnly.*Secure.*SameSite=Lax'
! grep -q 'access_token' /tmp/body.json

# la sesión sirve, y el dashboard de un tenant nuevo está vacío
[ "$(curl -fsS -b "$J" "$API/v1/dummy-resources" | jq -r .total)" = "0" ]

# ruta protegida sin cookie → 307 a /login, no HTML del dashboard
[ "$(curl -s -o /dev/null -w '%{http_code}' "$APP/dashboard")" = "307" ]

# el manifiesto se sirve y es instalable
curl -fsS "$APP/manifest.webmanifest" \
  | jq -e '.name and .short_name and (.display=="standalone")
           and ([.icons[].sizes]|index("192x192")) and ([.icons[].sizes]|index("512x512"))'
curl -sI "$APP/sw.js" | grep -qi 'cache-control: .*no-store'
```

**Verifica:** ADR-0009, ADR-0011, ADR-0012.

### A13 · Sentry recibe un error con el tag `tenant_id`

```bash
curl -fsS -b "$J" -X POST "$API/v1/debug/boom"     # endpoint de prueba, solo fuera de producción
```

**Pasa si:** el evento llega a Sentry con el tag `tenant_id` puesto (como en
`backend/app/api/v1/deps.py:72`), con `user.id` **y sin el email** (`deps.py:73`), y con el
`X-Trace-Id` del request en los breadcrumbs.
**Y además** —esto es lo que hay que mirar de verdad— el evento **no contiene ninguna patente ni ningún
DNI** en headers, query string, vars del stacktrace, breadcrumbs ni extra. Se comprueba mandando el boom
con una patente de prueba (`AB123CD`) en un header, en la query y en el body, y verificando que en el
evento aparezca redactada en los tres lugares.

### A14 · El deploy falla seguro

Un deploy con una migración rota **no** puede dejar la API caída.

1. Abrir una rama con una migración que falle a propósito y desplegarla a **staging**.
2. **Pasa si:** el `preDeployCommand` corta (el `set -eu` de `migrate.sh:15`), Railway aborta el deploy, y
   `https://api-staging.carwash.app/health` **sigue respondiendo 200 con el `commit` anterior**.
3. En el log del deploy tiene que estar la salida del preflight, diciendo host, base y las tablas
   `alembic_version` visibles.
4. Revertir la rama.

Sin este ensayo, el fail-safe es una creencia. **Verifica:** ADR-0012.

---

## Cómo se corre todo

```bash
make check          # A1 + A2
make test-cov       # A3 + A4
make test-pg        # A5 + A6 + A7 + A8  (Postgres real, secuencial)
make openapi-check  # A10
make check-envs     # A11
```

A12, A13 y A14 son manuales por definición: son el checkpoint.

## Compuerta de `/code-review`

Antes del checkpoint corre `/code-review` sobre el diff completo de la fase. Según el `CLAUDE.md` del
proyecto es **compuerta, no sugerencia**: ningún hallazgo llega abierto al checkpoint sin resolver, o sin
diferir con motivo escrito. El acta del checkpoint lista, para cada hallazgo diferido, por qué se difiere y
a qué fase.

## Qué **no** es criterio de aceptación de esta fase

Dicho explícitamente, para que nadie lo agregue:

- Cualquier cosa del dominio del lavadero: turnos, jobs, clientes, vehículos, caja. **Cero.** Si aparece un
  modelo que no sea `tenants`, `users`, `idempotency_keys` y `dummy_resources`, la fase se está pasando de
  alcance.
- Diseño visual. El dashboard vacío es un `EmptyState`, no una pantalla terminada.
- Rendimiento y pruebas de carga.
- Notificaciones push (ADR-0011), rol `SUPERADMIN` (ADR-0005), paginación por cursor (ADR-0006) y UUIDv7
  (ADR-0004): los cuatro están diferidos por escrito, cada uno con su ADR futura.
