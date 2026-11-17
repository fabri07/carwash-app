"""Operación del job, turnos a mano y carga retrospectiva — FASE-6-CONTRATO §3.2 y §3.3 (PR 6.1).

- Ver (`GET /jobs/{id}`, línea de tiempo) con `AGENDA_VER`; llegó, iniciar, finalizar, retirar,
  cancelar por demora y la recepción opcional con `JOBS_OPERAR`; cobrar con `COBROS_REGISTRAR` y
  anular un cobro con `COBROS_ANULAR`; el turno "solo a mano" con `TURNOS_GESTIONAR`; la carga
  retrospectiva con `JOBS_OPERAR` (y `COBROS_REGISTRAR` si trae el cobro). El `OWNER` pasa siempre.
- **Toda acción lleva `Idempotency-Key` (obligatoria) y `occurred_at` del cliente** (el momento del
  toque; la cola offline lo manda tal cual). Un reenvío con la misma clave devuelve el mismo
  resultado (200, sin efectos); con otra clave sobre un estado ya superado, 409 `INVALID_TRANSITION`
  y el estado no cambia.
- Recurso ajeno o inexistente → el mismo 404 (nunca 403). Una guarda del dominio (saldo, cliente
  nuevo con deuda, demora que no supera la tolerancia, …) → 422 `VALIDATION_ERROR` con el mensaje
  de sistema; el horario tomado de un turno a mano → 409 `SLOT_TAKEN`.
- **Cancelar por demora es siempre un botón manual** (D6-2): el servidor valida con su reloj que
  la demora supere `tolerance_min` (ni un `occurred_at` del futuro lo adelanta); nunca cancela solo.
"""

import uuid
from collections.abc import Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, status

from app.api.v1._shared import guard, once
from app.api.v1.deps import DbSession, require_permission
from app.api.v1.errors import IDEMPOTENT_RESPONSES, ApiError, forbidden
from app.api.v1.pagination import Paginacion
from app.application.services._payments import PaymentInput
from app.application.services.booking_inbox_service import BookingInboxService
from app.application.services.errors import (
    BlockedError,
    IdempotencyKeyReusedError,
    SlotTakenError,
)
from app.application.services.job_service import JobService, RetroPayment
from app.application.services.operation_service import (
    ManualDeposit,
    OperationService,
)
from app.application.services.team_service import effective_permissions
from app.domain.enums import Channel
from app.domain.errors import ErrorCode
from app.domain.exceptions import (
    GuardFailedError,
    InvalidDatetimeError,
    InvalidParameterError,
    InvalidPlateError,
    InvalidTransition,
)
from app.domain.permissions import Permission
from app.persistence.models.user import User
from app.schemas.bookings import BookingSummary
from app.schemas.common import PaginatedResponse
from app.schemas.operation import (
    CancelDelayBody,
    InspectionBody,
    JobEventResponse,
    JobResponse,
    ManualBookingBody,
    ManualBookingResponse,
    OccurredBody,
    PaymentBody,
    ReceiveBody,
    RetrospectiveBody,
    VoidPaymentBody,
)
from app.utils.datetime_utils import utcnow

ViewerUser = Annotated[User, Depends(require_permission(Permission.AGENDA_VER))]
OperatorUser = Annotated[User, Depends(require_permission(Permission.JOBS_OPERAR))]
CashierUser = Annotated[User, Depends(require_permission(Permission.COBROS_REGISTRAR))]
VoiderUser = Annotated[User, Depends(require_permission(Permission.COBROS_ANULAR))]
ManagerUser = Annotated[User, Depends(require_permission(Permission.TURNOS_GESTIONAR))]

#: Obligatoria en toda acción: en la cola offline el id del ítem **es** la clave.
RequiredKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]

ACTION_RESPONSES: dict[int | str, dict[str, Any]] = {
    **IDEMPOTENT_RESPONSES,
    422: IDEMPOTENT_RESPONSES[404],
}

router = APIRouter()
#: `POST /bookings/manual`: vive con las acciones de la fase pero cuelga de `/bookings`.
manual_bookings = APIRouter()


def _conflict(code: ErrorCode, exc: Exception) -> ApiError:
    return ApiError(status.HTTP_409_CONFLICT, code, str(exc))


