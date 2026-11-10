"""`BodyLimitMiddleware` (PR 4.5, hallazgo 1 del /code-review) sobre una app ASGI mínima."""

import json

import pytest

from app.api.body_limit import BodyLimitMiddleware

TOPE = 100


async def _app_que_lee_todo(scope, receive, send):
    """Lee el cuerpo entero y responde 200. Si la lectura falla, deja subir el error."""
    while True:
        message = await receive()
        if not message.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def _llamar(app, path="/v1/branding/logo", headers=(), trozos=(b"",)):
    enviados: list[dict] = []
    pendientes = list(trozos)

    async def receive():
        trozo = pendientes.pop(0) if pendientes else b""
        return {"type": "http.request", "body": trozo, "more_body": bool(pendientes)}

    async def send(message):
        enviados.append(message)

    scope = {"type": "http", "path": path, "headers": list(headers)}
    await BodyLimitMiddleware(app, prefixes=("/v1/branding/",), max_bytes=TOPE)(
        scope, receive, send
    )
    return enviados


async def test_sin_content_length_y_la_app_no_atrapa_el_error_igual_es_413():
    enviados = await _llamar(_app_que_lee_todo, trozos=[b"x" * 60, b"x" * 60, b"x" * 60])
    assert enviados[0]["status"] == 413
    assert json.loads(enviados[1]["body"])["detail"]["code"] == "FILE_TOO_LARGE"
    assert len(enviados) == 2


async def test_dentro_del_tope_pasa():
    enviados = await _llamar(_app_que_lee_todo, trozos=[b"x" * 50, b"x" * 50])
    assert enviados[0]["status"] == 200


async def test_otras_rutas_no_tienen_tope():
    enviados = await _llamar(_app_que_lee_todo, path="/v1/settings", trozos=[b"x" * 500])
    assert enviados[0]["status"] == 200


async def test_content_length_grande_no_llama_a_la_app():
    llamada = False

    async def app(scope, receive, send):
        nonlocal llamada
        llamada = True

    enviados = await _llamar(app, headers=[(b"content-length", b"101")])
    assert not llamada and enviados[0]["status"] == 413


async def test_un_error_de_la_app_que_no_es_del_tope_sube():
    async def app(scope, receive, send):
        raise RuntimeError("otro")

    with pytest.raises(RuntimeError):
        await _llamar(app)


async def test_lo_que_no_es_http_pasa_derecho():
    vistos = []

    async def app(scope, receive, send):
        vistos.append(scope["type"])

    await BodyLimitMiddleware(app, prefixes=("/v1/branding/",), max_bytes=TOPE)(
        {"type": "lifespan"}, None, None
    )
    assert vistos == ["lifespan"]
