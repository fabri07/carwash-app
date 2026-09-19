"""main.py: health, ready, handlers de error, cabeceras y endpoint de prueba de Sentry."""

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app import bootstrap
from app.main import create_app, db_fingerprint


async def test_health_devuelve_env_commit_y_fingerprint(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["env"] == "test"
    assert set(body) == {"status", "env", "commit", "db_fingerprint"}
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-trace-id"]


async def test_trace_id_del_cliente_se_respeta(client):
    r = await client.get("/health", headers={"X-Trace-Id": "abc-123"})
    assert r.headers["x-trace-id"] == "abc-123"


def test_fingerprint_distingue_bases_y_no_incluye_credenciales():
    a = db_fingerprint("postgresql://u:p@staging.db:5432/carwash")
    b = db_fingerprint("postgresql://u:p@prod.db:5432/carwash")
    assert a != b
    assert a == db_fingerprint("postgresql+asyncpg://otro:clave@staging.db:5432/carwash")


async def test_ready_degradado_da_503(client):
    # En test no hay Redis real (REDIS_URL apunta a un puerto cerrado).
    r = await client.get("/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"
    assert r.json()["checks"]["redis"]["ok"] is False


async def test_ready_ok(client, monkeypatch):
    from app import main

    async def ok():
        return main.ReadyCheck(ok=True)

    monkeypatch.setattr(main, "_check_database_ready", ok)
    monkeypatch.setattr(main, "_check_redis_ready", ok)
    r = await client.get("/ready")
    assert r.status_code == 200 and r.json()["status"] == "ready"


async def test_hsts_solo_en_produccion(settings_env):
    settings_env(APP_ENV="production")
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as ac:
        r = await ac.get("/health")
    assert "strict-transport-security" in r.headers


async def test_http_exception_generica_usa_error_response(client):
    r = await client.get("/no-existe")
    assert r.status_code == 404
    assert r.json() == {"detail": {"code": "NOT_FOUND", "message": "Not Found"}}


async def _app_con_ruta(path, handler):
    app = create_app()
    app.add_api_route(path, handler, methods=["GET"], response_model=None)
    return app


async def test_409_generico_no_es_duplicate_idempotent():
    async def boom():
        raise HTTPException(status_code=409, detail="otro conflicto")

    app = await _app_con_ruta("/x", boom)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as ac:
        r = await ac.get("/x")
    assert r.json()["detail"]["code"] == "CONFLICT"


async def test_excepcion_no_manejada_es_500_sin_detalle_y_va_a_sentry(monkeypatch):
    capturadas = []
    monkeypatch.setattr("app.main.sentry_sdk.capture_exception", capturadas.append)

    async def boom():
        raise RuntimeError("secreto interno")

    app = await _app_con_ruta("/x", boom)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://t") as ac:
        r = await ac.get("/x")
    assert r.status_code == 500
    assert r.json()["detail"]["code"] == "INTERNAL_ERROR"
    assert "secreto" not in r.text
    assert len(capturadas) == 1


async def test_pydantic_validation_error_en_handler_es_422():
    from pydantic import BaseModel

    class M(BaseModel):
        n: int

    async def invalido():
        M.model_validate({"n": "no"})

    app = await _app_con_ruta("/x", invalido)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://t") as ac:
        r = await ac.get("/x")
    assert r.status_code == 422


async def test_debug_boom_requiere_sesion_y_manda_el_error(client, cookies_a, monkeypatch):
    assert (await client.post("/v1/debug/boom")).status_code == 401
    capturadas = []
    monkeypatch.setattr("app.main.sentry_sdk.capture_exception", capturadas.append)
    transport = client._transport
    transport.raise_app_exceptions = False
    r = await client.post("/v1/debug/boom", cookies=cookies_a)
    assert r.status_code == 500 and len(capturadas) == 1


def test_debug_no_esta_en_el_contrato():
    rutas = create_app().openapi()["paths"]
    assert not any(p.startswith("/v1/debug") for p in rutas)
    assert any(isinstance(r, APIRoute) and r.path == "/v1/debug/boom" for r in create_app().routes)


async def test_bootstrap_tolerante_con_dependencias_caidas(monkeypatch):
    async def cae():
        raise ConnectionError("abajo")

    monkeypatch.setattr(bootstrap, "_init_database", cae)
    monkeypatch.setattr(bootstrap, "_init_redis", cae)
    await bootstrap.startup()  # no levanta


async def test_bootstrap_ok_y_shutdown(monkeypatch):
    from app.persistence.db import redis as redis_mod

    class _Pool:
        async def ping(self):
            return True

    monkeypatch.setattr(redis_mod, "get_redis_pool", lambda: _Pool())
    await bootstrap._init_database()  # SQLite in-memory del engine de la app
    await bootstrap._init_redis()
    await bootstrap.shutdown()


async def test_lifespan_arranca_y_cierra(monkeypatch):
    async def nada():
        return None

    monkeypatch.setattr("app.main.startup", nada)
    monkeypatch.setattr("app.main.shutdown", nada)
    app = create_app()
    async with app.router.lifespan_context(app):
        pass


@pytest.mark.parametrize("prod", [False, True])
def test_client_ip_confia_en_x_real_ip_solo_en_produccion(settings_env, prod):
    from starlette.requests import Request

    from app.api.v1 import deps

    settings_env(APP_ENV="production" if prod else "test")
    scope = {
        "type": "http",
        "headers": [(b"x-real-ip", b"203.0.113.9")],
        "client": ("10.0.0.1", 1234),
    }
    esperado = "203.0.113.9" if prod else "10.0.0.1"
    assert deps.client_ip(Request(scope)) == esperado
    assert deps.rate_limit_key(Request({"type": "http", "headers": [], "client": None})) == (
        "unknown"
    )


def test_client_ip_avisa_una_sola_vez_si_falta_el_header(settings_env, monkeypatch):
    from starlette.requests import Request

    from app.api.v1 import deps

    settings_env(APP_ENV="production")
    monkeypatch.setattr(deps, "_aviso_sin_x_real_ip_emitido", False)
    req = Request({"type": "http", "headers": [], "client": ("10.0.0.1", 1)})
    assert deps.client_ip(req) == "10.0.0.1"
    assert deps._aviso_sin_x_real_ip_emitido is True
    assert deps.client_ip(req) == "10.0.0.1"


@pytest.mark.parametrize("variable", ["GIT_COMMIT_SHA", "RAILWAY_GIT_COMMIT_SHA"])
def test_el_commit_sale_de_la_variable_que_fija_el_workflow(monkeypatch, variable):
    # Los deploys van por `railway up`, que no inyecta RAILWAY_GIT_COMMIT_SHA: el
    # workflow fija GIT_COMMIT_SHA. Se aceptan las dos por si un servicio se conecta a Git.
    from app.config.settings import Settings  # noqa: PLC0415

    monkeypatch.delenv("GIT_COMMIT_SHA", raising=False)
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.setenv(variable, "abc123")
    assert Settings(_env_file=None).GIT_COMMIT == "abc123"