async def guard_operation[T](call: Coroutine[Any, Any, T]) -> T:
    """`guard` de siempre más lo de la operación: transición que el estado no admite (409
    `INVALID_TRANSITION`), clave usada por otra operación (409 `CONFLICT`), horario tomado o
    bloqueado (409) y guardas del dominio (422, con el mensaje de sistema)."""
    try:
        return await guard(call)
    except InvalidTransition as exc:
        raise _conflict(ErrorCode.INVALID_TRANSITION, exc) from exc
    except IdempotencyKeyReusedError as exc:
        raise _conflict(ErrorCode.CONFLICT, exc) from exc
    except SlotTakenError as exc:
        raise _conflict(ErrorCode.SLOT_TAKEN, exc) from exc
    except BlockedError as exc:
        raise _conflict(ErrorCode.BLOCKED, exc) from exc
    except (
        GuardFailedError,
        InvalidParameterError,
        InvalidPlateError,
        InvalidDatetimeError,
    ) as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.VALIDATION_ERROR, str(exc)
        ) from exc


def _jobs(session: DbSession, user: User) -> JobService:
    return JobService(session, user.tenant_id, user.id)


def _operation(session: DbSession, user: User) -> OperationService:
    return OperationService(session, user.tenant_id, user.id)


async def _response(session: DbSession, user: User, job_id: uuid.UUID) -> JobResponse:
    return JobResponse.of(await guard_operation(_operation(session, user).job_detail(job_id)))


# ── Lecturas ─────────────────────────────────────────────────────────────────


@router.get("/{id}", response_model=JobResponse, responses=ACTION_RESPONSES)
async def get_job(id: uuid.UUID, user: ViewerUser, session: DbSession) -> JobResponse:
    """El job con sus servicios, pagos y saldo derivado (`> 0` es "debe")."""
    return await _response(session, user, id)


@router.get(
    "/{id}/events", response_model=PaginatedResponse[JobEventResponse], responses=ACTION_RESPONSES
)
async def list_events(
    id: uuid.UUID, user: ViewerUser, session: DbSession, page: Paginacion
) -> PaginatedResponse[JobEventResponse]:
    """La línea de tiempo (`job_events`) en el orden en que el servidor los aceptó. Cada evento
    trae la hora del toque (`occurred_at`) y la de llegada (`created_at`); una carga retrospectiva
    lleva `metadata.manual_entry`."""
    events = await guard_operation(_jobs(session, user).history(id))
    window = events[page.offset : page.offset + page.limit]
    return PaginatedResponse[JobEventResponse](
        items=[JobEventResponse.of(e) for e in window],
        total=len(events),
        limit=page.limit,
        offset=page.offset,
    )


# ── Recibir y transiciones ───────────────────────────────────────────────────


@router.post(
    "", response_model=JobResponse, status_code=status.HTTP_201_CREATED, responses=ACTION_RESPONSES
)
async def receive_job(
    body: ReceiveBody, session: DbSession, user: OperatorUser, idempotency_key: RequiredKey
) -> JobResponse:
    """Llegó (con `booking_id`) o ingreso sin turno. El turno pasa a `RECIBIDO`; el job nace
    `PRESENTE`. Recibir es idempotente por turno y por clave."""
    job = await guard_operation(
        _operation(session, user).receive_job(
            idempotency_key=idempotency_key,
            arrived_at=body.occurred_at,
            booking_id=body.booking_id,
            vehicle_id=body.vehicle_id,
            plate=body.plate,
            vehicle_size_id=body.vehicle_size_id,
            service_ids=body.service_ids,
            customer_id=body.customer_id,
            customer_name=body.customer_name,
            customer_phone=body.customer_phone,
            channel=body.channel,
            resource_id=body.resource_id,
            quote_id=body.quote_id,
            notes=body.notes,
        )
    )
    return await _response(session, user, job.id)


@router.post("/{id}/start", response_model=JobResponse, responses=ACTION_RESPONSES)
async def start_job(
    id: uuid.UUID,
    body: OccurredBody,
    session: DbSession,
    user: OperatorUser,
    idempotency_key: RequiredKey,
) -> JobResponse:
    """`PRESENTE → EN_PROCESO`. No exige la recepción completa (D-007)."""
    await guard_operation(
        _jobs(session, user).start(
            id, idempotency_key=idempotency_key, occurred_at=body.occurred_at
        )
    )
    return await _response(session, user, id)


