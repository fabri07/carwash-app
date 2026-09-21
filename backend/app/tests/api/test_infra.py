"""main.py: health, ready, handlers de error, cabeceras y endpoint de prueba de Sentry."""

import logging
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app import bootstrap
from app.main import create_app, db_fingerprint

#: El único origen que `conftest.py` pone en CORS_ORIGINS.
ORIGEN_PERMITIDO = "https://app.carwash.test"


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
    monkeypatch.setattr(main, "_check_schema_ready", ok)
    r = await client.get("/ready")
    assert r.status_code == 200 and r.json()["status"] == "ready"


async def test_ready_mira_el_esquema_y_no_solo_que_la_base_conteste(client, monkeypatch):
    """Una base VACÍA acepta conexiones igual: `SELECT 1` funciona sin una sola tabla.

    Staging, 2026-09-21: el `preDeployCommand` no estaba configurado en Railway, las
    migraciones nunca corrieron y el deploy salió VERDE — `/health` no toca tablas y
    `/ready` solo abría una conexión. El dueño lo descubrió cuando no pudo entrar.
    Por eso `/ready` compara la revisión aplicada contra el head de este código.
    """
    from app import main

    async def ok():
        return main.ReadyCheck(ok=True)

    async def sin_migrar():
        return main.ReadyCheck(ok=False, error="sin migrar: no existe alembic_version")

    monkeypatch.setattr(main, "_check_database_ready", ok)
    monkeypatch.setattr(main, "_check_redis_ready", ok)
    monkeypatch.setattr(main, "_check_schema_ready", sin_migrar)

    r = await client.get("/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"
    assert r.json()["checks"]["schema"]["ok"] is False


def test_el_head_de_alembic_se_lee_del_codigo():
    """El head sale de los archivos de migración, no de una constante que se copia."""
    from app.main import head_de_alembic

    head = head_de_alembic()
    assert head and head != "unknown"

    # Tiene que ser EL head real: el mismo que resuelve alembic por su cuenta.
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    raiz = Path(__file__).resolve().parents[3]
    cfg = Config(str(raiz / "alembic.ini"))
    cfg.set_main_option("script_location", str(raiz / "app" / "persistence" / "migrations"))
    assert head == ScriptDirectory.from_config(cfg).get_current_head()


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


async def test_el_500_le_llega_al_navegador_con_cors(monkeypatch):
    """Un 500 SIN `access-control-allow-origin` el navegador lo descarta: el fetch
    falla como error de red, axios se queda sin `response` y el frontend muestra
    "No hay conexión con el servidor" (`frontend/src/lib/errors.ts`).

    Pasó en staging el 2026-09-21. La base estaba sin migrar y todo endpoint que
    tocaba una tabla daba 500, pero el dueño vio "no hay conexión" y se puso a
    revisar su internet. El handler de `Exception` corre en el `ServerErrorMiddleware`
    de Starlette, que envuelve TODO — incluido el `CORSMiddleware`, que por eso nunca
    llega a tocar la respuesta. Por eso el error se captura además en un middleware
    interno al CORS.
    """
    monkeypatch.setattr("app.main.sentry_sdk.capture_exception", lambda exc: None)

    async def boom():
        raise RuntimeError("secreto interno")

    app = await _app_con_ruta("/x", boom)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://t") as ac:
        r = await ac.get("/x", headers={"Origin": ORIGEN_PERMITIDO})

    assert r.status_code == 500
    assert r.json()["detail"]["code"] == "INTERNAL_ERROR"
    assert "secreto" not in r.text
    # Sin estas dos cabeceras el navegador nunca le entrega el cuerpo al JS.
    assert r.headers.get("access-control-allow-origin") == ORIGEN_PERMITIDO
    assert r.headers.get("access-control-allow-credentials") == "true"


async def test_el_500_deja_el_traceback_en_el_log(monkeypatch, caplog):
    """Atrapar la excepción nosotros le saca a uvicorn la chance de imprimir el
    traceback (el `ServerErrorMiddleware`, que es quien la re-lanza, ya no la ve).
    El log estructurado tiene que compensarlo: sin traceback, un 500 en Railway se
    vuelve indiagnosticable. El del 2026-09-21 decía la causa exacta en la última
    línea: `UndefinedTableError: relation "tenants" does not exist`.
    """
    monkeypatch.setattr("app.main.sentry_sdk.capture_exception", lambda exc: None)

    async def boom():
        raise RuntimeError("causa raíz")

    app = await _app_con_ruta("/x", boom)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="app.main"):
        async with AsyncClient(transport=transport, base_url="https://t") as ac:
            await ac.get("/x")

    # `format_exc_info` (app/observability/logger.py) convierte la excepción en el
    # traceback ya formateado; sin ese procesador acá quedaría solo su `repr`.
    mensajes = [str(r.msg) for r in caplog.records]
    assert any("Traceback" in m and "causa raíz" in m for m in mensajes), mensajes


async def test_un_origen_ajeno_no_recibe_cors_ni_en_el_500(monkeypatch):
    """La red de seguridad del test anterior no puede volverse un agujero: a un
    origen que no está en CORS_ORIGINS el 500 le llega igual de mudo que un 200."""
    monkeypatch.setattr("app.main.sentry_sdk.capture_exception", lambda exc: None)

    async def boom():
        raise RuntimeError("x")

    app = await _app_con_ruta("/x", boom)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://t") as ac:
        r = await ac.get("/x", headers={"Origin": "https://atacante.example"})

    assert r.status_code == 500
    assert "access-control-allow-origin" not in r.headers


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
