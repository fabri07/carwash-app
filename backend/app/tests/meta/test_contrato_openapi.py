"""ADR-0013 — el contrato es generable, completo y está al día."""

from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.cli import dump_openapi
from app.main import create_app
from app.tests.meta._rutas import BACKEND


def test_todo_endpoint_declara_response_model():
    culpables = [
        f"{r.methods} {r.path}"
        for r in create_app().routes
        if isinstance(r, APIRoute) and r.response_model is None and r.status_code != 204
    ]
    assert not culpables, f"sin response_model: {culpables}"


def test_todo_error_usa_el_envelope_unico():
    schemas = create_app().openapi()["components"]["schemas"]
    assert "ErrorResponse" in schemas and "ErrorCode" in schemas
    assert "DUPLICATE_IDEMPOTENT" in schemas["ErrorCode"]["enum"]
    assert set(schemas["Role"]["enum"]) == {"OWNER", "STAFF"}


async def test_openapi_esta_apagado_en_produccion(settings_env):
    # httpx y no `starlette.testclient`: importar el TestClient dispara avisos de
    # deprecación de anyio/httpx que no son nuestros.
    settings_env(APP_ENV="production")
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="https://t") as tc:
        for ruta in ("/openapi.json", "/docs", "/redoc"):
            assert (await tc.get(ruta)).status_code == 404
        # y el endpoint de prueba de Sentry no existe en producción
        assert (await tc.post("/v1/debug/boom")).status_code == 404


def test_openapi_json_commiteado_esta_al_dia():
    # Mismo chequeo que `git diff --exit-code openapi.json` en CI (A10), sin git.
    actual = (BACKEND / "openapi.json").read_text(encoding="utf-8")
    assert (
        actual == dump_openapi.render()
    ), "backend/openapi.json está vencido: correr `uv run python -m app.cli.dump_openapi`"


def test_dump_openapi_escribe_el_archivo(tmp_path):
    destino = tmp_path / "openapi.json"
    assert dump_openapi.main([str(destino)]) == 0
    assert destino.read_text(encoding="utf-8") == dump_openapi.render()
