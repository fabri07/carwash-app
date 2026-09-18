"""Repositorios de dinero — FASE-3-CONTRATO §1.5.

El saldo no se guarda (X9): se deriva de estas filas cada vez, con una sola consulta por
job o por turno (sin N+1).
"""

import uuid

from sqlalchemy import select

from app.domain.enums import PaymentKind
from app.persistence.models.money import CashMovement, Payment
from app.persistence.repositories.base import BaseRepository, one_or_none


class PaymentRepository(BaseRepository[Payment]):
    model = Payment

    async def get_by_key(self, idempotency_key: str, tenant_id: uuid.UUID) -> Payment | None:
        """El pago con esa clave, anulado o no: anular no libera la clave (A8)."""
        return await one_or_none(
            self._session,
            select(Payment).where(
                Payment.idempotency_key == idempotency_key, Payment.tenant_id == tenant_id
            ),
        )

    async def list_for_job(self, job_id: uuid.UUID, tenant_id: uuid.UUID) -> list[Payment]:
        """Todos los pagos del job, anulados incluidos (el saldo los descarta por `voided`)."""
        result = await self._session.scalars(
            select(Payment)
            .where(Payment.job_id == job_id, Payment.tenant_id == tenant_id)
            .order_by(Payment.occurred_at, Payment.id)
        )
        return list(result.all())

    async def list_for_booking(self, booking_id: uuid.UUID, tenant_id: uuid.UUID) -> list[Payment]:
        result = await self._session.scalars(
            select(Payment)
            .where(Payment.booking_id == booking_id, Payment.tenant_id == tenant_id)
            .order_by(Payment.occurred_at, Payment.id)
        )
        return list(result.all())

    async def lock_unlinked_for_booking(
        self, booking_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[Payment]:
        """Pagos del turno que todavía no apuntan a un job (las señas antes de recibir)."""
        result = await self._session.scalars(
            select(Payment)
            .where(
                Payment.booking_id == booking_id,
                Payment.job_id.is_(None),
                Payment.tenant_id == tenant_id,
            )
            .order_by(Payment.id)
            .with_for_update()
        )
        return list(result.all())


class CashMovementRepository(BaseRepository[CashMovement]):
    model = CashMovement

    async def find_by_payment(
        self, payment_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> CashMovement | None:
        """El movimiento vivo del pago (a lo sumo uno: único vivo parcial)."""
        return await one_or_none(
            self._session,
            select(CashMovement).where(
                CashMovement.payment_id == payment_id, *self._scope(tenant_id, False)
            ),
        )


def deposits_paid(payments: list[Payment]) -> int:
    """Señas cobradas y vivas del turno o del job, en centavos (snapshot de cancelación)."""
    return sum(
        p.amount_cents for p in payments if p.kind == PaymentKind.SENA and p.voided_at is None
    )
