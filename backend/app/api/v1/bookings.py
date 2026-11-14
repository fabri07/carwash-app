"""Bandeja del dueño: turnos — FASE-5-CONTRATO §4.1 (PR 5.3).

- Ver (lista, contador, detalle) con `AGENDA_VER`; aprobar, rechazar, cancelar, no asistió,
  incidencias y reprogramar con `TURNOS_GESTIONAR`; confirmar la seña con `COBROS_REGISTRAR`
  (registra un pago). El `OWNER` pasa siempre. Sin permisos nuevos (Z3).
- Recurso ajeno o inexistente → el mismo 404 (nunca 403).
- Horario tomado (el `EXCLUDE`) → 409 `SLOT_TAKEN`; un bloqueo lo pisa → 409 `BLOCKED`; acción que
  el estado del turno no admite → 409 `CONFLICT`.
- Las acciones aceptan `Idempotency-Key` (un doble toque es `DUPLICATE_IDEMPOTENT`). En
  `confirm-deposit` es **obligatorio** y es la clave del pago: un reenvío devuelve el mismo turno.
"""

import datetime as dt
import uuid
from collections.abc import Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Response, status

from app.api.v1._shared import guard, once
from app.api.v1.deps import DbSession, require_permission
from app.api.v1.errors import ERROR_RESPONSES, IDEMPOTENT_RESPONSES, ApiError, IdempotencyKeyHeader
from app.api.v1.pagination import Paginacion
from app.application.services._payments import PaymentInput
from app.application.services.booking_inbox_service import BookingInboxService
from app.application.services.booking_service import BookingService
from app.application.services.errors import (
    BlockedError,
    IdempotencyKeyReusedError,
    SlotTakenError,
)
from app.domain.enums import BookingStatus
from app.domain.errors import ErrorCode
from app.domain.exceptions import GuardFailedError, InvalidParameterError, InvalidTransition
from app.domain.permissions import Permission
from app.persistence.models.agenda import Booking
from app.persistence.models.user import User
from app.schemas.bookings import (
    BookingCountsResponse,
    BookingDetailResponse,
    BookingSummary,
    CancellationResponse,
    ConfirmDepositBody,
    ConfirmDepositResponse,
    IncidentMarkResponse,
    ReasonBody,
    RescheduleBody,
    RescheduleResponse,
)
from app.schemas.common import PaginatedResponse
from app.utils.datetime_utils import utcnow

ViewerUser = Annotated[User, Depends(require_permission(Permission.AGENDA_VER))]
ManagerUser = Annotated[User, Depends(require_permission(Permission.TURNOS_GESTIONAR))]
CashierUser = Annotated[User, Depends(require_permission(Permission.COBROS_REGISTRAR))]

ACTION_RESPONSES: dict[int | str, dict[str, Any]] = {
    **IDEMPOTENT_RESPONSES,
    422: IDEMPOTENT_RESPONSES[404],
}
#: La clave del pago de la seña: obligatoria (la pone la cola offline o el botón).
PaymentKeyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]

router = APIRouter()
incident_marks = APIRouter()


def _conflict(code: ErrorCode, exc: Exception) -> ApiError:
    return ApiError(status.HTTP_409_CONFLICT, code, str(exc))


async def guard_inbox[T](call: Coroutine[Any, Any, T]) -> T:
    """`guard` de siempre más los errores de la agenda: horario tomado, bloqueo, transición que
    el estado no admite (409) y guardas del dominio (422, con el mensaje de sistema)."""
    try:
        return await guard(call)
    except SlotTakenError as exc:
        raise _conflict(ErrorCode.SLOT_TAKEN, exc) from exc
    except BlockedError as exc:
        raise _conflict(ErrorCode.BLOCKED, exc) from exc
    except (InvalidTransition, IdempotencyKeyReusedError) as exc:
        raise _conflict(ErrorCode.CONFLICT, exc) from exc
    except (GuardFailedError, InvalidParameterError) as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.VALIDATION_ERROR, str(exc)
        ) from exc


