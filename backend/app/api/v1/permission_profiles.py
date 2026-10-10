"""Perfiles de permisos — FASE-4-CONTRATO §2 y §4 (PR 4.1). Solo el `OWNER` (Y5).

- Colección con `PaginatedResponse` (ADR-0006).
- `POST` con `Idempotency-Key` opcional → 409 `DUPLICATE_IDEMPOTENT` en el replay.
- `tenant_id` siempre del token; recurso ajeno o inexistente → el MISMO 404.
- `DELETE` anula (ADR-0003); un perfil con empleados vivos → 409 `PROFILE_IN_USE`.

Toma el lugar de `dummy_resources` como sujeto de los tests HTTP de aislamiento (Y12).
"""

import uuid

from fastapi import APIRouter, Response, status

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
from app.application.services.team_service import ProfileInUseError, TeamService
from app.domain.errors import ErrorCode
from app.persistence.models.permission_profile import PermissionProfile
from app.persistence.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.team import (
    PermissionProfileCreate,
    PermissionProfileResponse,
    PermissionProfileUpdate,
)

router = APIRouter()


def _service(session: DbSession, owner: User) -> TeamService:
    return TeamService(session, owner.tenant_id, owner.id)


def _name_taken() -> ApiError:
    return ApiError(
        status.HTTP_409_CONFLICT, ErrorCode.NAME_TAKEN, "A profile with that name already exists."
    )


@router.get(
    "",
    response_model=PaginatedResponse[PermissionProfileResponse],
    responses=ERROR_RESPONSES,
)
async def list_permission_profiles(
    owner: OwnerUser, session: DbSession, page: Paginacion
) -> PaginatedResponse[PermissionProfileResponse]:
    result = await _service(session, owner).list_profiles(limit=page.limit, offset=page.offset)
    return PaginatedResponse[PermissionProfileResponse](
        items=[PermissionProfileResponse.model_validate(p) for p in result.items],
        total=result.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "",
    response_model=PermissionProfileResponse,
    status_code=status.HTTP_201_CREATED,
    responses=IDEMPOTENT_RESPONSES,
)
async def create_permission_profile(
    body: PermissionProfileCreate,
    owner: OwnerUser,
    session: DbSession,
    idempotency_key: IdempotencyKeyHeader = None,
) -> PermissionProfile:
    if idempotency_key is not None and not await claim_idempotency_key(
        session, owner.tenant_id, idempotency_key, "permission_profiles.create"
    ):
        raise duplicate_idempotent()
    try:
        return await _service(session, owner).create_profile(body.name, body.permissions)
    except AlreadyExistsError as exc:
        raise _name_taken() from exc


@router.get("/{id}", response_model=PermissionProfileResponse, responses=ERROR_RESPONSES)
async def get_permission_profile(
    id: uuid.UUID, owner: OwnerUser, session: DbSession
) -> PermissionProfile:
    try:
        return await _service(session, owner).get_profile(id)
    except NotFoundError as exc:
        raise not_found() from exc


@router.patch("/{id}", response_model=PermissionProfileResponse, responses=IDEMPOTENT_RESPONSES)
async def update_permission_profile(
    id: uuid.UUID, body: PermissionProfileUpdate, owner: OwnerUser, session: DbSession
) -> PermissionProfile:
    try:
        return await _service(session, owner).update_profile(
            id, name=body.name, permissions=body.permissions
        )
    except NotFoundError as exc:
        raise not_found() from exc
    except AlreadyExistsError as exc:
        raise _name_taken() from exc


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=IDEMPOTENT_RESPONSES)
async def delete_permission_profile(
    id: uuid.UUID, owner: OwnerUser, session: DbSession
) -> Response:
    try:
        await _service(session, owner).void_profile(id)
    except NotFoundError as exc:
        raise not_found() from exc
    except ProfileInUseError as exc:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            ErrorCode.PROFILE_IN_USE,
            "The profile is assigned to active staff members.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
