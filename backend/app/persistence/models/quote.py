"""Cotizaciones — FASE-3-CONTRATO §1.4 y §2.3. **[diseñar]**: el legacy es insert-only (R-C-027).

Una cotización puede nacer sin turno (la fila real no tenía, R-C-028). A lo sumo una viva
(`PENDIENTE`, `COTIZADO`, `ACEPTADO`) por turno.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import (
    QUOTE_STATUS_ENUM,
    SUPPLIES_PURCHASER_ENUM,
    QuoteStatus,
    SuppliesPurchaser,
)
from app.persistence.db.base import TenantScopedModel
from app.persistence.models._constraints import (
    check,
    parent_key,
    pg_enum,
    tenant_fk,
    tenant_index,
    unique_alive,
    voidable_table_args,
)

_OPEN_SQL = ", ".join(
    f"'{s.value}'" for s in (QuoteStatus.PENDIENTE, QuoteStatus.COTIZADO, QuoteStatus.ACEPTADO)
)
_PRICED_SQL = f"'{QuoteStatus.COTIZADO.value}', '{QuoteStatus.ACEPTADO.value}'"


class Quote(TenantScopedModel):
    __tablename__ = "quotes"
    __table_args__ = voidable_table_args(
        parent_key(),
        tenant_fk("customer_id", "customers"),
        tenant_fk("vehicle_id", "vehicles"),
        tenant_fk("service_id", "services"),
        tenant_fk("vehicle_size_id", "vehicle_sizes"),
        tenant_fk("booking_id", "bookings"),
        tenant_fk("decided_by_user_id", "users"),
        tenant_index("quotes", "customer_id"),
        tenant_index("quotes", "vehicle_id"),
        tenant_index("quotes", "service_id"),
        tenant_index("quotes", "vehicle_size_id"),
        tenant_index("quotes", "booking_id"),
        tenant_index("quotes", "decided_by_user_id"),
        unique_alive(
            "quotes", "booking_id", where=f"booking_id IS NOT NULL AND status IN ({_OPEN_SQL})"
        ),
        check("agreed_price_cents IS NULL OR agreed_price_cents > 0", "precio_positivo"),
        check("agreed_duration_min IS NULL OR agreed_duration_min > 0", "duracion_positiva"),
        check(
            f"status NOT IN ({_PRICED_SQL}) "
            "OR (agreed_price_cents IS NOT NULL AND agreed_duration_min IS NOT NULL)",
            "cotizado_completo",
        ),
        check("supplies_cost_cents IS NULL OR supplies_cost_cents >= 0", "insumos_no_negativos"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    service_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vehicle_size_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[QuoteStatus] = mapped_column(
        pg_enum(QuoteStatus, QUOTE_STATUS_ENUM), nullable=False
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: NULL = sin cotizar (el legacy usaba `0`, R-C-024).
    agreed_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Pisa la del catálogo.
    agreed_duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supplies_purchaser: Mapped[SuppliesPurchaser] = mapped_column(
        pg_enum(SuppliesPurchaser, SUPPLIES_PURCHASER_ENUM),
        nullable=False,
        default=SuppliesPurchaser.A_DEFINIR,
        server_default=SuppliesPurchaser.A_DEFINIR.value,
    )
    supplies_cost_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: TTL propio (R-C-026).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Lleva teléfono o patente adentro: PII (§7).
    legacy_id: Mapped[str | None] = mapped_column(Text, nullable=True)