@router.post("/{id}/finish", response_model=JobResponse, responses=ACTION_RESPONSES)
async def finish_job(
    id: uuid.UUID,
    body: OccurredBody,
    session: DbSession,
    user: OperatorUser,
    idempotency_key: RequiredKey,
) -> JobResponse:
    """`EN_PROCESO → FINALIZADO`. Con el saldo en 0 (una seña que cubre todo) pasa solo a
    `COBRADO`."""
    await guard_operation(
        _jobs(session, user).finish(
            id, idempotency_key=idempotency_key, occurred_at=body.occurred_at
        )
    )
    return await _response(session, user, id)


@router.post("/{id}/pick-up", response_model=JobResponse, responses=ACTION_RESPONSES)
async def pick_up_job(
    id: uuid.UUID,
    body: OccurredBody,
    session: DbSession,
    user: OperatorUser,
    idempotency_key: RequiredKey,
) -> JobResponse:
    """`COBRADO | FINALIZADO → RETIRADO`. Con deuda, solo si el cliente ya pagó un servicio."""
    await guard_operation(
        _jobs(session, user).pick_up(
            id, idempotency_key=idempotency_key, occurred_at=body.occurred_at
        )
    )
    return await _response(session, user, id)


@router.post("/{id}/cancel-delay", response_model=JobResponse, responses=ACTION_RESPONSES)
async def cancel_delay(
    id: uuid.UUID,
    body: CancelDelayBody,
    session: DbSession,
    user: OperatorUser,
    idempotency_key: RequiredKey,
) -> JobResponse:
    """`PRESENTE → CANCELADO_DEMORA`: **botón manual** (D6-2), nunca automático. Solo si la demora
    supera `tolerance_min`; el servidor mira su propio reloj: un `occurred_at` del futuro no lo
    adelanta (se toma el menor entre el del toque y el del servidor). Retiene la seña (F5)."""
    occurred_at = min(body.occurred_at, utcnow())
    await guard_operation(
        _jobs(session, user).cancel_for_delay(
            id, idempotency_key=idempotency_key, occurred_at=occurred_at, reason=body.reason
        )
    )
    return await _response(session, user, id)


# ── Cobros ───────────────────────────────────────────────────────────────────


@router.post("/{id}/payments", response_model=JobResponse, responses=ACTION_RESPONSES)
async def record_payment(
    id: uuid.UUID,
    body: PaymentBody,
    session: DbSession,
    user: CashierUser,
    idempotency_key: RequiredKey,
) -> JobResponse:
    """Un cobro más (suma, no pisa). Sin `amount_cents` cobra el saldo completo: cobrar son 2
    toques (abrir, confirmar). Con el saldo en 0 el job `FINALIZADO` pasa solo a `COBRADO`. Un
    importe mayor al saldo es 422."""
    amount = body.amount_cents
    if amount is None:
        amount = await guard_operation(_jobs(session, user).balance(id))
    await guard_operation(
        _jobs(session, user).record_payment(
            id,
            PaymentInput(
                amount_cents=amount,
                payment_method_id=body.payment_method_id,
                idempotency_key=idempotency_key,
                occurred_at=body.occurred_at,
                reference=body.reference,
            ),
        )
    )
    return await _response(session, user, id)


@router.post(
    "/{id}/payments/{payment_id}/void", response_model=JobResponse, responses=ACTION_RESPONSES
)
async def void_payment(
    id: uuid.UUID,
    payment_id: uuid.UUID,
    body: VoidPaymentBody,
    session: DbSession,
    user: VoiderUser,
    idempotency_key: RequiredKey,
) -> JobResponse:
    """Anula un cobro del job, con motivo. El job no se reabre: si queda saldo, es deuda."""
    await guard_operation(
        _jobs(session, user).void_payment(
            payment_id,
            idempotency_key=idempotency_key,
            occurred_at=body.occurred_at,
            reason=body.reason,
            job_id=id,
        )
    )
    return await _response(session, user, id)


# ── Recepción opcional ───────────────────────────────────────────────────────


