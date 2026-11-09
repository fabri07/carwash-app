"""ABM de la configuración del catálogo — FASE-4-CONTRATO §4 (PR 4.2a).

`/vehicle-sizes`, `/resources`, `/business-hours` y `/payment-methods`. Lectura: cualquier usuario
autenticado. Escritura: permiso `CATALOGO_EDITAR` (el `OWNER` pasa siempre). `POST` con
`Idempotency-Key` opcional (409 `DUPLICATE_IDEMPOTENT` en el replay); `DELETE` anula (ADR-0003).
Recurso ajeno o inexistente → el mismo 404.
"""

import uuid

from fastapi import APIRouter, Response, status

from app.api.v1._shared import EditorUser, guard, once, taken
from app.api.v1.deps import AuthenticatedUser, DbSession
from app.api.v1.errors import ERROR_RESPONSES, IDEMPOTENT_RESPONSES, IdempotencyKeyHeader
from app.api.v1.pagination import Paginacion
from app.application.services.catalog_service import CatalogService
from app.domain.errors import ErrorCode
from app.persistence.models.catalog import BusinessHours, PaymentMethod, Resource, VehicleSize
from app.persistence.models.user import User
from app.schemas.catalog import (
    BusinessHoursCreate,
    BusinessHoursResponse,
    BusinessHoursUpdate,
    PaymentMethodCreate,
    PaymentMethodResponse,
    PaymentMethodUpdate,
    ResourceCreate,
    ResourceResponse,
    ResourceUpdate,
    VehicleSizeCreate,
    VehicleSizeResponse,
    VehicleSizeUpdate,
)
from app.schemas.common import PaginatedResponse


def _service(session: DbSession, user: User) -> CatalogService:
    return CatalogService(session, user.tenant_id, user.id)


# ── /vehicle-sizes ───────────────────────────────────────────────────────────

vehicle_sizes = APIRouter()


@vehicle_sizes.get(
    "", response_model=PaginatedResponse[VehicleSizeResponse], responses=ERROR_RESPONSES
)
async def list_vehicle_sizes(
    user: AuthenticatedUser, session: DbSession, page: Paginacion
) -> PaginatedResponse[VehicleSizeResponse]:
    result = await _service(session, user).list_vehicle_sizes(limit=page.limit, offset=page.offset)
    return PaginatedResponse[VehicleSizeResponse](
        items=[VehicleSizeResponse.model_validate(i) for i in result.items],
        total=result.total,
        limit=page.limit,
        offset=page.offset,
    )


