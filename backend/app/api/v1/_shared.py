"""Piezas comunes de los routers de catálogo (configuración y servicios) — FASE-4-CONTRATO §4.

`guard` traduce los errores de servicio a la API (404 uniforme, 409, 422), `once` reclama el
`Idempotency-Key` y `taken` arma el 409 de "ya existe". Públicas: las usan `catalog.py` y
`services.py`.
"""

from collections.abc import Coroutine
from typing import Annotated, Any

from fastapi import Depends, status

from app.api.v1.deps import DbSession, require_permission
from app.api.v1.errors import ApiError, duplicate_idempotent, not_found
from app.application.idempotency import claim_idempotency_key
from app.application.services.errors import AlreadyExistsError, InUseError, NotFoundError
from app.domain.errors import ErrorCode
from app.domain.exceptions import InvalidAmountError
from app.domain.permissions import Permission
from app.persistence.models.user import User

#: Escritura del catálogo. Las lecturas usan `AuthenticatedUser`.
EditorUser = Annotated[User, Depends(require_permission(Permission.CATALOGO_EDITAR))]


def invalid(exc: InvalidAmountError) -> ApiError:
    return ApiError(status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.VALIDATION_ERROR, str(exc))


def taken(code: ErrorCode, what: str) -> ApiError:
    return ApiError(status.HTTP_409_CONFLICT, code, f"{what} already exists.")


async def once(session: DbSession, user: User, key: str | None, scope: str) -> None:
    if key is not None and not await claim_idempotency_key(session, user.tenant_id, key, scope):
        raise duplicate_idempotent()


async def guard[T](call: Coroutine[Any, Any, T], on_taken: ApiError | None = None) -> T:
    """Traduce los errores de servicio comunes a la API: 404 uniforme, 409 y 422."""
    try:
        return await call
    except NotFoundError as exc:
        raise not_found() from exc
    except AlreadyExistsError as exc:
        if on_taken is None:  # pragma: no cover  # toda ruta con único pasa su error
            raise
        raise on_taken from exc
    except InUseError as exc:
        raise ApiError(status.HTTP_409_CONFLICT, ErrorCode.IN_USE, str(exc)) from exc
    except InvalidAmountError as exc:
        raise invalid(exc) from exc


__all__ = ["EditorUser", "guard", "invalid", "once", "taken"]
