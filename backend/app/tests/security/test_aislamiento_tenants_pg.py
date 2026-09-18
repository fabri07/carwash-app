"""Aislamiento en la red de abajo: RLS de Postgres sin la API (ADR-0002).

Tester-aislamiento de T3. Todo se afirma con el rol de runtime `carwash_app`
(`pg_engine`, `pool_size=1`, `max_overflow=0`: el próximo checkout es la MISMA
conexión). Los datos se siembran con el superusuario (`pg_admin_engine`), que es
también el único testigo confiable de "no cambió nada".

Cubre:
1. Sin `app.tenant_id` → 0 filas en toda tabla con tenant, sin error.
2. Con tenant A → nada de B; `INSERT`/`UPDATE` hacia B → rechazado por `WITH CHECK`.
3. `SET LOCAL` no se filtra por el pool, ni tras éxito ni tras error.
4. La búsqueda de identidad del login. Antes era una "ventana" `app.identity_lookup`
   en la política de `users` (BUG-1/M4: convención, no frontera, y con tenant en
   contexto permitía secuestrar un usuario ajeno). Se reemplazó por la función
   `SECURITY DEFINER` `auth_lookup_user`; estos tests verifican que el GUC viejo ya
   no abre nada y que la función no es un atajo para leer ni escribir `users`.
"""

import ast
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.persistence.db.base import Base
from app.persistence.db.tenant_context import set_tenant_context
from app.tests.conftest_pg import PG_TEST_URL, _as_app_role, _with_driver
from app.utils.cookies import ACCESS_COOKIE
from app.utils.security import create_access_token

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

TABLAS_TENANT = sorted(t.name for t in Base.metadata.tables.values() if "tenant_id" in t.c)
PASSWORD = "correct-horse-battery"


@pytest.fixture
async def sembrado(pg_admin_engine, pg_tenant_a, pg_tenant_b, pg_user_factory):
    """Una fila por tenant en cada tabla con tenant."""
    ids: dict[str, dict[str, uuid.UUID]] = {"a": {}, "b": {}}
    for lado, tenant in (("a", pg_tenant_a), ("b", pg_tenant_b)):
        ids[lado]["tenant"] = tenant
        ids[lado]["users"] = await pg_user_factory(tenant, f"owner-{lado}@rls.example.com")
        async with pg_admin_engine.begin() as conn:
            ids[lado]["dummy_resources"] = uuid.uuid4()
            await conn.execute(
                text("INSERT INTO dummy_resources (id, tenant_id, name) VALUES (:i, :t, :n)"),
                {"i": ids[lado]["dummy_resources"], "t": tenant, "n": f"de {lado}"},
            )
            ids[lado]["idempotency_keys"] = uuid.uuid4()
            await conn.execute(
                text(
                    "INSERT INTO idempotency_keys (id, tenant_id, key, action) "
                    "VALUES (:i, :t, 'k', 'x')"
                ),
                {"i": ids[lado]["idempotency_keys"], "t": tenant},
            )
    return ids


async def _tenant_de_fila(admin: AsyncEngine, tabla: str, fila_id: uuid.UUID) -> uuid.UUID:
    async with admin.connect() as conn:
        return await conn.scalar(
            text(f"SELECT tenant_id FROM {tabla} WHERE id = :i"), {"i": fila_id}
        )


