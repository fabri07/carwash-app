"""T3b #1 — la transacción del request se cierra ANTES de enviar la respuesta.

Con el scope por defecto de FastAPI (≥0.121), una dependencia `yield` corre su salida
DESPUÉS de mandar la respuesta: `get_db_session` commiteaba después del 201. Un commit
fallido se reportaba como éxito (la cola offline del frontend descartaba el ítem) y
un `/auth/me` inmediato podía ver 401.

`httpx.ASGITransport` junta toda la respuesta antes de devolverla y oculta el orden.
Acá se llama a la app ASGI a mano y se registra, en una sola lista, cada mensaje que
la app manda y el commit real de SQLAlchemy.
"""

import json
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Session

from app.domain.roles import Role
from app.main import create_app
from app.persistence.db.session import get_db_session, make_session_dependency
from app.persistence.models import Tenant, User
from app.tests.conftest import session_cookies
from app.utils.security import hash_password


async def _llamar_asgi(app, method: str, path: str, body: dict, cookies: dict[str, str], eventos):
    cuerpo = json.dumps(body).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"test"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(cuerpo)).encode()),
            (b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 443),
        "state": {},
    }
    entregado = False

    async def receive():
        nonlocal entregado
        if not entregado:
            entregado = True
            return {"type": "http.request", "body": cuerpo, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        eventos.append(message["type"])
        if message["type"] == "http.response.start":
            eventos.append(f"status:{message['status']}")

    await app(scope, receive, send)


@pytest.fixture
async def app_con_commits_reales(isolated_db_engine):
    factory = async_sessionmaker(isolated_db_engine, expire_on_commit=False, autoflush=False)
    async with factory() as s, s.begin():
        tenant = Tenant(id=uuid.uuid4(), name="orden")
        s.add(tenant)
        await s.flush()
        user = User(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            email="orden@example.com",
            password_hash=hash_password("x" * 10),
            role=Role.OWNER,
        )
        s.add(user)
    from app.api.rate_limit import limiter

    limiter.reset()
    app = create_app()
    # La dependency de producción, sobre un engine con commits de verdad.
    app.dependency_overrides[get_db_session] = make_session_dependency(factory)
    return app, session_cookies(user)


async def test_el_commit_ocurre_antes_de_enviar_la_respuesta(app_con_commits_reales):
    app, cookies = app_con_commits_reales
    eventos: list[str] = []

    def _al_commitear(_session):
        eventos.append("commit")

    event.listen(Session, "before_commit", _al_commitear)
    try:
        await _llamar_asgi(app, "POST", "/v1/dummy-resources", {"name": "x"}, cookies, eventos)
    finally:
        event.remove(Session, "before_commit", _al_commitear)

    assert "status:201" in eventos, eventos
    assert "commit" in eventos, eventos
    assert eventos.index("commit") < eventos.index(
        "http.response.start"
    ), f"la transacción se cerró DESPUÉS de empezar a responder: {eventos}"


async def test_un_commit_que_falla_no_se_reporta_como_exito(app_con_commits_reales):
    app, cookies = app_con_commits_reales
    eventos: list[str] = []

    def _falla(_session):
        raise RuntimeError("commit roto a propósito")

    event.listen(Session, "before_commit", _falla)
    try:
        with pytest.raises(RuntimeError):
            await _llamar_asgi(app, "POST", "/v1/dummy-resources", {"name": "x"}, cookies, eventos)
    finally:
        event.remove(Session, "before_commit", _falla)
    assert "status:201" not in eventos, f"se respondió 201 con el commit roto: {eventos}"
