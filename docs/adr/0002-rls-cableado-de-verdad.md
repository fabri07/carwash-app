# ADR-0002 · RLS de Postgres cableado de verdad, no declarado y olvidado

**Estado:** aceptada · **Fecha:** 2026-09-15 · **Fase:** 2

## Contexto

Véktor tiene las políticas de Row Level Security creadas en la base y **nunca las activa**. Las dos
mitades del mecanismo existen por separado y no se tocan:

**La mitad que sí está.** Las migraciones habilitan RLS y crean las políticas:
- `backend/app/persistence/migrations/versions/20260401_0001_security_fase0_rls_pending_actions_cipher.py:114`
  → `EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);`
- `…20260401_0001…py:121-122` → `CREATE POLICY … USING (tenant_id = current_setting('app.current_tenant_id', TRUE)::uuid)`
- Políticas equivalentes en `…20260401_0003_restore_chat_context_and_heuristics.py:85,97-99,105,117-119`,
  `…20260726_0001_chat_coverage_gaps.py:85,92-93` y `…20260806_0003_audit_log_tenant_nullable.py:84,98`.

**La mitad que falta.** La función que setea el parámetro de sesión del que dependen todas esas políticas
existe — `backend/app/application/db/tenant_context.py:6` (`async def set_tenant_context(...)`, que emite
`SET app.current_tenant_id = :tid` en las líneas 16-21) — pero **no se llama desde ningún punto del código
de producción**. `rg -n "set_tenant_context"` devuelve exactamente dos matches más, y ninguno es una
llamada:

- `backend/app/application/db/tenant_context.py:13` — está dentro del docstring, es el ejemplo de uso.
- `backend/app/application/middleware/tenant.py:16` — es un **comentario** que describe la intención
  (*"que tenant_context.set_tenant_context() lo use al abrir cada conexión DB"*). El middleware nunca la
  invoca: `TenantMiddleware.dispatch` (`middleware/tenant.py:19-40`) decodifica el JWT **sin verificar la
  firma** y guarda `request.state.tenant_id` (`:35`). No toca la base.

Tampoco la llama el dependency que abre la sesión (`backend/app/persistence/db/session.py:20-31`, que no
menciona tenant en absoluto) ni `backend/app/api/v1/deps.py`. El aislamiento real es 100% el filtro manual
del repositorio: `backend/app/api/v1/deps.py:63` → `await repo.get_by_id(UUID(user_id), UUID(tenant_id))`.

La única comprobación de RLS es `backend/scripts/verify_rls.py`, un script que se corre **a mano** contra
prod y que solo consulta `pg_tables.rowsecurity` (`verify_rls.py:45-46`): verifica que la política exista,
no que esté activa. En Véktor pasaría en verde exactamente con el agujero descrito.

Por qué no se hereda: una política RLS cuyo `current_setting('app.current_tenant_id', TRUE)` es siempre
`NULL` no filtra nada — **es más peligrosa que no tenerla**, porque el equipo cree que hay una red que no
existe. El aislamiento termina dependiendo de que ninguno de los ~40 repositorios se olvide nunca de un
`WHERE tenant_id = :x`. Eso es una apuesta, no un control.

Hay además un bug concreto en el código muerto: `SET app.current_tenant_id` (sin `LOCAL`) es de **sesión**,
no de transacción. Con un pool de conexiones, el valor sobrevive al request y la conexión vuelve al pool
marcada con el tenant anterior. Si Véktor cableara esa función tal como está, el próximo request que tome
esa conexión y no setee el contexto leería datos del tenant previo.

## Decisión

1. **Dos roles de Postgres.** `carwash_owner` (dueño de las tablas, corre las migraciones) y `carwash_app`
   (runtime, **no** dueño, **sin** `BYPASSRLS`). El dueño de una tabla se saltea RLS por defecto, así que
   correr la app con el rol de migración anula todo el mecanismo.
2. **`FORCE ROW LEVEL SECURITY`** en toda tabla tenant-scoped, además de `ENABLE`. Con `FORCE`, ni el
   dueño se saltea la política.
3. **La política se crea en la misma migración que crea la tabla**, generada a partir de las tablas que
   usan `TenantMixin` (ADR-0001), no de una lista mantenida a mano:
   `USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)`
   `WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)::uuid)` — el `WITH CHECK` es lo que
   impide *escribir* en el tenant ajeno, y las políticas de Véktor no lo tienen.
4. **`SET LOCAL`, no `SET`.** El dependency `get_db_session` emite
   `SET LOCAL app.tenant_id = :tid` dentro de la transacción del request, antes de ceder la sesión, con el
   `tenant_id` sacado del token (nunca del body ni del path). Al cerrar la transacción el valor se va con
   ella: la conexión vuelve al pool limpia.
5. **Sin tenant no hay datos.** Un request sin contexto seteado deja `current_setting('app.tenant_id', TRUE)`
   en `NULL`, y `tenant_id = NULL::uuid` es `NULL` → la política no deja pasar ninguna fila. El modo
   por defecto es *no ver nada*, no *ver todo*.
6. **Los repositorios siguen filtrando por `tenant_id`** (`BaseRepository`, ADR-0001 y el manifiesto de
   portado). RLS es la primera red y los tests la segunda; el filtro del repositorio es la tercera y es
   gratis. Lo que **no** se acepta es que sea la única.
