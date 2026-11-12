"""Agenda: bloqueos, aperturas especiales y disponibilidad interna — FASE-5-CONTRATO §4.1 (PR 5.1).

- Lectura (`GET`) con `AGENDA_VER`; alta y anulación con `TURNOS_GESTIONAR` (§2). El `OWNER` pasa
  siempre.
- `POST /agenda/blocks` devuelve el bloqueo **y** los turnos activos que quedan adentro: no los
  cancela (R-T-052), la pantalla avisa.
- `DELETE` anula (ADR-0003). Recurso ajeno o inexistente → el mismo 404.
- `GET /agenda/availability`: los inicios libres de un día para servicios y tamaño (§5), con los
  puestos libres en cada uno (uso interno; el turnero público de 5.2 no los muestra).
"""

import datetime as dt
import uuid
from collections.abc import Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.v1._shared import guard, once
from app.api.v1.deps import DbSession, require_permission
from app.api.v1.errors import ERROR_RESPONSES, IDEMPOTENT_RESPONSES, ApiError, IdempotencyKeyHeader
from app.api.v1.pagination import Paginacion
from app.application.services.agenda_service import AgendaService, BlockCreated
from app.domain.errors import ErrorCode
from app.domain.exceptions import GuardFailedError, InvalidParameterError
from app.domain.permissions import Permission
from app.persistence.models.user import User
from app.schemas.agenda import (
    AvailabilityResponse,
    AvailableStart,
    BlockCreate,
    BlockCreatedResponse,
    BlockResponse,
    OpeningCreate,
    OpeningResponse,
    OverlappingBooking,
)
from app.schemas.common import PaginatedResponse
from app.utils.datetime_utils import utcnow

ViewerUser = Annotated[User, Depends(require_permission(Permission.AGENDA_VER))]
ManagerUser = Annotated[User, Depends(require_permission(Permission.TURNOS_GESTIONAR))]

VALIDATION_RESPONSES: dict[int | str, dict[str, Any]] = {
    **IDEMPOTENT_RESPONSES,
    422: IDEMPOTENT_RESPONSES[404],
}

router = APIRouter()


def _service(session: DbSession, user: User) -> AgendaService:
    return AgendaService(session, user.tenant_id, user.id)


async def _guard[T](call: Coroutine[Any, Any, T]) -> T:
    """`guard` de siempre más las guardas del dominio (rango invertido, servicio sin precio para
    el tamaño, motivo largo): 422 con el mensaje de sistema."""
    try:
        return await guard(call)
    except (GuardFailedError, InvalidParameterError) as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.VALIDATION_ERROR, str(exc)
        ) from exc


def _block_created(result: BlockCreated) -> BlockCreatedResponse:
    return BlockCreatedResponse(
        block=BlockResponse.model_validate(result.block),
        overlapping_bookings=[
            OverlappingBooking(
                id=b.id,
                code=b.code,
                status=b.status,
                resource_id=b.resource_id,
                start_at=b.start_at,
                end_at=b.end_at,
                customer_name=result.customer_names.get(b.customer_id, ""),
            )
            for b in result.overlapping
        ],
    )


# ── /agenda/blocks ───────────────────────────────────────────────────────────


@router.get("/blocks", response_model=PaginatedResponse[BlockResponse], responses=ERROR_RESPONSES)
async def list_blocks(
    user: ViewerUser, session: DbSession, page: Paginacion
) -> PaginatedResponse[BlockResponse]:
    """Bloqueos vigentes (los que todavía no terminaron), por inicio."""
    result = await _service(session, user).list_blocks(
        now=utcnow(), limit=page.limit, offset=page.offset
    )
    return PaginatedResponse[BlockResponse](
        items=[BlockResponse.model_validate(b) for b in result.items],
        total=result.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/blocks",
    response_model=BlockCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    responses=VALIDATION_RESPONSES,
)
async def create_block(
    body: BlockCreate,
    session: DbSession,
    user: ManagerUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> BlockCreatedResponse:
    await once(session, user, idempotency_key, "agenda.blocks.create")
    result = await _guard(
        _service(session, user).create_block(
            body.to_range(), resource_id=body.resource_id, reason=body.reason
        )
    )
    return _block_created(result)


@router.delete(
    "/blocks/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=IDEMPOTENT_RESPONSES
)
async def delete_block(id: uuid.UUID, session: DbSession, user: ManagerUser) -> Response:
    await _guard(_service(session, user).void_block(id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── /agenda/openings ─────────────────────────────────────────────────────────


@router.get(
    "/openings", response_model=PaginatedResponse[OpeningResponse], responses=ERROR_RESPONSES
)
async def list_openings(
    user: ViewerUser, session: DbSession, page: Paginacion
) -> PaginatedResponse[OpeningResponse]:
    """Aperturas especiales vigentes (las que todavía no terminaron), por inicio."""
    result = await _service(session, user).list_openings(
        now=utcnow(), limit=page.limit, offset=page.offset
    )
    return PaginatedResponse[OpeningResponse](
        items=[OpeningResponse.model_validate(o) for o in result.items],
        total=result.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/openings",
    response_model=OpeningResponse,
    status_code=status.HTTP_201_CREATED,
    responses=VALIDATION_RESPONSES,
)
async def create_opening(
    body: OpeningCreate,
    session: DbSession,
    user: ManagerUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> OpeningResponse:
    await once(session, user, idempotency_key, "agenda.openings.create")
    opening = await _guard(
        _service(session, user).create_opening(
            body.to_range(), resource_id=body.resource_id, reason=body.reason
        )
    )
    return OpeningResponse.model_validate(opening)


@router.delete(
    "/openings/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=IDEMPOTENT_RESPONSES
)
async def delete_opening(id: uuid.UUID, session: DbSession, user: ManagerUser) -> Response:
    await _guard(_service(session, user).void_opening(id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── /agenda/availability ─────────────────────────────────────────────────────


@router.get("/availability", response_model=AvailabilityResponse, responses=VALIDATION_RESPONSES)
async def get_availability(
    user: ViewerUser,
    session: DbSession,
    date: dt.date,
    vehicle_size_id: uuid.UUID,
    service_ids: Annotated[list[uuid.UUID], Query(min_length=1, max_length=20)],
) -> AvailabilityResponse:
    result = await _guard(
        _service(session, user).availability(
            day=date, vehicle_size_id=vehicle_size_id, service_ids=service_ids, now=utcnow()
        )
    )
    duration = dt.timedelta(minutes=result.duration_min)
    return AvailabilityResponse(
        date=date,
        timezone=result.timezone,
        duration_min=result.duration_min,
        slots=[
            AvailableStart(
                start_at=s.start_at, end_at=s.start_at + duration, resource_ids=list(s.resource_ids)
            )
            for s in result.starts
        ],
    )