def _cookies(user_id: uuid.UUID, tenant_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token({"sub": str(user_id), "tenant_id": str(tenant_id), "ver": 0})
    return {ACCESS_COOKIE: token}


# ── 1 · Sin contexto no hay datos ─────────────────────────────────────────────


async def test_las_tablas_cubiertas_son_las_esperadas():
    # si aparece una tabla con tenant nueva, los tests de abajo la recorren sola
    assert set(TABLAS_TENANT) >= {"users", "dummy_resources", "idempotency_keys"}


@pytest.mark.parametrize("tabla", TABLAS_TENANT)
async def test_sin_contexto_cero_filas_sin_error(pg_session_factory, sembrado, tabla):
    async with pg_session_factory() as session, session.begin():
        assert await session.scalar(text(f"SELECT count(*) FROM {tabla}")) == 0
        # ni siquiera pidiéndolas explícitamente
        assert (
            await session.scalar(
                text(f"SELECT count(*) FROM {tabla} WHERE tenant_id = :t"),
                {"t": sembrado["a"]["tenant"]},
            )
            == 0
        )


@pytest.mark.parametrize("valor", ["", "off"])
async def test_contexto_vacio_o_basura_no_abre_nada(pg_session_factory, sembrado, valor):
    async with pg_session_factory() as session:
        try:
            async with session.begin():
                await session.execute(text(f"SET LOCAL app.tenant_id = '{valor}'"))
                total = sum(
                    [await session.scalar(text(f"SELECT count(*) FROM {t}")) for t in TABLAS_TENANT]
                )
        except DBAPIError:
            return  # falla cerrado con error: aceptable
    assert total == 0


# ── 2 · Con tenant A: nada de B, y no se escribe en B ─────────────────────────


@pytest.mark.parametrize("tabla", TABLAS_TENANT)
async def test_con_tenant_a_solo_se_ve_a(pg_session_factory, sembrado, tabla):
    a, b = sembrado["a"], sembrado["b"]
    async with pg_session_factory() as session, session.begin():
        await set_tenant_context(session, a["tenant"])
        ids = set((await session.execute(text(f"SELECT id FROM {tabla}"))).scalars())
        assert ids == {a[tabla]}
        assert (
            await session.scalar(
                text(f"SELECT count(*) FROM {tabla} WHERE id = :i"), {"i": b[tabla]}
            )
            == 0
        )


def _insert_en(tabla: str) -> str:
    return {
        "users": "INSERT INTO users (id, tenant_id, email, password_hash, role) "
        "VALUES (:i, :t, 'intruso@rls.example.com', 'x', 'OWNER')",
        "dummy_resources": "INSERT INTO dummy_resources (id, tenant_id, name) "
        "VALUES (:i, :t, 'intruso')",
        "idempotency_keys": "INSERT INTO idempotency_keys (id, tenant_id, key, action) "
        "VALUES (:i, :t, 'intrusa', 'x')",
    }[tabla]


@pytest.mark.parametrize("tabla", TABLAS_TENANT)
async def test_insert_con_tenant_de_b_rechazado_por_with_check(
    pg_session_factory, pg_admin_engine, sembrado, tabla
):
    nuevo = uuid.uuid4()
    async with pg_session_factory() as session:
        with pytest.raises(DBAPIError, match="row-level security"):
            async with session.begin():
                await set_tenant_context(session, sembrado["a"]["tenant"])
                await session.execute(
                    text(_insert_en(tabla)), {"i": nuevo, "t": sembrado["b"]["tenant"]}
                )
    assert await _tenant_de_fila(pg_admin_engine, tabla, nuevo) is None


@pytest.mark.parametrize("tabla", TABLAS_TENANT)
async def test_update_que_muda_fila_propia_a_b_rechazado(
    pg_session_factory, pg_admin_engine, sembrado, tabla
):
    a, b = sembrado["a"], sembrado["b"]
    async with pg_session_factory() as session:
        with pytest.raises(DBAPIError, match="row-level security"):
            async with session.begin():
                await set_tenant_context(session, a["tenant"])
                await session.execute(
                    text(f"UPDATE {tabla} SET tenant_id = :b WHERE id = :i"),
                    {"b": b["tenant"], "i": a[tabla]},
                )
    assert await _tenant_de_fila(pg_admin_engine, tabla, a[tabla]) == a["tenant"]


async def test_update_sobre_filas_de_b_no_toca_nada(pg_engine, pg_admin_engine, sembrado):
    a, b = sembrado["a"], sembrado["b"]
    async with pg_engine.connect() as conn, conn.begin():
        await conn.execute(text(f"SET LOCAL app.tenant_id = '{a['tenant']}'"))
        r = await conn.execute(
            text("UPDATE dummy_resources SET name = 'pisado' WHERE id = :i"),
            {"i": b["dummy_resources"]},
        )
        assert r.rowcount == 0
        r = await conn.execute(
            text("UPDATE users SET token_version = 99 WHERE id = :i"), {"i": b["users"]}
        )
        assert r.rowcount == 0
    async with pg_admin_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT name FROM dummy_resources WHERE id = :i"), {"i": b["dummy_resources"]}
            )
            == "de b"
        )
        assert (
            await conn.scalar(
                text("SELECT token_version FROM users WHERE id = :i"), {"i": b["users"]}
            )
            == 0
        )


