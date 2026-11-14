"""Bandeja del dueño: señas de turnos cancelados — FASE-5-CONTRATO §4.1 (PR 5.3).

`DepositResolutionService` (D-005) no tenía endpoint hasta acá.

- `GET /cancellations`: los casos de seña **abiertos** (devolución pendiente, en revisión,
  reprogramar o devolver), el más viejo primero. `AGENDA_VER`.
- `POST /cancellations/{id}/resolve`: `DEVUELTA` (con el pago de la devolución, que sale de caja),
  `RETENIDA` (solo si el cliente canceló después de la hora) o `REPROGRAMADA`, con motivo.
  `COBROS_REGISTRAR`. Con devolución, `Idempotency-Key` es obligatorio (es la clave del pago).
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from app.api.v1.bookings import guard_inbox
from app.api.v1.deps import DbSession, require_permission
from app.api.v1.errors import ERROR_RESPONSES, IDEMPOTENT_RESPONSES, ApiError, IdempotencyKeyHeader
from app.api.v1.pagination import Paginacion
from app.application.services._payments import PaymentInput
from app.application.services.booking_inbox_service import BookingInboxService
from app.application.services.deposit_resolution_service import DepositResolutionService
from app.domain.enums import DepositStatus
from app.domain.errors import ErrorCode
from app.domain.permissions import Permission
from app.persistence.models.user import User
from app.schemas.bookings import CancellationResponse, DepositCaseResponse, ResolveBody
from app.schemas.common import PaginatedResponse
from app.utils.datetime_utils import utcnow

ViewerUser = Annotated[User, Depends(require_permission(Permission.AGENDA_VER))]
CashierUser = Annotated[User, Depends(require_permission(Permission.COBROS_REGISTRAR))]

ACTION_RESPONSES: dict[int | str, dict[str, Any]] = {
    **IDEMPOTENT_RESPONSES,
    422: IDEMPOTENT_RESPONSES[404],
}

router = APIRouter()


@router.get("", response_model=PaginatedResponse[DepositCaseResponse], responses=ERROR_RESPONSES)
async def list_open_cases(
    user: ViewerUser, session: DbSession, page: Paginacion
) -> PaginatedResponse[DepositCaseResponse]:
    result = await BookingInboxService(session, user.tenant_id, user.id).list_deposit_cases(
        limit=page.limit, offset=page.offset
    )
    return PaginatedResponse[DepositCaseResponse](
        items=[DepositCaseResponse.of(c) for c in result.items],
        total=result.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/{id}/resolve", response_model=CancellationResponse, responses=ACTION_RESPONSES)
async def resolve(
    id: uuid.UUID,
    body: ResolveBody,
    session: DbSession,
    user: CashierUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> CancellationResponse:
    """Cierra el caso de la seña. Reenviar el mismo cierre devuelve el caso sin efectos."""
    now = utcnow()
    refund: PaymentInput | None = None
    if body.refund is not None:
        if idempotency_key is None:
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                ErrorCode.VALIDATION_ERROR,
                "a refund needs an Idempotency-Key header",
            )
        refund = PaymentInput(
            amount_cents=body.refund.amount_cents,
            payment_method_id=body.refund.payment_method_id,
            idempotency_key=idempotency_key,
            occurred_at=now,
        )
    cancellation = await guard_inbox(
        DepositResolutionService(session, user.tenant_id, user.id).resolve(
            id,
            status=DepositStatus(body.status),
            reason=body.reason,
            resolved_at=now,
            refund=refund,
        )
    )
    return CancellationResponse.of(cancellation)
