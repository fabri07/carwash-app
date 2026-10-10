"""Empleados — FASE-4-CONTRATO §2, §3.1 y §4 (PR 4.1). Solo el `OWNER` (Y5).

Un empleado es un `STAFF` con usuario, clave inicial y perfil (D4-3). El `OWNER` no se ve
ni se edita por acá: su id responde 404, igual que uno ajeno o inexistente.

- `DELETE` anula y sube `token_version`: sus sesiones mueren en el request siguiente.
- `POST /{id}/reset-password`: clave nueva elegida por el dueño, `must_change_password` y
  sesiones revocadas.
- Usuario o email ya usados (son únicos globales) → 409 `USERNAME_TAKEN` / `EMAIL_TAKEN`.

Ese 409 dice si un email o usuario existe en **cualquier** negocio: es el mismo oráculo
que `/auth/register`. Por eso las escrituras que pueden darlo (y las que hashean una clave)
llevan rate limit propio, `STAFF_WRITE_LIMIT`, y no el general de 200/min.
"""

import uuid

from fastapi import APIRouter, Request, Response, status

from app.api.rate_limit import limiter
from app.api.v1.deps import DbSession, OwnerUser
from app.api.v1.errors import (
    ERROR_RESPONSES,
    IDEMPOTENT_RESPONSES,
    ApiError,
    IdempotencyKeyHeader,
    duplicate_idempotent,
    not_found,
)
from app.api.v1.pagination import Paginacion
from app.application.idempotency import claim_idempotency_key
from app.application.services.errors import AlreadyExistsError, NotFoundError
from app.application.services.team_service import MissingIdentifierError, TeamService
from app.domain.errors import ErrorCode
from app.persistence.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.team import StaffCreate, StaffPasswordReset, StaffResponse, StaffUpdate
from app.utils.security import hash_password_async

router = APIRouter()

#: Por IP. Alcanza de sobra para dar de alta un equipo; corta la enumeración (ver arriba).
STAFF_WRITE_LIMIT = "20/5minutes"


def _service(session: DbSession, owner: User) -> TeamService:
    return TeamService(session, owner.tenant_id, owner.id)


def _identity_taken(exc: AlreadyExistsError) -> ApiError:
    if "email" in str(exc):
        return ApiError(status.HTTP_409_CONFLICT, ErrorCode.EMAIL_TAKEN, "Email already in use.")
    return ApiError(status.HTTP_409_CONFLICT, ErrorCode.USERNAME_TAKEN, "Username already in use.")


@router.get("", response_model=PaginatedResponse[StaffResponse], responses=ERROR_RESPONSES)
async def list_staff(
    owner: OwnerUser, session: DbSession, page: Paginacion
) -> PaginatedResponse[StaffResponse]:
    result = await _service(session, owner).list_staff(limit=page.limit, offset=page.offset)
    return PaginatedResponse[StaffResponse](
        items=[StaffResponse.model_validate(u) for u in result.items],
        total=result.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "",
    response_model=StaffResponse,
    status_code=status.HTTP_201_CREATED,
    responses=IDEMPOTENT_RESPONSES,
)
@limiter.limit(STAFF_WRITE_LIMIT)
async def create_staff(
    request: Request,
    body: StaffCreate,
    owner: OwnerUser,
    session: DbSession,
    idempotency_key: IdempotencyKeyHeader = None,
) -> User:
    if idempotency_key is not None and not await claim_idempotency_key(
        session, owner.tenant_id, idempotency_key, "staff.create"
    ):
        raise duplicate_idempotent()
    try:
        return await _service(session, owner).create_staff(
            username=body.username,
            email=body.email,
            password_hash=await hash_password_async(body.password),
            permission_profile_id=body.permission_profile_id,
        )
    except NotFoundError as exc:  # el perfil no es de este negocio
        raise not_found() from exc
    except AlreadyExistsError as exc:
        raise _identity_taken(exc) from exc


@router.get("/{id}", response_model=StaffResponse, responses=ERROR_RESPONSES)
async def get_staff(id: uuid.UUID, owner: OwnerUser, session: DbSession) -> User:
    try:
        return await _service(session, owner).get_staff(id)
    except NotFoundError as exc:
        raise not_found() from exc


@router.patch("/{id}", response_model=StaffResponse, responses=IDEMPOTENT_RESPONSES)
@limiter.limit(STAFF_WRITE_LIMIT)
async def update_staff(
    request: Request, id: uuid.UUID, body: StaffUpdate, owner: OwnerUser, session: DbSession
) -> User:
    clear_email = "email" in body.model_fields_set and body.email is None
    try:
        return await _service(session, owner).update_staff(
            id,
            username=body.username,
            email=body.email,
            clear_email=clear_email,
            permission_profile_id=body.permission_profile_id,
        )
    except NotFoundError as exc:
        raise not_found() from exc
    except AlreadyExistsError as exc:
        raise _identity_taken(exc) from exc
    except MissingIdentifierError as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCode.VALIDATION_ERROR,
            "A staff member needs a username or an email.",
        ) from exc


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=ERROR_RESPONSES)
async def delete_staff(id: uuid.UUID, owner: OwnerUser, session: DbSession) -> Response:
    try:
        await _service(session, owner).void_staff(id)
    except NotFoundError as exc:
        raise not_found() from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{id}/reset-password",
    response_model=StaffResponse,
    responses=ERROR_RESPONSES,
)
@limiter.limit(STAFF_WRITE_LIMIT)
async def reset_staff_password(
    request: Request,
    id: uuid.UUID,
    body: StaffPasswordReset,
    owner: OwnerUser,
    session: DbSession,
) -> User:
    try:
        return await _service(session, owner).reset_password(
            id, await hash_password_async(body.password)
        )
    except NotFoundError as exc:
        raise not_found() from exc
