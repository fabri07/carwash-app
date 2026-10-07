"""Clientes y vehículos — FASE-3-CONTRATO §1.2.

**[corregir]** R-C-004: `CLIENTES` se borraba y reconstruía desde las transacciones en cada
cierre. Acá el cliente es entidad de primera clase y nunca se reconstruye. El teléfono no es la
PK (tres normalizaciones distintas en el legacy): es un único parcial entre vivos.

PII (§7): `customers.name/phone_e164/phone_raw/email/legacy_ids`,
`vehicles.plate/plate_normalized/legacy_id` y todo `notes`. La patente es el PII fuerte.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import CHANNEL_ENUM, PLATE_FORMAT_ENUM, Channel, PlateFormat
from app.persistence.db.base import PGTEXTARRAY, TenantScopedModel
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


class Customer(TenantScopedModel):
    __tablename__ = "customers"
    __table_args__ = voidable_table_args(
        parent_key(),
        check("length(name) BETWEEN 1 AND 80", "nombre_largo"),
        pg_check("phone_e164 ~ '^\\+[1-9][0-9]{7,14}$'", "telefono_e164"),
        # [abierto] familias que comparten WhatsApp: se arranca estricto (§9, pregunta 2).
        unique_alive("customers", "phone_e164", where="phone_e164 IS NOT NULL"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    #: NULL permitido: agenda interna sin WhatsApp (R-C-001).
    phone_e164: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Lo que se tipeó.
    phone_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Canal de ORIGEN: se fija una vez (R-C-002 guardaba el último).
    acquisition_channel: Mapped[Channel | None] = mapped_column(
        pg_enum(Channel, CHANNEL_ENUM), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: `CLI-…` opacos del legacy. Contienen teléfono: PII.
    legacy_ids: Mapped[list[str] | None] = mapped_column(PGTEXTARRAY, nullable=True)


class Vehicle(TenantScopedModel):
    """Corregir la patente no cambia el `id` (**[corregir]** R-C-008)."""

    __tablename__ = "vehicles"
    __table_args__ = voidable_table_args(
        parent_key(),
        tenant_fk("vehicle_size_id", "vehicle_sizes"),
        tenant_index("vehicles", "vehicle_size_id"),
        pg_check("plate_normalized ~ '^[A-Z0-9]{1,10}$'", "patente_normalizada"),
        check("length(brand_model) <= 40", "marca_modelo_largo"),
        unique_alive("vehicles", "plate_normalized", where="plate_normalized IS NOT NULL"),
    )

    #: Como se ve.
    plate: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: `SIN000` y similares → NULL.
    plate_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: `OTRO` se acepta con advertencia: no bloquea la recepción.
    plate_format: Mapped[PlateFormat | None] = mapped_column(
        pg_enum(PlateFormat, PLATE_FORMAT_ENUM), nullable=True
    )
    #: Tamaño habitual; el del trabajo va en el job.
    vehicle_size_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    brand_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: `VEH-{tel}-{patente}`: PII.
    legacy_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class CustomerVehicle(TenantScopedModel):
    """Vínculo cliente ↔ vehículo con vigencia (R-C-010: hoy un auto no cambia de dueño).

    Se permiten N clientes vigentes sobre el mismo auto (familia/empresa).
    """

    __tablename__ = "customer_vehicles"
    __table_args__ = voidable_table_args(
        tenant_fk("customer_id", "customers"),
        tenant_fk("vehicle_id", "vehicles"),
        tenant_index("customer_vehicles", "customer_id"),
        tenant_index("customer_vehicles", "vehicle_id"),
        check("valid_to IS NULL OR valid_to > valid_from", "vigencia_valida"),
        unique_alive("customer_vehicles", "customer_id", "vehicle_id", where="valid_to IS NULL"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
