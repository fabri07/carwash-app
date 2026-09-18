"""Operación — FASE-3-CONTRATO §1.4: el lavado, sus eventos y la inspección.

`job_events` es append-only y es la fuente de verdad de las transiciones; `jobs.status` y los
`*_at` son cachés del último evento. `occurred_at` lo pone el cliente (momento del toque),
`created_at` el server (X10). No hay CHECKs de orden temporal entre columnas: con la cola
offline, dos dispositivos con relojes distintos producen órdenes legítimos "imposibles".
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import (
    CHANNEL_ENUM,
    DIRT_LEVEL_ENUM,
    JOB_EVENT_TYPE_ENUM,
    JOB_STATUS_ENUM,
    Channel,
    DirtLevel,
    JobEventType,
    JobStatus,
)
from app.persistence.db.base import (
    PGJSONB,
    PGTEXTARRAY,
    Base,
    TenantScopedModel,
    UUIDPrimaryKeyMixin,
)
from app.persistence.db.mixins import TenantMixin
from app.persistence.models._constraints import (
    check,
    parent_key,
    pg_check,
    pg_enum,
    tenant_fk,
    tenant_index,
    unique_alive,
    voidable_table_args,
)

#: Único de la cola offline: un reenvío con la misma clave no duplica el evento.
JOB_EVENTS_IDEMPOTENCY_UNIQUE = "uq_job_events_tenant_id_idempotency_key"
#: Recibir es idempotente: un turno tiene a lo sumo un job vivo (**[corregir]** R-O-010).
JOBS_BOOKING_UNIQUE = "ux_jobs_tenant_id_booking_id"


class Job(TenantScopedModel):
    """El lavado. Nace cuando el auto llega; un ingreso sin turno es un job sin booking."""

    __tablename__ = "jobs"
    __table_args__ = voidable_table_args(
        parent_key(),
        tenant_fk("booking_id", "bookings"),
        tenant_fk("quote_id", "quotes"),
        tenant_fk("customer_id", "customers"),
        tenant_fk("vehicle_id", "vehicles"),
        tenant_fk("vehicle_size_id", "vehicle_sizes"),
        tenant_fk("service_id", "services"),
        tenant_fk("resource_id", "resources"),
        tenant_fk("responsible_user_id", "users"),
        tenant_index("jobs", "booking_id"),
        tenant_index("jobs", "quote_id"),
        tenant_index("jobs", "customer_id"),
        tenant_index("jobs", "vehicle_id"),
        tenant_index("jobs", "vehicle_size_id"),
        tenant_index("jobs", "service_id"),
        tenant_index("jobs", "resource_id"),
        tenant_index("jobs", "responsible_user_id"),
        unique_alive("jobs", "booking_id", where="booking_id IS NOT NULL"),
        unique_alive("jobs", "legacy_id", where="legacy_id IS NOT NULL"),
        check("base_price_cents > 0", "precio_base_positivo"),
        check("surcharge_cents >= 0", "recargo_no_negativo"),
        check("discount_cents >= 0", "descuento_no_negativo"),
        check("discount_cents = 0 OR discount_reason IS NOT NULL", "descuento_con_motivo"),
        # R-O-003. El total pactado se DERIVA; esto solo impide que sea <= 0.
        check("base_price_cents + surcharge_cents - discount_cents > 0", "total_positivo"),
        check("deposit_required_cents >= 0", "sena_no_negativa"),
    )

    booking_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    quote_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: [abierto] ¿el alta rápida exige cliente? (§9, pregunta 1).
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: La patente es obligatoria en el alta rápida.
    vehicle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: Nunca texto libre (R-O-005).
    vehicle_size_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    service_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: En qué puesto se lava; la ocupación de walk-ins es F5/F6.
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: Reemplaza el literal hardcodeado (R-O-038).
    responsible_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channel: Mapped[Channel] = mapped_column(pg_enum(Channel, CHANNEL_ENUM), nullable=False)
    #: Caché del último `job_events.to_status` (§2.2).
    status: Mapped[JobStatus] = mapped_column(pg_enum(JobStatus, JOB_STATUS_ENUM), nullable=False)
    service_name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    #: De `service_prices` o de la cotización aceptada (D-006, C-17).
    base_price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Separados (R-O-032).
    surcharge_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    discount_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    discount_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Snapshot del turno.
    deposit_required_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    #: Snapshot de `booking.start_at`; NULL en walk-in.
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: La pone el cliente (**[corregir]** R-O-006: el server inflaba la demora).
    arrived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Con signo (llegar antes también se registra); NULL sin turno.
    arrival_delay_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Cachés de los eventos. NULL = no pasó (nunca `0`).
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    picked_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    legacy_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Señales de revisión de la migración (D-008).
    legacy_review_flags: Mapped[list[str] | None] = mapped_column(PGTEXTARRAY, nullable=True)


class JobEvent(Base, UUIDPrimaryKeyMixin, TenantMixin):
    """Append-only (X8): sin `updated_at` ni anulación.

    En la base: `carwash_app` solo tiene `SELECT, INSERT`, y el trigger
    `job_events_append_only` rechaza `UPDATE`/`DELETE` aun al dueño. Un `GRANT` se puede
    ampliar por error; el trigger no se saltea sin una migración visible.
    """

    __tablename__ = "job_events"
    __table_args__ = (
        tenant_fk("job_id", "jobs"),
        tenant_fk("actor_user_id", "users"),
        # Cubre la FK del job y es el orden de lectura de la historia.
        tenant_index("job_events", "job_id", "occurred_at"),
        tenant_index("job_events", "actor_user_id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
        check(
            "(from_status IS NULL AND to_status IS NULL) OR to_status IS NOT NULL",
            "estados_coherentes",
        ),
        # D-001.4. `coalesce`: sin él, un `reason` ausente da NULL y el CHECK lo dejaría pasar.
        pg_check(
            f"event_type <> '{JobEventType.DEPOSIT_RETENTION_REVERSED.value}' "
            "OR length(btrim(coalesce(metadata->>'reason', ''))) > 0",
            "reversion_con_motivo",
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[JobEventType] = mapped_column(
        pg_enum(JobEventType, JOB_EVENT_TYPE_ENUM), nullable=False
    )
    from_status: Mapped[JobStatus | None] = mapped_column(
        pg_enum(JobStatus, JOB_STATUS_ENUM), nullable=True
    )
    to_status: Mapped[JobStatus | None] = mapped_column(
        pg_enum(JobStatus, JOB_STATUS_ENUM), nullable=True
    )
    #: Del cliente.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Del server.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    #: Siempre identificado (D-001.4).
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    #: Columna `metadata` (el atributo no puede llamarse así: lo reserva Declarative).
    #: `reason`, `delay_min`, `tolerance_min`, `amount_cents`, `payment_id`, `source`.
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", PGJSONB, nullable=False, default=dict, server_default=text("'{}'")
    )


class JobInspection(TenantScopedModel):
    """Opcional, 0..1 por job, todo nullable (D-007). *No reconstruir `CONTROL_INICIO`.*"""

    __tablename__ = "job_inspections"
    __table_args__ = voidable_table_args(
        tenant_fk("job_id", "jobs"),
        tenant_fk("inspected_by_user_id", "users"),
        tenant_index("job_inspections", "job_id"),
        tenant_index("job_inspections", "inspected_by_user_id"),
        unique_alive("job_inspections", "job_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: Los 6 niveles del legacy (R-O-001 B15).
    dirt_level: Mapped[DirtLevel | None] = mapped_column(
        pg_enum(DirtLevel, DIRT_LEVEL_ENUM), nullable=True
    )
    pre_existing_damage: Mapped[str | None] = mapped_column(Text, nullable=True)
    valuables: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_consent: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: Ítem → bool.
    checklist: Mapped[dict[str, Any] | None] = mapped_column(PGJSONB, nullable=True)
    inspected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    inspected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