@router.post("/{id}/inspection", response_model=JobResponse, responses=ACTION_RESPONSES)
async def record_inspection(
    id: uuid.UUID,
    body: InspectionBody,
    session: DbSession,
    user: OperatorUser,
    idempotency_key: RequiredKey,
) -> JobResponse:
    """La recepción completa (suciedad, daños, objetos, consentimiento de fotos): camino
    secundario y opcional (D-007). 0..1 por job: la segunda actualiza la primera."""
    await guard_operation(
        _jobs(session, user).record_inspection(
            id,
            idempotency_key=idempotency_key,
            occurred_at=body.occurred_at,
            dirt_level=body.dirt_level,
            pre_existing_damage=body.pre_existing_damage,
            valuables=body.valuables,
            photo_consent=body.photo_consent,
            checklist=body.checklist,
        )
    )
    return await _response(session, user, id)


# ── Carga retrospectiva ──────────────────────────────────────────────────────


@router.post(
    "/retrospective",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ACTION_RESPONSES,
)
async def retrospective(
    body: RetrospectiveBody, session: DbSession, user: OperatorUser, idempotency_key: RequiredKey
) -> JobResponse:
    """Un trabajo ya hecho, de una sola vez (D6-3): crea el job y sus eventos con la hora real
    (`manual_entry`), `entry_mode = RETROSPECTIVA`, y cae en la agenda de **su** fecha. Con
    `payment` lo cobra (medio y monto; sin monto, el total); sin él queda `FINALIZADO` y "debe".
    Cargar el cobro exige además `COBROS_REGISTRAR`."""
    if body.payment is not None and Permission.COBROS_REGISTRAR not in await effective_permissions(
        session, user
    ):
        raise forbidden("Your permission profile does not allow this action.")
    payment = (
        RetroPayment(
            payment_method_id=body.payment.payment_method_id,
            amount_cents=body.payment.amount_cents,
            reference=body.payment.reference,
        )
        if body.payment is not None
        else None
    )
    job = await guard_operation(
        _operation(session, user).record_retrospective_job(
            idempotency_key=idempotency_key,
            started_at=body.started_at,
            finished_at=body.finished_at,
            now=utcnow(),
            vehicle_size_id=body.vehicle_size_id,
            service_ids=body.service_ids,
            customer_name=body.customer_name,
            customer_phone=body.customer_phone,
            plate=body.plate,
            channel=body.channel or Channel.CARGA_MANUAL,
            resource_id=body.resource_id,
            notes=body.notes,
            payment=payment,
        )
    )
    return await _response(session, user, job.id)


# ── Turno "solo a mano" ──────────────────────────────────────────────────────


@manual_bookings.post(
    "/manual",
    response_model=ManualBookingResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ACTION_RESPONSES,
)
async def create_manual_booking(
    body: ManualBookingBody, session: DbSession, user: ManagerUser, idempotency_key: RequiredKey
) -> ManualBookingResponse:
    """Un turno cargado por el dueño (fuente `AGENDA_INTERNA`, código `INT-…`). El `EXCLUDE`
    decide el choque (409 `SLOT_TAKEN`). Con `deposit` registra la seña ya cobrada y el turno
    nace `CONFIRMADO`; si el negocio pide seña y no se manda, queda `PENDIENTE_SEÑA` con su hold.
    Un reenvío de la clave es 409 `DUPLICATE_IDEMPOTENT`."""
    await once(session, user, idempotency_key, "bookings.manual")
    deposit = (
        ManualDeposit(
            payment_method_id=body.deposit.payment_method_id,
            amount_cents=body.deposit.amount_cents,
            reference=body.deposit.reference,
        )
        if body.deposit is not None
        else None
    )
    now = utcnow()
    if deposit is not None and Permission.COBROS_REGISTRAR not in await effective_permissions(
        session, user
    ):
        raise forbidden("Your permission profile does not allow this action.")
    result = await guard_operation(
        _operation(session, user).create_manual_booking(
            idempotency_key=idempotency_key,
            now=now,
            start_at=body.start_at,
            resource_id=body.resource_id,
            vehicle_size_id=body.vehicle_size_id,
            service_ids=body.service_ids,
            customer_name=body.customer_name,
            customer_phone=body.customer_phone,
            plate=body.plate,
            channel=body.channel or Channel.CARGA_MANUAL,
            notes=body.notes,
            deposit=deposit,
        )
    )
    (row,) = await BookingInboxService(session, user.tenant_id, user.id).rows([result.booking])
    return ManualBookingResponse.of(result, BookingSummary.of(row))
