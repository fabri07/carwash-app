"""CRUD mínimo sobre el recurso dummy — sujeto del test cruzado de T3.

- Colección con `PaginatedResponse` (ADR-0006).
- `POST` con `Idempotency-Key` opcional → 409 `DUPLICATE_IDEMPOTENT` en el replay.
- `tenant_id` siempre del token; recurso ajeno o inexistente → el MISMO 404.
- `DELETE` anula (ADR-0003) y exige `OWNER` (ADR-0005).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.v1.deps import CurrentTenantId, DbSession, require_role
from app.api.v1.errors import (
    ERROR_RESPONSES,
    IDEMPOTENT_RESPONSES,
    IdempotencyKeyHeader,
    duplicate_idempotent,
    not_found,
)
from app.api.v1.pagination import Paginacion
from app.application.idempotency import claim_idempotency_key
from app.domain.roles import Role
from app.domain.void import VoidReason
from app.persistence.models.dummy_resource import DummyResource
from app.persistence.models.user import User
from app.persistence.repositories.dummy_resource_repository import DummyResourceRepository
from app.schemas.common import PaginatedResponse
from app.schemas.dummy_resource import (
    DummyResourceCreate,
    DummyResourceResponse,
    DummyResourceUpdate,
)

router = APIRouter()


@router.get(
    "",
    response_model=PaginatedResponse[DummyResourceResponse],
    responses=ERROR_RESPONSES,
)
async def list_dummy_resources(
    tenant_id: CurrentTenantId, session: DbSession, page: Paginacion
) -> PaginatedResponse[DummyResourceResponse]:
    result = await DummyResourceRepository(session).list_by_tenant(
        tenant_id, limit=page.limit, offset=page.offset
    )
    return PaginatedResponse[DummyResourceResponse](
        items=[DummyResourceResponse.model_validate(item) for item in result.items],
        total=result.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "",
    response_model=DummyResourceResponse,
    status_code=status.HTTP_201_CREATED,
    responses=IDEMPOTENT_RESPONSES,
)
async def create_dummy_resource(
    body: DummyResourceCreate,
    tenant_id: CurrentTenantId,
    session: DbSession,
    idempotency_key: IdempotencyKeyHeader = None,
) -> DummyResource:
    if idempotency_key is not None and not await claim_idempotency_key(
        session, tenant_id, idempotency_key, "dummy_resources.create"
    ):
        raise duplicate_idempotent()
    return await DummyResourceRepository(session).save(
        DummyResource(id=uuid.uuid4(), tenant_id=tenant_id, name=body.name)
    )


@router.get("/{id}", response_model=DummyResourceResponse, responses=ERROR_RESPONSES)
async def get_dummy_resource(
    id: uuid.UUID, tenant_id: CurrentTenantId, session: DbSession
) -> DummyResource:
    entity = await DummyResourceRepository(session).get_by_id(id, tenant_id)
    if entity is None:
        raise not_found()
    return entity


@router.patch("/{id}", response_model=DummyResourceResponse, responses=ERROR_RESPONSES)
async def update_dummy_resource(
    id: uuid.UUID, body: DummyResourceUpdate, tenant_id: CurrentTenantId, session: DbSession
) -> DummyResource:
    repo = DummyResourceRepository(session)
    entity = await repo.get_by_id(id, tenant_id)
    if entity is None:
        raise not_found()
    entity.name = body.name
    return await repo.save(entity)


@router.delete(
    "/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERROR_RESPONSES,
)
async def delete_dummy_resource(
    id: uuid.UUID,
    owner: Annotated[User, Depends(require_role(Role.OWNER))],
    session: DbSession,
) -> Response:
    voided = await DummyResourceRepository(session).void(
        id, owner.tenant_id, VoidReason.PEDIDO_DEL_USUARIO
    )
    if voided is None:
        raise not_found()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