7. **Recurso ajeno devuelve 404, no 403.** Con RLS activo esto sale solo: la fila simplemente no existe
   para esa sesión, así que el `get_by_id` devuelve `None` y el endpoint responde 404 sin tener que
   acordarse de no filtrar existencia.

## Consecuencias

- **Gana:** un repositorio nuevo que se olvide del `WHERE tenant_id` sigue devolviendo solo lo del tenant.
  El olvido pasa de ser una fuga a ser una redundancia.
- **Gana:** el 404-en-vez-de-403 deja de ser una convención que hay que recordar y pasa a ser una
  consecuencia del mecanismo.
- **Cuesta:** SQLite no tiene RLS. La suite rápida sigue corriendo en SQLite (es lo que la hace durar
  minutos), pero **los tests de aislamiento tienen que correr contra Postgres real**, con marca `postgres`
  y un engine propio. El CI ya levanta un `postgres:16` como servicio (ver el manifiesto), así que el
  costo es un job, no una infraestructura nueva.
- **Cuesta:** dos roles de base que hay que crear y mantener en Railway, y un `DATABASE_URL` distinto para
  migraciones y para runtime.
- **Trampa a recordar:** `SET LOCAL` exige que haya una transacción abierta. Con `autocommit`/
  `AUTOCOMMIT` el `SET LOCAL` se pierde en silencio y las políticas bloquean todo. El síntoma es "no veo
  mis propios datos", y es el modo de falla correcto: falla cerrado.
- **Trampa a recordar:** si alguna vez entra un pooler en modo *transaction* (PgBouncer, el pooler de
  Neon), `SET LOCAL` sigue siendo correcto — `SET` a secas no lo sería. Esta decisión ya está del lado
  seguro.

## Cómo se verifica

Cuatro comprobaciones, de la más barata a la más cara. Las tres primeras son de la Fase 2 (dueño:
`backend`); la cuarta la escribe el Tester-aislamiento de T3, que es un agente distinto del implementador.

**1 · Que la política exista y esté forzada, para toda tabla del mixin** —
`backend/app/tests/security/test_rls_politicas_pg.py` (marca `postgres`):

```python
@pytest.mark.postgres
async def test_toda_tabla_tenant_tiene_rls_forzado(pg_conn):
    esperadas = {t.name for t in Base.metadata.tables.values() if "tenant_id" in t.c}
    filas = await pg_conn.fetch(
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relkind='r' AND relname = ANY($1)", list(esperadas))
    for r in filas:
        assert r["relrowsecurity"],      f"{r['relname']}: RLS no habilitado"
        assert r["relforcerowsecurity"], f"{r['relname']}: RLS sin FORCE (el owner se lo saltea)"
    assert {r["relname"] for r in filas} == esperadas

@pytest.mark.postgres
async def test_toda_politica_tiene_using_y_with_check(pg_conn):
    # la ausencia de WITH CHECK es exactamente el agujero de Véktor: deja escribir en el tenant ajeno.
    for p in await pg_conn.fetch("SELECT tablename, qual, with_check FROM pg_policies"):
        assert p["qual"] is not None       and "app.tenant_id" in p["qual"]
        assert p["with_check"] is not None and "app.tenant_id" in p["with_check"]
```

**2 · Que el contexto se setee de verdad en cada request, y con `LOCAL`.** El test que Véktor no tiene:

```python
@pytest.mark.postgres
async def test_get_db_session_setea_el_contexto_en_la_transaccion(pg_session, tenant_a):
    valor = await pg_session.scalar(text("SELECT current_setting('app.tenant_id', TRUE)"))
    assert valor == str(tenant_a.id)

@pytest.mark.postgres
async def test_el_contexto_no_sobrevive_a_la_transaccion(pg_engine, tenant_a):
    # si alguien cambia SET LOCAL por SET, este test se pone rojo: el valor se filtra al próximo
    # usuario de la misma conexión del pool.
    async with pg_engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SET LOCAL app.tenant_id = :t"), {"t": str(tenant_a.id)})
        assert await conn.scalar(text("SELECT current_setting('app.tenant_id', TRUE)")) in (None, "")
```

**3 · Que RLS filtre aunque el repositorio no filtre.** Es el test que prueba que RLS es una red *propia*
y no un adorno sobre el `WHERE` del repositorio:

```python
@pytest.mark.postgres
async def test_rls_filtra_sin_ayuda_del_repositorio(pg_session_de(tenant_b), dummy_de_tenant_a):
    # SQL crudo, sin cláusula tenant_id: si RLS no estuviera cableado, esto devolvería 1.
    total = await pg_session.scalar(text("SELECT count(*) FROM dummy_resources"))
    assert total == 0
```

**4 · El test cruzado de extremo a extremo (T3, Tester-aislamiento).** Dos tenants, un recurso dummy
creado por A, leído por B con su propio token: `GET /v1/dummy-resources/{id}` → **404**, y el cuerpo no
distingue "no existe" de "no es tuyo". `PATCH` y `DELETE` sobre el mismo id → 404. Y el intento de
**crear** un dummy con `tenant_id` ajeno inyectado en el body → el recurso queda bajo el tenant del token,
nunca bajo el del body. Detalle en `FASE-2-ACEPTACION.md`.

**Anti-verificación explícita:** no alcanza con portar `backend/scripts/verify_rls.py`. Ese script mira
`pg_tables.rowsecurity` y en Véktor daría verde hoy, con el contexto sin setear. Un chequeo que pasa
estando el sistema roto no es un chequeo.
