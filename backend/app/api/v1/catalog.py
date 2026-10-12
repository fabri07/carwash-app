"""Catálogos de operación — FASE-4-CONTRATO §4 (PR 4.2).

Tamaños de vehículo, puestos, franjas horarias y medios de pago. Mismo patrón que
`permission_profiles.py`:

- Lectura: cualquier usuario del tenant. Escritura: permiso `CATALOGO_EDITAR` (el `OWNER`
  pasa siempre, Y5).
- Colecciones con `PaginatedResponse` (ADR-0006); `POST` con `Idempotency-Key` opcional.
- `tenant_id` siempre del token; recurso ajeno o inexistente → el MISMO 404 (Y11).
- `DELETE` anula (ADR-0003). Un puesto con turnos que lo ocupan a futuro → 409
  `RESOURCE_IN_USE`.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Response, status

from app.api.v1.deps import CatalogEditor, DbSession, TenantMember
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
from app.application.services.catalog_service import CatalogService, ResourceInUseError
from app.application.services.errors import AlreadyExistsError, NotFoundError
from app.domain.errors import ErrorCode
from app.domain.exceptions import InvalidAmountError
from app.persistence.models.catalog import BusinessHours, PaymentMethod, Resource, VehicleSize
from app.persistence.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.configuracion import (
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

vehicle_sizes = APIRouter()
resources = APIRouter()
business_hours = APIRouter()
payment_methods = APIRouter()

_NO_CONTENT = status.HTTP_204_NO_CONTENT


def _service(session: DbSession, user: User) -> CatalogService:
    return CatalogService(session, user.tenant_id, user.id)


async def _claim(session: DbSession, user: User, key: str | None, action: str) -> None:
    if key is not None and not await claim_idempotency_key(session, user.tenant_id, key, action):
        raise duplicate_idempotent()


def _code_taken() -> ApiError:
    return ApiError(status.HTTP_409_CONFLICT, ErrorCode.CODE_TAKEN, "That code is already in use.")


def _name_taken() -> ApiError:
    return ApiError(status.HTTP_409_CONFLICT, ErrorCode.NAME_TAKEN, "That name is already in use.")


def _invalid(exc: InvalidAmountError) -> ApiError:
    return ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, ErrorCode.VALIDATION_ERROR, str(exc))


# ── Tamaños ───────────────────────────────────────────────────────────────────


@vehicle_sizes.get(
    "", response_model=PaginatedResponse[VehicleSizeResponse], responses=ERROR_RESPONSES
)
async def list_vehicle_sizes(
    user: TenantMember, session: DbSession, page: Paginacion
) -> PaginatedResponse[VehicleSizeResponse]:
    result = await _service(session, user).list_vehicle_sizes(limit=page.limit, offset=page.offset)
    return PaginatedResponse[VehicleSizeResponse](
        items=[VehicleSizeResponse.model_validate(x) for x in result.items],
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
    user: CatalogEditor,
    session: DbSession,
    idempotency_key: IdempotencyKeyHeader = None,
) -> VehicleSize:
    await _claim(session, user, idempotency_key, "vehicle_sizes.create")
    try:
        return await _service(session, user).create_vehicle_size(
            body.code, body.label, body.sort_order
        )
    except AlreadyExistsError as exc:
        raise _code_taken() from exc


@vehicle_sizes.get("/{id}", response_model=VehicleSizeResponse, responses=ERROR_RESPONSES)
async def get_vehicle_size(id: uuid.UUID, user: TenantMember, session: DbSession) -> VehicleSize:
    try:
        return await _service(session, user).get_vehicle_size(id)
    except NotFoundError as exc:
        raise not_found() from exc


@vehicle_sizes.patch("/{id}", response_model=VehicleSizeResponse, responses=IDEMPOTENT_RESPONSES)
async def update_vehicle_size(
    id: uuid.UUID, body: VehicleSizeUpdate, user: CatalogEditor, session: DbSession
) -> VehicleSize:
    try:
        return await _service(session, user).update_vehicle_size(
            id, code=body.code, label=body.label, sort_order=body.sort_order
        )
    except NotFoundError as exc:
        raise not_found() from exc
    except AlreadyExistsError as exc:
        raise _code_taken() from exc


@vehicle_sizes.delete("/{id}", status_code=_NO_CONTENT, responses=ERROR_RESPONSES)
async def delete_vehicle_size(id: uuid.UUID, user: CatalogEditor, session: DbSession) -> Response:
    try:
        await _service(session, user).void_vehicle_size(id)
    except NotFoundError as exc:
        raise not_found() from exc
    return Response(status_code=_NO_CONTENT)


# ── Puestos ───────────────────────────────────────────────────────────────────


@resources.get("", response_model=PaginatedResponse[ResourceResponse], responses=ERROR_RESPONSES)
async def list_resources(
    user: TenantMember, session: DbSession, page: Paginacion
) -> PaginatedResponse[ResourceResponse]:
    result = await _service(session, user).list_resources(limit=page.limit, offset=page.offset)
    return PaginatedResponse[ResourceResponse](
        items=[ResourceResponse.model_validate(x) for x in result.items],
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
    user: CatalogEditor,
    session: DbSession,
    idempotency_key: IdempotencyKeyHeader = None,
) -> Resource:
    await _claim(session, user, idempotency_key, "resources.create")
    try:
        return await _service(session, user).create_resource(body.name, body.sort_order)
    except AlreadyExistsError as exc:
        raise _name_taken() from exc


@resources.get("/{id}", response_model=ResourceResponse, responses=ERROR_RESPONSES)
async def get_resource(id: uuid.UUID, user: TenantMember, session: DbSession) -> Resource:
    try:
        return await _service(session, user).get_resource(id)
    except NotFoundError as exc:
        raise not_found() from exc


@resources.patch("/{id}", response_model=ResourceResponse, responses=IDEMPOTENT_RESPONSES)
async def update_resource(
    id: uuid.UUID, body: ResourceUpdate, user: CatalogEditor, session: DbSession
) -> Resource:
    try:
        return await _service(session, user).update_resource(
            id, name=body.name, sort_order=body.sort_order
        )
    except NotFoundError as exc:
        raise not_found() from exc
    except AlreadyExistsError as exc:
        raise _name_taken() from exc


@resources.delete("/{id}", status_code=_NO_CONTENT, responses=IDEMPOTENT_RESPONSES)
async def delete_resource(id: uuid.UUID, user: CatalogEditor, session: DbSession) -> Response:
    try:
        await _service(session, user).void_resource(id, now=datetime.now(UTC))
    except NotFoundError as exc:
        raise not_found() from exc
    except ResourceInUseError as exc:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            ErrorCode.RESOURCE_IN_USE,
            "The resource has upcoming bookings.",
        ) from exc
    return Response(status_code=_NO_CONTENT)


# ── Franjas horarias ──────────────────────────────────────────────────────────


@business_hours.get(
    "", response_model=PaginatedResponse[BusinessHoursResponse], responses=ERROR_RESPONSES
)
async def list_business_hours(
    user: TenantMember, session: DbSession, page: Paginacion
) -> PaginatedResponse[BusinessHoursResponse]:
    result = await _service(session, user).list_business_hours(limit=page.limit, offset=page.offset)
    return PaginatedResponse[BusinessHoursResponse](
        items=[BusinessHoursResponse.model_validate(x) for x in result.items],
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
    user: CatalogEditor,
    session: DbSession,
    idempotency_key: IdempotencyKeyHeader = None,
) -> BusinessHours:
    await _claim(session, user, idempotency_key, "business_hours.create")
    try:
        return await _service(session, user).add_business_hours(
            body.weekday, body.opens_at, body.closes_at
        )
    except InvalidAmountError as exc:
        raise _invalid(exc) from exc


@business_hours.get("/{id}", response_model=BusinessHoursResponse, responses=ERROR_RESPONSES)
async def get_business_hours(
    id: uuid.UUID, user: TenantMember, session: DbSession
) -> BusinessHours:
    try:
        return await _service(session, user).get_business_hours(id)
    except NotFoundError as exc:
        raise not_found() from exc


@business_hours.patch("/{id}", response_model=BusinessHoursResponse, responses=ERROR_RESPONSES)
async def update_business_hours(
    id: uuid.UUID, body: BusinessHoursUpdate, user: CatalogEditor, session: DbSession
) -> BusinessHours:
    try:
        return await _service(session, user).update_business_hours(
            id, weekday=body.weekday, opens_at=body.opens_at, closes_at=body.closes_at
        )
    except NotFoundError as exc:
        raise not_found() from exc
    except InvalidAmountError as exc:
        raise _invalid(exc) from exc


@business_hours.delete("/{id}", status_code=_NO_CONTENT, responses=ERROR_RESPONSES)
async def delete_business_hours(id: uuid.UUID, user: CatalogEditor, session: DbSession) -> Response:
    try:
        await _service(session, user).void_business_hours(id)
    except NotFoundError as exc:
        raise not_found() from exc
    return Response(status_code=_NO_CONTENT)


# ── Medios de pago ────────────────────────────────────────────────────────────


@payment_methods.get(
    "", response_model=PaginatedResponse[PaymentMethodResponse], responses=ERROR_RESPONSES
)
async def list_payment_methods(
    user: TenantMember, session: DbSession, page: Paginacion
) -> PaginatedResponse[PaymentMethodResponse]:
    result = await _service(session, user).list_payment_methods(
        limit=page.limit, offset=page.offset
    )
    return PaginatedResponse[PaymentMethodResponse](
        items=[PaymentMethodResponse.model_validate(x) for x in result.items],
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
    user: CatalogEditor,
    session: DbSession,
    idempotency_key: IdempotencyKeyHeader = None,
) -> PaymentMethod:
    await _claim(session, user, idempotency_key, "payment_methods.create")
    try:
        return await _service(session, user).create_payment_method(
            body.code,
            body.label,
            commission_bps=body.commission_bps,
            settlement_account=body.settlement_account,
            for_income=body.for_income,
            for_expense=body.for_expense,
        )
    except AlreadyExistsError as exc:
        raise _code_taken() from exc


@payment_methods.get("/{id}", response_model=PaymentMethodResponse, responses=ERROR_RESPONSES)
async def get_payment_method(
    id: uuid.UUID, user: TenantMember, session: DbSession
) -> PaymentMethod:
    try:
        return await _service(session, user).get_payment_method(id)
    except NotFoundError as exc:
        raise not_found() from exc


@payment_methods.patch(
    "/{id}", response_model=PaymentMethodResponse, responses=IDEMPOTENT_RESPONSES
)
async def update_payment_method(
    id: uuid.UUID, body: PaymentMethodUpdate, user: CatalogEditor, session: DbSession
) -> PaymentMethod:
    # `settlement_account` presente en `null` o vacío lo borra; ausente no lo toca.
    settlement = (
        (body.settlement_account or "") if "settlement_account" in body.model_fields_set else None
    )
    try:
        return await _service(session, user).update_payment_method(
            id,
            code=body.code,
            label=body.label,
            commission_bps=body.commission_bps,
            settlement_account=settlement,
            for_income=body.for_income,
            for_expense=body.for_expense,
        )
    except NotFoundError as exc:
        raise not_found() from exc
    except AlreadyExistsError as exc:
        raise _code_taken() from exc


@payment_methods.delete("/{id}", status_code=_NO_CONTENT, responses=ERROR_RESPONSES)
async def delete_payment_method(id: uuid.UUID, user: CatalogEditor, session: DbSession) -> Response:
    try:
        await _service(session, user).void_payment_method(id)
    except NotFoundError as exc:
        raise not_found() from exc
    return Response(status_code=_NO_CONTENT)
