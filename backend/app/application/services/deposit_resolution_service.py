"""Resolución de la seña de un turno cancelado — FASE-3-CONTRATO §2.4 (D-005) y adenda A2.

**[diseñar]** el cierre (C-02: cuatro caminos abrían un caso y ninguno lo cerraba). Las
transiciones salen de `app.domain.deposit.DEPOSIT_RESOLUTIONS`:

- `RETENIDA` **solo desde `EN_REVISION`** (el cliente canceló después de la hora del turno;
  A2). Retener una cancelación a tiempo o tardía sería la regla nueva que C-14 deja abierta.
- `DEVUELTA` y `REPROGRAMADA` desde cualquier caso abierto. `DEVUELTA` exige el pago
  `DEVOLUCION`, que este servicio registra (sale de caja) y enlaza.
- `SIN_PAGO` no se resuelve; un cierre no se reabre.

Cerrar exige actor y motivo (CHECK en la base). Idempotente por la clave del pago de la
devolución: un reenvío devuelve la cancelación ya resuelta. Sin devolución (`RETENIDA`,
`REPROGRAMADA`), reenviar el mismo cierre sobre un caso ya cerrado con ese estado devuelve el
caso sin efectos (la cola offline reintenta, F9); un cierre distinto sigue fallando.
"""

import uuid
from datetime import datetime

from app.application.services._base import ServiceBase, require_instant, require_key
from app.application.services._payments import PaymentInput, create_payment
from app.application.services.errors import IdempotencyKeyReusedError
from app.domain.deposit import (
    DEPOSIT_CLOSED_STATUSES,
    check_resolution_requirements,
    resolve_deposit,
)
from app.domain.enums import DepositStatus, PaymentKind
from app.domain.exceptions import GuardFailedError, InvalidAmountError
from app.persistence.models.cancellation import Cancellation
from app.persistence.repositories.cancellations import CancellationRepository
from app.persistence.repositories.catalog import PaymentMethodRepository
from app.persistence.repositories.money import PaymentRepository


class DepositResolutionService(ServiceBase):
    async def resolve(
        self,
        cancellation_id: uuid.UUID,
        *,
        status: DepositStatus,
        reason: str,
        resolved_at: datetime,
        refund: PaymentInput | None = None,
    ) -> Cancellation:
        await self._enter()
        require_instant(resolved_at)
        actor = self._actor()
        cancellation = await self._lock(CancellationRepository(self._session), cancellation_id)
        if refund is not None:
            key = require_key(refund.idempotency_key)
            existing = await PaymentRepository(self._session).get_by_key(key, self._tenant_id)
            if existing is not None:
                if cancellation.refund_payment_id != existing.id:
                    raise IdempotencyKeyReusedError(key)
                return cancellation

        if (
            refund is None
            and status in DEPOSIT_CLOSED_STATUSES
            and cancellation.deposit_status == status
        ):
            return cancellation  # reenvío del mismo cierre: sin efectos (F9)
        target = resolve_deposit(cancellation.deposit_status, status)
        cleaned = check_resolution_requirements(target, reason, refund is not None)
        if refund is not None and target != DepositStatus.DEVUELTA:
            raise GuardFailedError("only a refunded deposit takes a refund payment")

        refund_payment_id: uuid.UUID | None = None
        if refund is not None:
            if refund.amount_cents > cancellation.deposit_paid_cents:
                raise InvalidAmountError("cannot refund more than the deposit paid")
            method = await self._require(
                PaymentMethodRepository(self._session), refund.payment_method_id
            )
            payment = await create_payment(
                self._session,
                self._tenant_id,
                actor,
                method,
                refund,
                kind=PaymentKind.DEVOLUCION,
                booking_id=cancellation.booking_id,
            )
            refund_payment_id = payment.id

        cancellation.deposit_status = target
        cancellation.resolved_at = resolved_at
        cancellation.resolved_by_user_id = actor
        cancellation.resolution_reason = cleaned
        cancellation.refund_payment_id = refund_payment_id
        await self._session.flush()
        return cancellation