async def test_el_rol_de_runtime_no_puede_borrar_filas(pg_engine, sembrado):
    # sin GRANT DELETE: anular (ADR-0003) es la única baja posible
    async with pg_engine.connect() as conn:
        with pytest.raises(DBAPIError, match="permission denied"):
            async with conn.begin():
                await conn.execute(text(f"SET LOCAL app.tenant_id = '{sembrado['b']['tenant']}'"))
                await conn.execute(text("DELETE FROM dummy_resources"))


async def test_tabla_tenants_tiene_rls_y_el_rol_de_runtime_solo_ve_y_edita_el_propio(
    pg_engine, pg_admin_engine, sembrado
):
    """Invertido a propósito (L3). Antes: `tenants` sin RLS, con contexto de A se leía
    y renombraba B. Ahora la política es `id = tenant del contexto`, USING y WITH CHECK."""
    a, b = sembrado["a"], sembrado["b"]
    async with pg_engine.connect() as conn:
        async with conn.begin():
            assert await conn.scalar(text("SELECT count(*) FROM tenants")) == 0  # sin contexto
        async with conn.begin():
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{a['tenant']}'"))
            ids = set((await conn.execute(text("SELECT id FROM tenants"))).scalars())
            assert ids == {a["tenant"]}
            r = await conn.execute(
                text("UPDATE tenants SET name = 'renombrado por A' WHERE id = :b"),
                {"b": b["tenant"]},
            )
            assert r.rowcount == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            async with conn.begin():
                await conn.execute(text(f"SET LOCAL app.tenant_id = '{a['tenant']}'"))
                await conn.execute(
                    text("INSERT INTO tenants (id, name) VALUES (:i, 'intruso')"),
                    {"i": uuid.uuid4()},
                )
    async with pg_admin_engine.connect() as conn:
        assert (
            await conn.scalar(text("SELECT name FROM tenants WHERE id = :b"), {"b": b["tenant"]})
            == "Lavadero B"
        )


# ── 3 · SET LOCAL no se filtra por el pool ────────────────────────────────────


async def _estado_de_la_conexion(pg_session_factory) -> tuple[int, str | None, str | None, int]:
    async with pg_session_factory() as session, session.begin():
        pid = await session.scalar(text("SELECT pg_backend_pid()"))
        tenant = await session.scalar(text("SELECT current_setting('app.tenant_id', TRUE)"))
        lookup = await session.scalar(text("SELECT current_setting('app.identity_lookup', TRUE)"))
        filas = sum(
            [await session.scalar(text(f"SELECT count(*) FROM {t}")) for t in TABLAS_TENANT]
        )
        return pid, tenant, lookup, filas


async def test_request_de_a_no_deja_el_tenant_en_la_conexion(
    pg_client, pg_session_factory, sembrado
):
    a, b = sembrado["a"], sembrado["b"]
    pid_antes, *_ = await _estado_de_la_conexion(pg_session_factory)

    r = await pg_client.get("/v1/dummy-resources", cookies=_cookies(a["users"], a["tenant"]))
    assert r.status_code == 200 and r.json()["total"] == 1

    pid, tenant, _, filas = await _estado_de_la_conexion(pg_session_factory)
    assert pid == pid_antes, "el pool no reusó la conexión: el test no prueba nada"
    assert tenant in (None, "")
    assert filas == 0

    # y el request siguiente, de B, por la misma conexión, ve solo lo de B
    r = await pg_client.get("/v1/dummy-resources", cookies=_cookies(b["users"], b["tenant"]))
    assert [i["id"] for i in r.json()["items"]] == [str(b["dummy_resources"])]
    pid_b, tenant_b, _, filas_b = await _estado_de_la_conexion(pg_session_factory)
    assert pid_b == pid_antes and tenant_b in (None, "") and filas_b == 0


async def test_request_que_falla_tampoco_deja_el_tenant(pg_client, pg_session_factory, sembrado):
    a, b = sembrado["a"], sembrado["b"]
    pid_antes, *_ = await _estado_de_la_conexion(pg_session_factory)
    # 404 levantado DENTRO de la transacción, después del SET LOCAL → rollback
    r = await pg_client.get(
        f"/v1/dummy-resources/{b['dummy_resources']}", cookies=_cookies(a["users"], a["tenant"])
    )
    assert r.status_code == 404
    pid, tenant, _, filas = await _estado_de_la_conexion(pg_session_factory)
    assert pid == pid_antes and tenant in (None, "") and filas == 0


