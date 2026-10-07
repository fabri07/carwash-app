"""Dinero — FASE-3-CONTRATO §1.5. Todo en centavos `BIGINT` y puntos básicos (X3).

**[corregir]** R-O-026: un segundo cobro pisaba al primero. Acá los cobros son 1:N y el saldo,
el estado de pago y la seña pagada se DERIVAN de `payments` (X9): no hay columna que se
desincronice.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import (
    CASH_DIRECTION_ENUM,
    CASH_MOVEMENT_KIND_ENUM,
    PAYMENT_KIND_ENUM,
    CashDirection,
    CashMovementKind,
    PaymentKind,
)
from app.persistence.db.base import PGTEXTARRAY, TenantScopedModel
from app.persistence.models._constraints import (
    check,
    parent_key,
    pg_enum,
    tenant_fk,
    tenant_index,
    unique_alive,
    voidable_table_args,
)

#: Un cobro nunca se duplica: reenviar con la misma clave choca acá.
PAYMENTS_IDEMPOTENCY_UNIQUE = "uq_payments_tenant_id_idempotency_key"
CASH_MOVEMENTS_IDEMPOTENCY_UNIQUE = "uq_cash_movements_tenant_id_idempotency_key"


class Payment(TenantScopedModel):
    """Un cobro, seña o devolución. `amount_cents` siempre > 0: el signo lo da `kind`.

    La seña se cobra antes de que exista el job (R-O-030); al recibir el auto, el servicio
    vincula las señas del turno al job. Anular un cobro = `VoidableMixin` más el evento
    `PAYMENT_VOIDED` con motivo cuando el pago es de un job.
    """

    __tablename__ = "payments"
    __table_args__ = voidable_table_args(
        parent_key(),
        tenant_fk("job_id", "jobs"),
        tenant_fk("booking_id", "bookings"),
        tenant_fk("payment_method_id", "payment_methods"),
        tenant_fk("actor_user_id", "users"),
        tenant_index("payments", "job_id"),
        tenant_index("payments", "booking_id"),
        tenant_index("payments", "payment_method_id"),
        tenant_index("payments", "actor_user_id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
        check("job_id IS NOT NULL OR booking_id IS NOT NULL", "con_job_o_turno"),
        check("amount_cents > 0", "importe_positivo"),
        check("commission_bps BETWEEN 0 AND 10000", "comision_bps_rango"),
        check("commission_cents >= 0", "comision_no_negativa"),
    )

    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: `DEVOLUCION` resta.
    kind: Mapped[PaymentKind] = mapped_column(
        pg_enum(PaymentKind, PAYMENT_KIND_ENUM), nullable=False
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Nunca "A definir" (R-O-028).
    payment_method_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: Snapshot de la tasa del medio.
    commission_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    #: SIEMPRE calculado; costo interno, no toca el precio al cliente (**[corregir]** C-5).
    commission_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Del cliente.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    legacy_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: D-008.
    legacy_review_flags: Mapped[list[str] | None] = mapped_column(PGTEXTARRAY, nullable=True)


class CashMovement(TenantScopedModel):
    """Libro de caja mínimo. Apertura, cierre y arqueo son F7 ([abierto]).

    La comisión NO es un movimiento de caja. F7B agrega `kind`s por migración.
    """

    __tablename__ = "cash_movements"
    __table_args__ = voidable_table_args(
        tenant_fk("payment_method_id", "payment_methods"),
        tenant_fk("payment_id", "payments"),
        tenant_fk("actor_user_id", "users"),
        tenant_index("cash_movements", "payment_method_id"),
        tenant_index("cash_movements", "payment_id"),
        tenant_index("cash_movements", "actor_user_id"),
        # Un movimiento vivo por pago.
        unique_alive("cash_movements", "payment_id", where="payment_id IS NOT NULL"),
        UniqueConstraint("tenant_id", "idempotency_key"),
        check("amount_cents > 0", "importe_positivo"),
    )

    direction: Mapped[CashDirection] = mapped_column(
        pg_enum(CashDirection, CASH_DIRECTION_ENUM), nullable=False
    )
    kind: Mapped[CashMovementKind] = mapped_column(
        pg_enum(CashMovementKind, CASH_MOVEMENT_KIND_ENUM), nullable=False
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payment_method_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    payment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
