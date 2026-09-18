"""Agenda — FASE-3-CONTRATO §1.3: turnos y bloqueos.

**La garantía de no solapamiento vive en la base**: `xc_bookings_sin_solapamiento` es un
`EXCLUDE USING gist` sobre `(tenant_id, resource_id, [start_at, end_at))` para los turnos que
bloquean el puesto. Los servicios duran 60/90/120/300 min y hay varios puestos: un
`UNIQUE (business_id, slot)` no sirve.

El `WHERE` de un `EXCLUDE` no puede depender de `now()`: un hold vencido sigue bloqueando hasta
que el servicio lo pasa a `VENCIDO`. Por eso `BookingService` vence los holds del puesto en la
misma transacción, antes de insertar o confirmar (vencimiento perezoso, §1.3).
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, Text, literal_column, text
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.booking_state import BLOCKING_BOOKING_STATUSES
from app.domain.enums import (
    BOOKING_SOURCE_ENUM,
    BOOKING_STATUS_ENUM,
    CHANNEL_ENUM,
    BookingSource,
    BookingStatus,
    Channel,
)
from app.persistence.db.base import TenantScopedModel
from app.persistence.models._constraints import (
    ALIVE,
    check,
    parent_key,
    pg_enum,
    tenant_fk,
    tenant_index,
    unique_alive,
    voidable_table_args,
)

#: Nombre del constraint de exclusión. Los servicios lo usan para traducir el choque a
#: "horario ocupado" en vez de un error genérico.
BOOKING_OVERLAP_CONSTRAINT = "xc_bookings_sin_solapamiento"

#: Estados que ocupan el puesto (§2.1). Única fuente: el dominio. Se ordenan como el enum
#: para que el `WHERE` del EXCLUDE sea determinista.
BLOCKING_STATUSES: tuple[BookingStatus, ...] = tuple(
    s for s in BookingStatus if s in BLOCKING_BOOKING_STATUSES
)
_BLOCKING_SQL = ", ".join(f"'{s.value}'" for s in BLOCKING_STATUSES)
_HOLD_STATUSES_SQL = f"'{BookingStatus.PENDIENTE_SENA.value}', " + (
    f"'{BookingStatus.PENDIENTE_COTIZACION.value}'"
)


class Booking(TenantScopedModel):
    """El turno. Una sola tabla para turnero web, panel y agenda interna (R-C-036).

    **[corregir]** la agenda interna ya no se saltea el bloqueo: pasa por el mismo `EXCLUDE`.
    """

    __tablename__ = "bookings"
    __table_args__ = voidable_table_args(
        parent_key(),
        tenant_fk("resource_id", "resources"),
        tenant_fk("customer_id", "customers"),
        tenant_fk("vehicle_id", "vehicles"),
        tenant_fk("service_id", "services"),
        tenant_fk("vehicle_size_id", "vehicle_sizes"),
        # Ciclo con `quotes.booking_id`: esta FK se agrega con ALTER después de crear `quotes`.
        tenant_fk("quote_id", "quotes", use_alter=True),
        # Cubre la FK del puesto y sirve a la grilla de disponibilidad (puesto + hora).
        tenant_index("bookings", "resource_id", "start_at"),
        tenant_index("bookings", "customer_id"),
        tenant_index("bookings", "vehicle_id"),
        tenant_index("bookings", "service_id"),
        tenant_index("bookings", "vehicle_size_id"),
        tenant_index("bookings", "quote_id"),
        # [corregir] R-T-020: el código de turno no tenía unicidad.
        unique_alive("bookings", "code"),
        check("end_at > start_at", "rango_valido"),
        # [corregir] C-03: la cotización pendiente tapaba el horario para siempre.
        check(
            f"status NOT IN ({_HOLD_STATUSES_SQL}) OR hold_expires_at IS NOT NULL",
            "hold_obligatorio",
        ),
        check("duration_min > 0", "duracion_positiva"),
        check("price_cents IS NULL OR price_cents > 0", "precio_positivo"),
        check("deposit_bps BETWEEN 0 AND 10000", "sena_bps_rango"),
        check("deposit_required_cents >= 0", "sena_no_negativa"),
        # [corregir] R-C-032: sin precio no hay seña.
        check("price_cents IS NOT NULL OR deposit_required_cents = 0", "sin_precio_sin_sena"),
        check("length(notes) <= 500", "notas_largo"),
        ExcludeConstraint(
            ("tenant_id", "="),
            ("resource_id", "="),
            (literal_column("tstzrange(start_at, end_at, '[)')"), "&&"),
            where=text(f"status IN ({_BLOCKING_SQL}) AND {ALIVE}"),
            using="gist",
            name=BOOKING_OVERLAP_CONSTRAINT,
        ).ddl_if(dialect="postgresql"),
    )

    #: `TUR-…`/`INT-…` legado o generado.
    code: Mapped[str] = mapped_column(Text, nullable=False)
    #: QUIÉN lo cargó; distinto del canal.
    source: Mapped[BookingSource] = mapped_column(
        pg_enum(BookingSource, BOOKING_SOURCE_ENUM), nullable=False
    )
    #: D-004: de DÓNDE vino el cliente.
    channel: Mapped[Channel] = mapped_column(pg_enum(Channel, CHANNEL_ENUM), nullable=False)
    status: Mapped[BookingStatus] = mapped_column(
        pg_enum(BookingStatus, BOOKING_STATUS_ENUM), nullable=False
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Vence la retención de un `PENDIENTE_SEÑA` o `PENDIENTE_COTIZACION`.
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Toda captura crea o resuelve cliente (R-C-012).
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: La patente es opcional en la web (R-T-016).
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    service_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vehicle_size_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    service_name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    #: Snapshot: el bloqueo usa esta copia, no la duración actual del catálogo (R-T-012).
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Snapshot; NULL mientras sea a cotizar.
    price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deposit_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    deposit_required_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    quote_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    terms_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Las altas del panel NO estampan aceptación (**[corregir]** C-15).
    terms_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    legacy_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScheduleBlock(TenantScopedModel):
    """Bloqueo de agenda (R-T-013, R-T-052…055). Desactivar = anular con `DESACTIVADO`.

    Fuera del `EXCLUDE` de `bookings`: un bloqueo se puede crear sobre turnos vigentes y no
    los cancela (R-T-052). Lo chequea el motor de disponibilidad (F5).
    """

    __tablename__ = "schedule_blocks"
    __table_args__ = voidable_table_args(
        tenant_fk("resource_id", "resources"),
        tenant_fk("created_by_user_id", "users"),
        tenant_index("schedule_blocks", "resource_id"),
        tenant_index("schedule_blocks", "created_by_user_id"),
        check("ends_at > starts_at", "rango_valido"),
    )

    #: NULL = todo el lavadero.
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: Día completo = `[00:00, 00:00 del día siguiente)` en la zona del tenant.
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(
        Text, nullable=False, default="Bloqueo operativo", server_default="Bloqueo operativo"
    )
    #: Reemplaza el responsable hardcodeado del legacy.
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