async def test_request_sin_cookie_tras_uno_autenticado_no_hereda(
    pg_client, pg_session_factory, sembrado
):
    a = sembrado["a"]
    assert (
        await pg_client.get("/v1/dummy-resources", cookies=_cookies(a["users"], a["tenant"]))
    ).status_code == 200
    assert (await pg_client.get("/v1/dummy-resources")).status_code == 401
    _, tenant, _, filas = await _estado_de_la_conexion(pg_session_factory)
    assert tenant in (None, "") and filas == 0


# ── 4 · Búsqueda de identidad del login ───────────────────────────────────────


async def test_el_guc_viejo_de_la_ventana_no_abre_users(pg_session_factory, sembrado):
    """Lo que antes abría `users` de todos los tenants (con `password_hash`) ya no
    abre nada: la política de `users` no tiene excepción."""
    async with pg_session_factory() as session, session.begin():
        await session.execute(text("SET LOCAL app.identity_lookup = 'on'"))
        assert await session.scalar(text("SELECT count(*) FROM users")) == 0
        for tabla in TABLAS_TENANT:
            assert await session.scalar(text(f"SELECT count(*) FROM {tabla}")) == 0, tabla


async def test_el_guc_viejo_sin_tenant_no_escribe(pg_session_factory, pg_admin_engine, sembrado):
    b = sembrado["b"]
    async with pg_session_factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.identity_lookup = 'on'"))
            r = await session.execute(
                text("UPDATE users SET password_hash = 'robado' WHERE id = :i"),
                {"i": b["users"]},
            )
            assert r.rowcount == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            async with session.begin():
                await session.execute(text("SET LOCAL app.identity_lookup = 'on'"))
                await session.execute(
                    text(_insert_en("users")), {"i": uuid.uuid4(), "t": b["tenant"]}
                )
    async with pg_admin_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT password_hash FROM users WHERE id = :i"), {"i": b["users"]}
            )
            != "robado"
        )


async def test_con_tenant_a_no_se_edita_un_usuario_de_b(
    pg_session_factory, pg_admin_engine, sembrado
):
    a, b = sembrado["a"], sembrado["b"]
    async with pg_session_factory() as session, session.begin():
        await set_tenant_context(session, a["tenant"])
        await session.execute(text("SET LOCAL app.identity_lookup = 'on'"))
        r = await session.execute(
            text("UPDATE users SET password_hash = 'robado' WHERE id = :i"), {"i": b["users"]}
        )
        assert r.rowcount == 0
    async with pg_admin_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT password_hash FROM users WHERE id = :i"), {"i": b["users"]}
            )
            != "robado"
        )


async def test_con_tenant_a_no_se_puede_secuestrar_un_usuario_de_b(
    pg_session_factory, pg_admin_engine, sembrado
):
    """Era el xfail de BUG-1: `UPDATE users SET tenant_id = <A> WHERE id = <usuario de B>`
    con la ventana abierta mudaba al usuario de B al tenant A. Ya no hay ventana."""
    a, b = sembrado["a"], sembrado["b"]
    async with pg_session_factory() as session:
        try:
            async with session.begin():
                await set_tenant_context(session, a["tenant"])
                await session.execute(text("SET LOCAL app.identity_lookup = 'on'"))
                await session.execute(
                    text("UPDATE users SET tenant_id = :a, password_hash = 'robado' WHERE id = :i"),
                    {"a": a["tenant"], "i": b["users"]},
                )
        except DBAPIError:
            pass
    assert await _tenant_de_fila(pg_admin_engine, "users", b["users"]) == b["tenant"]


async def test_la_funcion_de_login_no_enumera_ni_abre_la_tabla(pg_session_factory, sembrado):
    """La función devuelve la fila de UN email exacto, y no deja `users` abierta después."""
    a = sembrado["a"]
    async with pg_session_factory() as session, session.begin():
        filas = (
            await session.execute(
                text("SELECT id, tenant_id FROM auth_lookup_user(:e)"),
                {"e": "owner-a@rls.example.com"},
            )
        ).all()
        assert [(f.id, f.tenant_id) for f in filas] == [(a["users"], a["tenant"])]
        for patron in ("%", "%@rls.example.com", ""):
            assert (
                await session.execute(text("SELECT * FROM auth_lookup_user(:e)"), {"e": patron})
            ).all() == [], patron
        assert await session.scalar(text("SELECT count(*) FROM users")) == 0