def _inbox(session: DbSession, user: User) -> BookingInboxService:
    return BookingInboxService(session, user.tenant_id, user.id)


def _bookings(session: DbSession, user: User) -> BookingService:
    return BookingService(session, user.tenant_id, user.id)


async def _summary(session: DbSession, user: User, booking: Booking) -> BookingSummary:
    (row,) = await _inbox(session, user).rows([booking])
    return BookingSummary.of(row)


# ── Lecturas ─────────────────────────────────────────────────────────────────


@router.get("", response_model=PaginatedResponse[BookingSummary], responses=ACTION_RESPONSES)
async def list_bookings(
    user: ViewerUser,
    session: DbSession,
    page: Paginacion,
    statuses: Annotated[list[BookingStatus] | None, Query(alias="status", max_length=20)] = None,
    from_day: Annotated[dt.date | None, Query(alias="from")] = None,
    to_day: Annotated[dt.date | None, Query(alias="to")] = None,
) -> PaginatedResponse[BookingSummary]:
    """Turnos por estado (repetible) y por día **local** de inicio (`to` inclusive), por inicio."""
    result = await guard_inbox(
        _inbox(session, user).list_bookings(
            statuses=statuses,
            from_day=from_day,
            to_day=to_day,
            limit=page.limit,
            offset=page.offset,
        )
    )
    return PaginatedResponse[BookingSummary](
        items=[BookingSummary.of(r) for r in result.items],
        total=result.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/counts", response_model=BookingCountsResponse, responses=ERROR_RESPONSES)
async def get_counts(user: ViewerUser, session: DbSession) -> BookingCountsResponse:
    """Pendientes de seña + lista de espera + señas a resolver: el número del menú."""
    return BookingCountsResponse.of(await _inbox(session, user).counts(now=utcnow()))


@router.get("/{id}", response_model=BookingDetailResponse, responses=ERROR_RESPONSES)
async def get_booking(id: uuid.UUID, user: ViewerUser, session: DbSession) -> BookingDetailResponse:
    detail = await guard_inbox(_inbox(session, user).detail(id, now=utcnow()))
    return BookingDetailResponse.of_detail(detail)


# ── Seña ─────────────────────────────────────────────────────────────────────


@router.post(
    "/{id}/confirm-deposit", response_model=ConfirmDepositResponse, responses=ACTION_RESPONSES
)
async def confirm_deposit(
    id: uuid.UUID,
    body: ConfirmDepositBody,
    session: DbSession,
    user: CashierUser,
    idempotency_key: PaymentKeyHeader,
) -> ConfirmDepositResponse:
    """Registra la seña: confirma el turno, o lo confirma tarde si el hold venció y el horario
    sigue libre (`late: true`; si no, 409 `SLOT_TAKEN`)."""
    service = _bookings(session, user)
    amount = body.amount_cents
    if amount is None:
        amount = (await guard_inbox(service.get(id))).deposit_required_cents
    now = utcnow()
    result = await guard_inbox(
        service.confirm_deposit_or_late(
            id,
            PaymentInput(
                amount_cents=amount,
                payment_method_id=body.payment_method_id,
                idempotency_key=idempotency_key,
                occurred_at=now,
                reference=body.reference,
            ),
            now=now,
        )
    )
    return ConfirmDepositResponse(
        booking=await _summary(session, user, result.booking), late=result.late
    )


# ── Lista de espera ──────────────────────────────────────────────────────────


@router.post("/{id}/approve", response_model=BookingSummary, responses=ACTION_RESPONSES)
async def approve(
    id: uuid.UUID,
    session: DbSession,
    user: ManagerUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> BookingSummary:
    """Aprueba un pedido de la lista de espera en la misma fila (D5-9): pasa a pendiente de seña
    (hold nuevo) o a confirmado, según la seña vigente. 409 `SLOT_TAKEN` si el horario ya no
    está."""
    await once(session, user, idempotency_key, "bookings.approve")
    booking = await guard_inbox(_bookings(session, user).approve_review(id, now=utcnow()))
    return await _summary(session, user, booking)


@router.post("/{id}/reject", response_model=CancellationResponse, responses=ACTION_RESPONSES)
async def reject(
    id: uuid.UUID,
    body: ReasonBody,
    session: DbSession,
    user: ManagerUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> CancellationResponse:
    """Rechaza un pedido de la lista de espera con motivo. Repetir devuelve el mismo rechazo."""
    await once(session, user, idempotency_key, "bookings.reject")
    cancellation = await guard_inbox(
        _bookings(session, user).reject_review(id, reason=body.reason, now=utcnow())
    )
    return CancellationResponse.of(cancellation)


# ── Operación ────────────────────────────────────────────────────────────────


@router.post("/{id}/cancel", response_model=CancellationResponse, responses=ACTION_RESPONSES)
async def cancel(
    id: uuid.UUID,
    body: ReasonBody,
    session: DbSession,
    user: ManagerUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> CancellationResponse:
    """Cancelación operativa (la decide el negocio, no es incidencia del cliente). Con seña
    cobrada abre el caso en "Señas a resolver". Repetir devuelve la misma cancelación."""
    await once(session, user, idempotency_key, "bookings.cancel")
    cancellation = await guard_inbox(
        _bookings(session, user).cancel_operational(id, requested_at=utcnow(), reason=body.reason)
    )
    return CancellationResponse.of(cancellation)


@router.post("/{id}/no-show", response_model=BookingSummary, responses=ACTION_RESPONSES)
async def no_show(
    id: uuid.UUID,
    session: DbSession,
    user: ManagerUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> BookingSummary:
    """`CONFIRMADO → NO_ASISTIO`. Cuenta como incidencia del cliente (D-014)."""
    await once(session, user, idempotency_key, "bookings.no_show")
    booking = await guard_inbox(_bookings(session, user).mark_no_show(id))
    return await _summary(session, user, booking)


@router.post("/{id}/reschedule", response_model=RescheduleResponse, responses=ACTION_RESPONSES)
async def reschedule(
    id: uuid.UUID,
    body: RescheduleBody,
    session: DbSession,
    user: ManagerUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> RescheduleResponse:
    """Otro horario y/o puesto (D5-10). Fuera del horario o de las reglas se guarda igual con
    `outside_rules: true`; un bloqueo no (409 `BLOCKED`), ni un puesto ocupado (409
    `SLOT_TAKEN`)."""
    await once(session, user, idempotency_key, "bookings.reschedule")
    result = await guard_inbox(
        _bookings(session, user).reschedule(
            id, now=utcnow(), start_at=body.start_at, resource_id=body.resource_id
        )
    )
    return RescheduleResponse(
        booking=await _summary(session, user, result.booking), outside_rules=result.outside_rules
    )


# ── Incidencias (D-014) ──────────────────────────────────────────────────────


@router.post(
    "/{id}/incident-marks",
    response_model=IncidentMarkResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ACTION_RESPONSES,
)
async def mark_incident(
    id: uuid.UUID,
    body: ReasonBody,
    session: DbSession,
    user: ManagerUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> IncidentMarkResponse:
    """Marca una incidencia del cliente del turno: cuenta para la lista de espera."""
    await once(session, user, idempotency_key, "bookings.incident_marks.create")
    mark = await guard_inbox(_inbox(session, user).mark_incident(id, reason=body.reason))
    return IncidentMarkResponse.of(mark)


@incident_marks.delete(
    "/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=IDEMPOTENT_RESPONSES
)
async def void_incident_mark(id: uuid.UUID, session: DbSession, user: ManagerUser) -> Response:
    """Desmarcar = anular (nunca se borra)."""
    await guard_inbox(_inbox(session, user).void_incident_mark(id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
