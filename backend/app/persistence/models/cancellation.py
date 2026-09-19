"""Cancelación de un turno y resolución de la seña — FASE-3-CONTRATO §1.4 y §2.4 (D-005).

**[diseñar]** el cierre: en el legacy cuatro caminos abren un caso y ninguno lo cierra (C-02).
Una cancelación por turno (`UNIQUE (tenant_id, booking_id)`): una segunda solicitud devuelve
la existente (R-T-035). `CANCELADO_DEMORA` NO crea fila acá: su retención se audita en
`job_events` (D-001.5).
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import (
    BOOKING_STATUS_ENUM,
    CANCELLATION_CLASSIFICATION_ENUM,
    CANCELLATION_INITIATOR_ENUM,
    DEPOSIT_STATUS_ENUM,
    BookingStatus,
    CancellationClassification,
    CancellationInitiator,
    DepositStatus,
)
from app.persistence.db.base import TenantScopedModel
from app.persistence.models._constraints import (
    check,
    pg_check,
    pg_enum,
    tenant_fk,
    tenant_index,
    voidable_table_args,
)

#: Una cancelación por turno.
CANCELLATIONS_BOOKING_UNIQUE = "uq_cancellations_tenant_id_booking_id"
DEFAULT_REASON = "Prefiere no informarlo"

_CLOSED_SQL = ", ".join(
    f"'{s.value}'"
    for s in (DepositStatus.DEVUELTA, DepositStatus.RETENIDA, DepositStatus.REPROGRAMADA)
)


class Cancellation(TenantScopedModel):
    __tablename__ = "cancellations"
    __table_args__ = voidable_table_args(
        tenant_fk("booking_id", "bookings"),
        tenant_fk("resolved_by_user_id", "users"),
        tenant_fk("refund_payment_id", "payments"),
        tenant_fk("actor_user_id", "users"),
        # Cubre también la FK del turno.
        UniqueConstraint("tenant_id", "booking_id"),
        tenant_index("cancellations", "resolved_by_user_id"),
        tenant_index("cancellations", "refund_payment_id"),
        tenant_index("cancellations", "actor_user_id"),
        check("length(reason) <= 160", "motivo_largo"),
        check("deposit_paid_cents >= 0", "sena_no_negativa"),
        # Cerrar el caso exige actor y motivo. Partido en dos: el del motivo usa `btrim` y
        # es solo de Postgres; el del actor corre también en la suite rápida.
        check(
            f"deposit_status NOT IN ({_CLOSED_SQL}) "
            "OR (resolved_at IS NOT NULL AND resolved_by_user_id IS NOT NULL)",
            "cierre_con_actor",
        ),
        pg_check(
            f"deposit_status NOT IN ({_CLOSED_SQL}) "
            "OR length(btrim(coalesce(resolution_reason, ''))) > 0",
            "cierre_con_motivo",
        ),
        check(
            f"deposit_status <> '{DepositStatus.DEVUELTA.value}' OR refund_payment_id IS NOT NULL",
            "devuelta_con_pago",
        ),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    initiator: Mapped[CancellationInitiator] = mapped_column(
        pg_enum(CancellationInitiator, CANCELLATION_INITIATOR_ENUM), nullable=False
    )
    #: Valores exactos del código (R-T-037, C-06).
    classification: Mapped[CancellationClassification] = mapped_column(
        pg_enum(CancellationClassification, CANCELLATION_CLASSIFICATION_ENUM), nullable=False
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: `floor((start − requested)/60)`; puede ser negativo.
    anticipation_min: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(
        Text, nullable=False, default=DEFAULT_REASON, server_default=DEFAULT_REASON
    )
    previous_status: Mapped[BookingStatus] = mapped_column(
        pg_enum(BookingStatus, BOOKING_STATUS_ENUM), nullable=False
    )
    resulting_status: Mapped[BookingStatus] = mapped_column(
        pg_enum(BookingStatus, BOOKING_STATUS_ENUM), nullable=False
    )
    #: Snapshot al cancelar.
    deposit_paid_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deposit_status: Mapped[DepositStatus] = mapped_column(
        pg_enum(DepositStatus, DEPOSIT_STATUS_ENUM), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    refund_payment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: NULL cuando cancela el cliente desde la web.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: La DEL TURNO, no la vigente (R-T-057).
    terms_version: Mapped[str | None] = mapped_column(Text, nullable=True)
