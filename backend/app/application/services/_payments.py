"""Alta de un pago con su movimiento de caja — FASE-3-CONTRATO §1.5.

Lo comparten `JobService` (saldo, devolución), `BookingService` (seña) y
`DepositResolutionService` (devolución de la seña). Un solo lugar calcula la comisión
(`apply_bps` con la tasa **snapshot** del medio, **[corregir]** C-5: eran literales `0`) y
decide la dirección del movimiento. La comisión es costo interno: no es un movimiento de caja.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services._base import require_instant, require_key
from app.application.services.errors import IdempotencyKeyReusedError
from app.domain.enums import CashDirection, CashMovementKind, PaymentKind
from app.domain.exceptions import GuardFailedError, InvalidAmountError
from app.domain.money import apply_bps
from app.domain.void import VoidReason
from app.persistence.db._savepoint import (
    SavepointConflictError,
    guarded_savepoint,
    unique_violation_classifier,
)
from app.persistence.models.catalog import PaymentMethod
from app.persistence.models.money import (
    PAYMENTS_IDEMPOTENCY_UNIQUE,
    CashMovement,
    Payment,
)
from app.persistence.repositories.money import CashMovementRepository
from app.utils.datetime_utils import utcnow

_PAYMENT_KEY = unique_violation_classifier(
    "payment_key",
    constraint=PAYMENTS_IDEMPOTENCY_UNIQUE,
    columns=("payments.tenant_id", "payments.idempotency_key"),
)


@dataclass(frozen=True, slots=True)
class PaymentInput:
    """Lo que manda el llamador para registrar un pago. `occurred_at` es del cliente."""

    amount_cents: int
    payment_method_id: uuid.UUID
    idempotency_key: str
    occurred_at: datetime
    reference: str | None = None
    notes: str | None = None


def _cash_for(kind: PaymentKind) -> tuple[CashDirection, CashMovementKind]:
    if kind == PaymentKind.DEVOLUCION:
        return CashDirection.SALIDA, CashMovementKind.DEVOLUCION
    return CashDirection.ENTRADA, CashMovementKind.COBRO


async def create_payment(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    method: PaymentMethod,
    data: PaymentInput,
    *,
    kind: PaymentKind,
    job_id: uuid.UUID | None = None,
    booking_id: uuid.UUID | None = None,
) -> Payment:
    """Inserta el pago y su movimiento de caja, con la misma clave del llamador.

    Los llamadores toman antes el lock de la fila dueña (job, turno o cancelación) y buscan
    la clave: un reenvío legítimo nunca llega acá. Por eso un choque de la clave en este
    punto es **otra** operación con la misma clave → `IdempotencyKeyReusedError`.
    """
    key = require_key(data.idempotency_key)
    require_instant(data.occurred_at)
    if data.amount_cents <= 0:
        raise InvalidAmountError(f"payment amount must be > 0: {data.amount_cents}")
    if kind != PaymentKind.DEVOLUCION and not method.for_income:
        raise GuardFailedError(f"payment method {method.code} is not enabled for income")
    direction, cash_kind = _cash_for(kind)
    payment = Payment(
        tenant_id=tenant_id,
        job_id=job_id,
        booking_id=booking_id,
        kind=kind,
        amount_cents=data.amount_cents,
        payment_method_id=method.id,
        commission_bps=method.commission_bps,
        # [abierto] Una DEVOLUCION también lleva comisión con la tasa del medio. Si la
        # devolución recupera o no la comisión del cobro original es decisión de negocio
        # abierta para F7 (pregunta 12 del contrato): no se cambia acá.
        commission_cents=apply_bps(data.amount_cents, method.commission_bps),
        occurred_at=data.occurred_at,
        actor_user_id=actor_user_id,
        idempotency_key=key,
        reference=data.reference,
        notes=data.notes,
    )
    try:
        async with guarded_savepoint(session, _PAYMENT_KEY):
            session.add(payment)
            await session.flush()
            session.add(
                CashMovement(
                    tenant_id=tenant_id,
                    direction=direction,
                    kind=cash_kind,
                    amount_cents=data.amount_cents,
                    payment_method_id=method.id,
                    payment_id=payment.id,
                    occurred_at=data.occurred_at,
                    actor_user_id=actor_user_id,
                    idempotency_key=key,
                )
            )
    except SavepointConflictError as exc:
        raise IdempotencyKeyReusedError(key) from exc
    return payment


async def void_payment_row(
    session: AsyncSession, tenant_id: uuid.UUID, payment: Payment, reason: VoidReason
) -> None:
    """Anula el pago y su movimiento de caja vivo (ADR-0003: las dos columnas juntas).

    El movimiento se anula en vez de compensarse con uno de signo contrario: el pago
    anulado "nunca debió existir" (`ERROR_DE_CARGA`) y el libro de F7 lo verá así.
    """
    now = utcnow()
    payment.voided_at = now
    payment.void_reason = reason
    movement = await CashMovementRepository(session).find_by_payment(payment.id, tenant_id)
    if movement is not None:
        movement.voided_at = now
        movement.void_reason = reason
    await session.flush()
