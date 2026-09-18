"""A7 / ADR-0002 — RLS puesto, forzado y cableado. Contra Postgres real, rol `carwash_app`.

Reemplaza a `backend/scripts/verify_rls.py` de Véktor, que solo mira
`pg_tables.rowsecurity` y daría verde con el contexto de tenant sin setear.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from starlette.requests import Request

import app.persistence.models  # noqa: F401
from app.domain.roles import Role
from app.persistence.db.base import Base
from app.persistence.db.session import make_session_dependency
from app.persistence.db.tenant_context import set_tenant_context
from app.utils.cookies import ACCESS_COOKIE
from app.utils.security import create_access_token

# loop de sesión: los engines de Postgres son session-scoped y asyncpg ata la conexión al loop.
pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

TABLAS_TENANT = {t.name for t in Base.metadata.tables.values() if "tenant_id" in t.c}
#: L3: `tenants` no tiene `tenant_id` (se aísla por `id`), pero también lleva RLS.
TABLAS_CON_RLS = TABLAS_TENANT | {"tenants"}


def _request_con_token(tenant_id: uuid.UUID) -> Request:
    token = create_access_token({"sub": str(uuid.uuid4()), "tenant_id": str(tenant_id)})
    cookie = f"{ACCESS_COOKIE}={token}".encode()
    return Request({"type": "http", "headers": [(b"cookie", cookie)], "method": "GET"})


async def test_toda_tabla_tenant_tiene_rls_forzado(pg_admin_engine):
    async with pg_admin_engine.connect() as conn:
        filas = (
            await conn.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relkind = 'r' AND relname = ANY(:tablas)"
                ),
                {"tablas": list(TABLAS_CON_RLS)},
            )
        ).all()
    assert {f.relname for f in filas} == TABLAS_CON_RLS
    for f in filas:
        assert f.relrowsecurity, f"{f.relname}: RLS no habilitado"
        assert f.relforcerowsecurity, f"{f.relname}: RLS sin FORCE (el owner se lo saltea)"


async def test_toda_politica_tiene_using_y_with_check(pg_admin_engine):
    async with pg_admin_engine.connect() as conn:
        politicas = (
            await conn.execute(
                text("SELECT tablename, policyname, cmd, roles, qual, with_check FROM pg_policies")
            )
        ).all()
    aislamiento = [p for p in politicas if p.policyname.endswith("_tenant_isolation")]
    assert {p.tablename for p in aislamiento} == TABLAS_CON_RLS
    for p in aislamiento:
        # Sin excepciones: la misma expresión en USING y WITH CHECK, para todos los roles.
        assert p.cmd == "ALL" and list(p.roles) == ["public"], p.tablename
        assert p.qual is not None and "app.tenant_id" in p.qual, p.tablename
        assert p.with_check == p.qual, p.tablename
        assert "identity_lookup" not in p.qual, p.tablename

    # La única otra política: lectura de `users` para el DUEÑO (la usa la función
    # SECURITY DEFINER del login). Solo SELECT, y nunca alcanza al rol de runtime.
    otras = [p for p in politicas if p not in aislamiento]
    assert [(p.tablename, p.policyname, p.cmd) for p in otras] == [
        ("users", "users_owner_auth_lookup", "SELECT")
    ]
    assert list(otras[0].roles) == ["carwash_owner"]


async def test_el_rol_de_runtime_no_saltea_rls(pg_engine):
    async with pg_engine.connect() as conn:
        fila = (
            await conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    assert fila.rolsuper is False and fila.rolbypassrls is False


async def test_get_db_session_setea_el_contexto_en_la_transaccion(pg_session_factory, pg_tenant_a):
    dependency = make_session_dependency(pg_session_factory)
    gen = dependency(_request_con_token(pg_tenant_a))
    session = await gen.__anext__()
    valor = await session.scalar(text("SELECT current_setting('app.tenant_id', TRUE)"))
    assert valor == str(pg_tenant_a)
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


async def test_el_contexto_no_sobrevive_a_la_transaccion(pg_engine, pg_tenant_a):
    # pool_size=1: la conexión que vuelve al pool es la MISMA que usa el siguiente.
    # Si alguien cambia SET LOCAL por SET, este test se pone rojo.
    async with pg_engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{pg_tenant_a}'"))
        assert await conn.scalar(text("SELECT current_setting('app.tenant_id', TRUE)")) in (
            None,
            "",
        )
    async with pg_engine.connect() as conn:
        assert await conn.scalar(text("SELECT current_setting('app.tenant_id', TRUE)")) in (
            None,
            "",
        )


async def test_rls_filtra_sin_ayuda_del_repositorio(pg_session_factory, pg_tenant_b, pg_dummy_de_a):
    # SQL crudo, sin cláusula tenant_id: si RLS no estuviera cableado, esto devolvería 1.
    async with pg_session_factory() as session, session.begin():
        await set_tenant_context(session, pg_tenant_b)
        assert await session.scalar(text("SELECT count(*) FROM dummy_resources")) == 0


async def test_el_duenio_ve_lo_suyo(pg_session_factory, pg_tenant_a, pg_dummy_de_a):
    async with pg_session_factory() as session, session.begin():
        await set_tenant_context(session, pg_tenant_a)
        assert await session.scalar(text("SELECT count(*) FROM dummy_resources")) == 1


async def test_sin_contexto_no_hay_datos(pg_session_factory, pg_dummy_de_a):
    async with pg_session_factory() as session, session.begin():
        assert await session.scalar(text("SELECT count(*) FROM dummy_resources")) == 0
        assert await session.scalar(text("SELECT count(*) FROM users")) == 0


async def test_with_check_impide_escribir_en_el_tenant_ajeno(
    pg_session_factory, pg_tenant_a, pg_tenant_b
):
    async with pg_session_factory() as session:
        with pytest.raises(DBAPIError, match="row-level security"):
            async with session.begin():
                await set_tenant_context(session, pg_tenant_b)
                await session.execute(
                    text("INSERT INTO dummy_resources (id, tenant_id, name) VALUES (:i, :t, 'x')"),
                    {"i": uuid.uuid4(), "t": pg_tenant_a},
                )


async def test_la_funcion_de_login_devuelve_solo_lo_minimo_de_una_fila(
    pg_session_factory, pg_tenant_a, pg_user_factory
):
    user_id = await pg_user_factory(pg_tenant_a, "login@example.com")
    async with pg_session_factory() as session, session.begin():
        # El rol de runtime, sin tenant: `users` directo no muestra nada...
        assert await session.scalar(text("SELECT count(*) FROM users")) == 0
        # ...y la función devuelve UNA fila, con estas columnas y ninguna otra.
        result = await session.execute(
            text("SELECT * FROM auth_lookup_user(:e)"), {"e": "LOGIN@example.com"}
        )
        filas = result.all()
        assert list(result.keys()) == ["id", "tenant_id", "password_hash", "token_version", "role"]
        assert [(f.id, f.tenant_id) for f in filas] == [(user_id, pg_tenant_a)]
        assert (
            await session.execute(text("SELECT * FROM auth_lookup_user('nadie@example.com')"))
        ).all() == []


async def test_la_funcion_de_login_es_segura(pg_admin_engine):
    async with pg_admin_engine.connect() as conn:
        f = (
            await conn.execute(
                text(
                    "SELECT p.prosecdef, pg_get_userbyid(p.proowner) AS owner, p.proconfig, "
                    "has_function_privilege('public', p.oid, 'EXECUTE') AS publico, "
                    "has_function_privilege('carwash_app', p.oid, 'EXECUTE') AS app "
                    "FROM pg_proc p WHERE p.proname = 'auth_lookup_user'"
                )
            )
        ).one()
    assert f.prosecdef is True, "tiene que ser SECURITY DEFINER"
    assert f.owner == "carwash_owner"
    assert any(c.startswith("search_path=") for c in f.proconfig), "search_path sin fijar"
    assert f.publico is False and f.app is True


async def test_el_enum_de_python_y_el_tipo_de_postgres_dicen_lo_mismo(pg_admin_engine):
    async with pg_admin_engine.connect() as conn:
        roles = set((await conn.execute(text("SELECT unnest(enum_range(NULL::role))"))).scalars())
        motivos = set(
            (await conn.execute(text("SELECT unnest(enum_range(NULL::void_reason))"))).scalars()
        )
    from app.domain.void import VoidReason

    assert roles == {r.value for r in Role}
    assert motivos == {v.value for v in VoidReason}


async def test_la_base_rechaza_un_rol_inventado(pg_session_factory, pg_tenant_a):
    async with pg_session_factory() as session:
        with pytest.raises(DBAPIError):
            async with session.begin():
                await set_tenant_context(session, pg_tenant_a)
                await session.execute(
                    text(
                        "INSERT INTO users (id, tenant_id, email, password_hash, role) "
                        "VALUES (:i, :t, 'p@example.com', 'x', 'PRESIDENTE')"
                    ),
                    {"i": uuid.uuid4(), "t": pg_tenant_a},
                )


async def test_flujo_http_completo_con_rls(pg_client):
    # registro → sesión → CRUD → login, todo con el rol de runtime y RLS activo.
    reg = await pg_client.post(
        "/v1/auth/register",
        json={"email": "pg@example.com", "password": "correct-horse-battery", "tenant": "PG"},
    )
    assert reg.status_code == 201, reg.text
    creado = await pg_client.post("/v1/dummy-resources", json={"name": "uno"})
    assert creado.status_code == 201, creado.text
    listado = (await pg_client.get("/v1/dummy-resources")).json()
    assert listado["total"] == 1
    replay = [
        await pg_client.post(
            "/v1/dummy-resources", json={"name": "i"}, headers={"Idempotency-Key": "pg-k"}
        )
        for _ in range(2)
    ]
    assert [r.status_code for r in replay] == [201, 409]
    assert replay[1].json()["detail"]["code"] == "DUPLICATE_IDEMPOTENT"

    pg_client.cookies.clear()
    login = await pg_client.post(
        "/v1/auth/login", json={"email": "pg@example.com", "password": "correct-horse-battery"}
    )
    assert login.status_code == 200, login.text
    assert (await pg_client.get("/v1/auth/me")).status_code == 200
    borrado = await pg_client.delete(f"/v1/dummy-resources/{creado.json()['id']}")
    assert borrado.status_code == 204
    dup = await pg_client.post(
        "/v1/auth/register",
        json={"email": "pg@example.com", "password": "correct-horse-battery", "tenant": "X"},
    )
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "EMAIL_TAKEN"


async def test_h4_auditoria_del_rol_contra_postgres_real(pg_engine, pg_admin_engine):
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.bootstrap import UnsafeDatabaseRoleError, audit_runtime_role, enforce_safe_runtime_role
    from app.tests.conftest_pg import _with_driver, owner_url

    async with pg_engine.connect() as conn:
        assert (await audit_runtime_role(conn)).problems == []
        await enforce_safe_runtime_role(conn, "production")
    async with pg_admin_engine.connect() as conn:
        with pytest.raises(UnsafeDatabaseRoleError, match="superusuario"):
            await enforce_safe_runtime_role(conn, "production")
    duenio = create_async_engine(_with_driver(owner_url(), "asyncpg"))
    try:
        async with duenio.connect() as conn:
            with pytest.raises(UnsafeDatabaseRoleError, match="dueño"):
                await enforce_safe_runtime_role(conn, "staging")
    finally:
        await duenio.dispose()