async def test_login_fija_el_tenant_y_solo_ve_el_usuario_propio(
    pg_client, pg_session_factory, sembrado, monkeypatch
):
    """Espía dentro del request de login, justo antes de armar la respuesta, y después
    en la conexión que vuelve al pool."""
    from app.api.v1 import auth

    visto: dict[str, object] = {}
    original = auth._me

    async def espia(session, user):
        visto["tenant"] = await session.scalar(
            text("SELECT current_setting('app.tenant_id', TRUE)")
        )
        visto["users"] = set((await session.execute(text("SELECT id FROM users"))).scalars())
        return await original(session, user)

    monkeypatch.setattr(auth, "_me", espia)
    r = await pg_client.post(
        "/v1/auth/login", json={"email": "owner-a@rls.example.com", "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    assert visto["tenant"] == str(sembrado["a"]["tenant"])
    assert visto["users"] == {sembrado["a"]["users"]}

    _, tenant, lookup, filas = await _estado_de_la_conexion(pg_session_factory)
    assert tenant in (None, "") and lookup in (None, "", "off") and filas == 0


@pytest.mark.parametrize(
    "email,password",
    [("owner-b@rls.example.com", "incorrecta"), ("nadie@rls.example.com", PASSWORD)],
    ids=["password-incorrecta", "email-inexistente"],
)
async def test_login_fallido_no_deja_contexto(
    pg_client, pg_session_factory, sembrado, email, password
):
    r = await pg_client.post("/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 401
    _, tenant, lookup, filas = await _estado_de_la_conexion(pg_session_factory)
    assert tenant in (None, "") and lookup in (None, "", "off") and filas == 0


async def test_login_con_cookie_de_otro_tenant_no_mezcla_contextos(
    pg_client, pg_session_factory, sembrado
):
    # B autenticado hace login como A: la respuesta es de A y la conexión queda limpia
    a, b = sembrado["a"], sembrado["b"]
    r = await pg_client.post(
        "/v1/auth/login",
        json={"email": "owner-a@rls.example.com", "password": PASSWORD},
        cookies=_cookies(b["users"], b["tenant"]),
    )
    assert r.status_code == 200
    assert r.json()["tenant"]["id"] == str(a["tenant"])
    _, tenant, lookup, filas = await _estado_de_la_conexion(pg_session_factory)
    assert tenant in (None, "") and lookup in (None, "", "off") and filas == 0


async def test_ningun_codigo_de_la_app_usa_la_ventana_y_solo_el_login_busca_identidad():
    """La ventana no existe más en `app/` (fuera de tests), y la búsqueda sin tenant
    (`find_login_candidate`) solo se llama desde `auth.login`."""
    app_dir = Path(__file__).resolve().parents[2]
    llamadas: set[tuple[str, str]] = set()
    for py in app_dir.rglob("*.py"):
        if "tests" in py.parts:
            continue
        fuente = py.read_text()
        assert "identity_lookup" not in fuente, py
        for fn in ast.walk(ast.parse(fuente)):
            if not isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            for nodo in ast.walk(fn):
                if (
                    isinstance(nodo, ast.Call)
                    and getattr(nodo.func, "attr", None) == "find_login_candidate"
                ):
                    llamadas.add((py.name, fn.name))
    assert llamadas == {("auth.py", "login")}


async def test_el_guc_viejo_con_set_de_sesion_tampoco_abre_nada(pg_admin_engine, sembrado):
    """Invertido a propósito. Antes: `SET app.identity_lookup = 'on'` (sin LOCAL) con el
    rol de runtime quedaba pegado a la conexión y el siguiente checkout veía `users` de
    todos los tenants — convención, no frontera. Ahora el GUC no significa nada."""
    engine = create_async_engine(
        _as_app_role(_with_driver(PG_TEST_URL, "asyncpg")), pool_size=1, max_overflow=0
    )
    try:
        async with engine.connect() as conn:
            pid = await conn.scalar(text("SELECT pg_backend_pid()"))
            await conn.execute(text("SET app.identity_lookup = 'on'"))
            await conn.commit()
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT pg_backend_pid()")) == pid
            assert await conn.scalar(text("SELECT count(*) FROM users")) == 0
            await conn.execute(text("RESET app.identity_lookup"))
            await conn.commit()
    finally:
        await engine.dispose()
