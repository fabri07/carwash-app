"""Helper único de errores HTTP y de `Idempotency-Key`.

En Véktor el `raise HTTPException(409, …)` está copiado 11 veces a mano. Acá hay
UNA forma. El contrato exacto del 409 importa: `useOfflineSubmit` trata
`409` + `detail.code == "DUPLICATE_IDEMPOTENT"` como éxito.
"""

from typing import Annotated, Any

from fastapi import Header, HTTPException, status

from app.domain.errors import ErrorCode
from app.schemas.common import ErrorResponse


class ApiError(HTTPException):
    """HTTPException con cuerpo `ErrorResponse`."""

    def __init__(
        self,
        status_code: int,
        code: ErrorCode,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            status_code=status_code,
            detail={"code": code.value, "message": message},
            headers=headers,
        )
        self.code = code


def not_found() -> ApiError:
    """404 idéntico para "no existe" y "no es tuyo" — el mensaje no filtra existencia."""
    return ApiError(status.HTTP_404_NOT_FOUND, ErrorCode.NOT_FOUND, "Resource not found.")


def duplicate_idempotent() -> ApiError:
    return ApiError(
        status.HTTP_409_CONFLICT,
        ErrorCode.DUPLICATE_IDEMPOTENT,
        "This Idempotency-Key was already used.",
    )


def unauthenticated(message: str = "Not authenticated.") -> ApiError:
    return ApiError(status.HTTP_401_UNAUTHORIZED, ErrorCode.UNAUTHENTICATED, message)


def forbidden(message: str = "Not allowed.") -> ApiError:
    return ApiError(status.HTTP_403_FORBIDDEN, ErrorCode.FORBIDDEN, message)


#: Header opcional. Máx. 200 caracteres: suficiente para un UUID con prefijo.
IdempotencyKeyHeader = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=200)
]

#: Para `responses=` de los routers: así `ErrorResponse` y `ErrorCode` quedan en el
#: OpenAPI y el frontend recibe el código como literal tipado.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
}
IDEMPOTENT_RESPONSES: dict[int | str, dict[str, Any]] = {
    **ERROR_RESPONSES,
    409: {"model": ErrorResponse},
}
