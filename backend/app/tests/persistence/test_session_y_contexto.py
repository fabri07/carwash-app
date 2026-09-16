"""Dependency de sesión y contexto de tenant (parte SQLite; lo de RLS va en *_pg)."""

import uuid

import pytest
from starlette.requests import Request

from app.persistence.db import tenant_context
from app.persistence.db.session import get_db_session, tenant_id_from_request
from app.utils.cookies import ACCESS_COOKIE
from app.utils.security import create_access_token, create_refresh_token


def _request(cookie: str | None) -> Request:
    headers = [(b"cookie", f"{ACCESS_COOKIE}={cookie}".encode())] if cookie else []
    return Request({"type": "http", "headers": headers, "method": "GET", "path": "/"})


def test_tenant_del_token_de_la_cookie():
    tid = uuid.uuid4()
    token = create_access_token({"sub": str(uuid.uuid4()), "tenant_id": str(tid)})
    assert tenant_id_from_request(_request(token)) == tid


@pytest.mark.parametrize(
    "cookie",
    [
        None,
        "basura",
        create_refresh_token({"sub": "x", "tenant_id": str(uuid.uuid4())}),
        create_access_token({"sub": "x", "tenant_id": "no-es-uuid"}),
        create_access_token({"sub": "x"}),
    ],
    # IDs estables: con el token como id, cada worker de xdist colecta nombres distintos.
    ids=["sin-cookie", "basura", "refresh-como-access", "tenant-no-uuid", "sin-tenant"],
)
def test_sin_token_valido_no_hay_tenant(cookie):
    assert tenant_id_from_request(_request(cookie)) is None


async def test_get_db_session_abre_una_transaccion():
    token = create_access_token({"sub": "x", "tenant_id": str(uuid.uuid4())})
    gen = get_db_session(_request(token))
    session = await gen.__anext__()
    assert session.in_transaction()
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


async def test_set_tenant_context_rechaza_lo_que_no_es_uuid(db_session):
    with pytest.raises(TypeError):
        await tenant_context.set_tenant_context(
            db_session, "'; DROP TABLE users; --"
        )  # string a propósito


async def test_en_sqlite_el_contexto_es_no_op(db_session):
    await tenant_context.set_tenant_context(db_session, uuid.uuid4())
    assert not hasattr(tenant_context, "set_identity_lookup"), "la ventana del login no vuelve"


def test_sql_de_rls_tiene_using_y_with_check_identicos():
    from app.persistence.db.rls import (
        create_auth_lookup,
        disable_rls,
        drop_auth_lookup,
        enable_rls,
        grant_app_role,
    )

    create = enable_rls("dummy_resources")
    assert any("FORCE ROW LEVEL SECURITY" in s for s in create)
    policy = next(s for s in create if s.startswith("CREATE POLICY"))
    using, with_check = policy.split(" WITH CHECK ")
    assert "USING (tenant_id = " in using and with_check.startswith("(tenant_id = ")
    tenants = next(s for s in enable_rls("tenants", column="id") if "CREATE POLICY" in s)
    assert "USING (id = " in tenants and "WITH CHECK (id = " in tenants
    assert any(s.startswith("DROP POLICY") for s in disable_rls("users"))
    assert any("carwash_app" in s for s in grant_app_role(["users"]))

    lookup = "\n".join(create_auth_lookup())
    assert "SECURITY DEFINER" in lookup and "SET search_path" in lookup
    assert "REVOKE ALL ON FUNCTION auth_lookup_user(text) FROM PUBLIC" in lookup
    assert "FOR SELECT TO CURRENT_USER" in lookup
    assert any("DROP FUNCTION" in s for s in drop_auth_lookup())
