"""Tope de tamaño del cuerpo para las subidas — FASE-4-CONTRATO §11.8 (PR 4.5).

FastAPI parsea el `multipart` (y vuelca el archivo a disco) **antes** de resolver las
dependencias: la autenticación corre después de recibir el cuerpo entero, y Starlette no pone tope
a las partes de archivo. Sin esto, cualquiera —incluso sin sesión— podría mandar gigas a
`/v1/branding/logo`.

Middleware ASGI puro (no `BaseHTTPMiddleware`) para ver el cuerpo antes que nadie:

- con `Content-Length` mayor al tope responde 413 `FILE_TOO_LARGE` **sin leer el cuerpo**;
- sin `Content-Length` (chunked) o si miente, cuenta los bytes a medida que llegan y, apenas se
  pasa, deja de leer (la lectura falla) y **reemplaza** la respuesta que arme la app (FastAPI
  convierte el error de lectura en un 400 genérico) por el mismo 413.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.domain.errors import ErrorCode
from app.schemas.common import ErrorResponse

_MESSAGE = "The request body is too large."


class _BodyTooLargeError(Exception):
    """La lectura del cuerpo pasó el tope."""


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, prefixes: tuple[str, ...], max_bytes: int) -> None:
        self.app = app
        self.prefixes = prefixes
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.prefixes):
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.max_bytes):
            await self._reject(send)
            return

        seen = 0
        exceeded = False
        responded = False

        async def limited_receive() -> Message:
            nonlocal seen, exceeded
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    exceeded = True
                    raise _BodyTooLargeError
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal responded
            if not exceeded:
                await send(message)
                return
            # La respuesta de la app (400 por el error de lectura) se cambia por el 413.
            if message["type"] == "http.response.start" and not responded:
                responded = True
                await self._reject(send)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise
            if not responded:
                responded = True
                await self._reject(send)

    async def _reject(self, send: Send) -> None:
        body = (
            ErrorResponse.model_validate(
                {"detail": {"code": ErrorCode.FILE_TOO_LARGE, "message": _MESSAGE}}
            )
            .model_dump_json(exclude_none=True)
            .encode()
        )
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    # El cliente quizá siga mandando el cuerpo: que no reuse la conexión.
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