@vehicle_sizes.post(
    "",
    response_model=VehicleSizeResponse,
    status_code=status.HTTP_201_CREATED,
    responses=IDEMPOTENT_RESPONSES,
)
async def create_vehicle_size(
    body: VehicleSizeCreate,
    session: DbSession,
    user: EditorUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> VehicleSize:
    await once(session, user, idempotency_key, "vehicle_sizes.create")
    return await guard(
        _service(session, user).create_vehicle_size(body.code, body.label, body.sort_order),
        taken(ErrorCode.CODE_TAKEN, "A size with that code"),
    )


@vehicle_sizes.get("/{id}", response_model=VehicleSizeResponse, responses=ERROR_RESPONSES)
async def get_vehicle_size(
    id: uuid.UUID, user: AuthenticatedUser, session: DbSession
) -> VehicleSize:
    return await guard(_service(session, user).get_vehicle_size(id))


@vehicle_sizes.patch("/{id}", response_model=VehicleSizeResponse, responses=IDEMPOTENT_RESPONSES)
async def update_vehicle_size(
    id: uuid.UUID,
    body: VehicleSizeUpdate,
    session: DbSession,
    user: EditorUser,
) -> VehicleSize:
    return await guard(
        _service(session, user).update_vehicle_size(
            id, label=body.label, sort_order=body.sort_order
        )
    )


@vehicle_sizes.delete(
    "/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=IDEMPOTENT_RESPONSES
)
async def delete_vehicle_size(id: uuid.UUID, session: DbSession, user: EditorUser) -> Response:
    await guard(_service(session, user).void_vehicle_size(id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── /resources ───────────────────────────────────────────────────────────────

resources = APIRouter()


@resources.get("", response_model=PaginatedResponse[ResourceResponse], responses=ERROR_RESPONSES)
async def list_resources(
    user: AuthenticatedUser, session: DbSession, page: Paginacion
) -> PaginatedResponse[ResourceResponse]:
    result = await _service(session, user).list_resources(limit=page.limit, offset=page.offset)
    return PaginatedResponse[ResourceResponse](
        items=[ResourceResponse.model_validate(i) for i in result.items],
        total=result.total,
        limit=page.limit,
        offset=page.offset,
    )


@resources.post(
    "",
    response_model=ResourceResponse,
    status_code=status.HTTP_201_CREATED,
    responses=IDEMPOTENT_RESPONSES,
)
async def create_resource(
    body: ResourceCreate,
    session: DbSession,
    user: EditorUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> Resource:
    await once(session, user, idempotency_key, "resources.create")
    return await guard(
        _service(session, user).create_resource(body.name, body.sort_order),
        taken(ErrorCode.NAME_TAKEN, "A station with that name"),
    )


@resources.get("/{id}", response_model=ResourceResponse, responses=ERROR_RESPONSES)
async def get_resource(id: uuid.UUID, user: AuthenticatedUser, session: DbSession) -> Resource:
    return await guard(_service(session, user).get_resource(id))


@resources.patch("/{id}", response_model=ResourceResponse, responses=IDEMPOTENT_RESPONSES)
async def update_resource(
    id: uuid.UUID,
    body: ResourceUpdate,
    session: DbSession,
    user: EditorUser,
) -> Resource:
    return await guard(
        _service(session, user).update_resource(id, name=body.name, sort_order=body.sort_order),
        taken(ErrorCode.NAME_TAKEN, "A station with that name"),
    )


@resources.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=IDEMPOTENT_RESPONSES)
async def delete_resource(id: uuid.UUID, session: DbSession, user: EditorUser) -> Response:
    await guard(_service(session, user).void_resource(id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── /business-hours ──────────────────────────────────────────────────────────

business_hours = APIRouter()


@business_hours.get(
    "", response_model=PaginatedResponse[BusinessHoursResponse], responses=ERROR_RESPONSES
)
async def list_business_hours(
    user: AuthenticatedUser, session: DbSession, page: Paginacion
) -> PaginatedResponse[BusinessHoursResponse]:
    result = await _service(session, user).list_business_hours(limit=page.limit, offset=page.offset)
    return PaginatedResponse[BusinessHoursResponse](
        items=[BusinessHoursResponse.model_validate(i) for i in result.items],
        total=result.total,
        limit=page.limit,
        offset=page.offset,
    )


@business_hours.post(
    "",
    response_model=BusinessHoursResponse,
    status_code=status.HTTP_201_CREATED,
    responses=IDEMPOTENT_RESPONSES,
)
async def create_business_hours(
    body: BusinessHoursCreate,
    session: DbSession,
    user: EditorUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> BusinessHours:
    await once(session, user, idempotency_key, "business_hours.create")
    return await guard(
        _service(session, user).add_business_hours(body.weekday, body.opens_at, body.closes_at)
    )


@business_hours.get("/{id}", response_model=BusinessHoursResponse, responses=ERROR_RESPONSES)
async def get_business_hours(
    id: uuid.UUID, user: AuthenticatedUser, session: DbSession
) -> BusinessHours:
    return await guard(_service(session, user).get_business_hours(id))


@business_hours.patch("/{id}", response_model=BusinessHoursResponse, responses=IDEMPOTENT_RESPONSES)
async def update_business_hours(
    id: uuid.UUID,
    body: BusinessHoursUpdate,
    session: DbSession,
    user: EditorUser,
) -> BusinessHours:
    return await guard(
        _service(session, user).update_business_hours(
            id, weekday=body.weekday, opens_at=body.opens_at, closes_at=body.closes_at
        )
    )


@business_hours.delete(
    "/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=IDEMPOTENT_RESPONSES
)
async def delete_business_hours(id: uuid.UUID, session: DbSession, user: EditorUser) -> Response:
    await guard(_service(session, user).void_business_hours(id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── /payment-methods ─────────────────────────────────────────────────────────

payment_methods = APIRouter()


@payment_methods.get(
    "", response_model=PaginatedResponse[PaymentMethodResponse], responses=ERROR_RESPONSES
)
async def list_payment_methods(
    user: AuthenticatedUser, session: DbSession, page: Paginacion
) -> PaginatedResponse[PaymentMethodResponse]:
    result = await _service(session, user).list_payment_methods(
        limit=page.limit, offset=page.offset
    )
    return PaginatedResponse[PaymentMethodResponse](
        items=[PaymentMethodResponse.model_validate(i) for i in result.items],
        total=result.total,
        limit=page.limit,
        offset=page.offset,
    )


@payment_methods.post(
    "",
    response_model=PaymentMethodResponse,
    status_code=status.HTTP_201_CREATED,
    responses=IDEMPOTENT_RESPONSES,
)
async def create_payment_method(
    body: PaymentMethodCreate,
    session: DbSession,
    user: EditorUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> PaymentMethod:
    await once(session, user, idempotency_key, "payment_methods.create")
    return await guard(
        _service(session, user).create_payment_method(
            body.code,
            body.label,
            commission_bps=body.commission_bps,
            settlement_account=body.settlement_account,
            for_income=body.for_income,
            for_expense=body.for_expense,
        ),
        taken(ErrorCode.CODE_TAKEN, "A payment method with that code"),
    )


@payment_methods.get("/{id}", response_model=PaymentMethodResponse, responses=ERROR_RESPONSES)
async def get_payment_method(
    id: uuid.UUID, user: AuthenticatedUser, session: DbSession
) -> PaymentMethod:
    return await guard(_service(session, user).get_payment_method(id))


@payment_methods.patch(
    "/{id}", response_model=PaymentMethodResponse, responses=IDEMPOTENT_RESPONSES
)
async def update_payment_method(
    id: uuid.UUID,
    body: PaymentMethodUpdate,
    session: DbSession,
    user: EditorUser,
) -> PaymentMethod:
    return await guard(
        _service(session, user).update_payment_method(
            id,
            label=body.label,
            commission_bps=body.commission_bps,
            settlement_account=body.settlement_account,
            clear_settlement_account=(
                "settlement_account" in body.model_fields_set and body.settlement_account is None
            ),
            for_income=body.for_income,
            for_expense=body.for_expense,
        )
    )


@payment_methods.delete(
    "/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=IDEMPOTENT_RESPONSES
)
async def delete_payment_method(id: uuid.UUID, session: DbSession, user: EditorUser) -> Response:
    await guard(_service(session, user).void_payment_method(id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
