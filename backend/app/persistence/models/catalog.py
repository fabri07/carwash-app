"""Catálogo del lavadero — FASE-3-CONTRATO §1.1.

Tamaños, servicios, precios por servicio × tamaño, puestos, franjas de atención y medios de
pago. Todo es del tenant: nada de esto es global.
"""

import uuid
from datetime import time

from sqlalchemy import BigInteger, Boolean, Integer, SmallInteger, Text, Time, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import PRICING_MODE_ENUM, PricingMode
from app.persistence.db.base import TenantScopedModel
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

#: Códigos de catálogo (`AUTO`, `SUV`, `EFECTIVO`…): mayúsculas, dígitos y `_`.
CODE_PATTERN = "^[A-Z0-9_]{1,30}$"


class VehicleSize(TenantScopedModel):
    """Tamaños (`AUTO`, `SUV`, `PICKUP`, `UTILITARIO`, `CAMION_PEQUENO`)."""

    __tablename__ = "vehicle_sizes"
    __table_args__ = voidable_table_args(
        parent_key(),
        pg_check(f"code ~ '{CODE_PATTERN}'", "code_formato"),
        unique_alive("vehicle_sizes", "code"),
    )

    code: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class Service(TenantScopedModel):
    """D-006. La modalidad es explícita: borrar el precio NO lo vuelve "a cotizar" (R-T-002)."""

    __tablename__ = "services"
    __table_args__ = voidable_table_args(
        parent_key(),
        unique_alive("services", "name"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    pricing_mode: Mapped[PricingMode] = mapped_column(
        pg_enum(PricingMode, PRICING_MODE_ENUM), nullable=False
    )
    #: La prosa de `Regla_horaria`. La regla estructurada es F5.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ServicePrice(TenantScopedModel):
    """Servicio × tamaño. La coherencia con la modalidad la valida `CatalogService` (cruza tablas).

    El monto de la seña no se guarda: `round_half_up(price × bps / 10000)` (R-T-003).
    """

    __tablename__ = "service_prices"
    __table_args__ = voidable_table_args(
        tenant_fk("service_id", "services"),
        tenant_fk("vehicle_size_id", "vehicle_sizes"),
        tenant_index("service_prices", "service_id"),
        tenant_index("service_prices", "vehicle_size_id"),
        unique_alive("service_prices", "service_id", "vehicle_size_id"),
        check("price_cents IS NULL OR price_cents > 0", "precio_positivo"),
        check("duration_min > 0", "duracion_positiva"),
        check("deposit_bps BETWEEN 0 AND 10000", "sena_bps_rango"),
    )

    service_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vehicle_size_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: NULL en `A_COTIZAR` (D-006).
    price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Obligatoria aun sin precio (D-006).
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Única fuente del % de seña (hoy en 3 lugares, R-O-031).
    deposit_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class Resource(TenantScopedModel):
    """Puesto de lavado (R-T-010). Un lavadero de un puesto es una fila: no hay caso especial."""

    __tablename__ = "resources"
    __table_args__ = voidable_table_args(
        parent_key(),
        unique_alive("resources", "name"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class BusinessHours(TenantScopedModel):
    """Franja de atención (R-T-004/006). Varias por día. Qué servicio va en qué franja es F5."""

    __tablename__ = "business_hours"
    __table_args__ = voidable_table_args(
        check("weekday BETWEEN 1 AND 7", "weekday_iso"),
        check("closes_at > opens_at", "franja_valida"),
        tenant_index("business_hours", "weekday"),
    )

    #: ISO: 1 = lunes.
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    opens_at: Mapped[time] = mapped_column(Time, nullable=False)
    closes_at: Mapped[time] = mapped_column(Time, nullable=False)


class PaymentMethod(TenantScopedModel):
    """Medio de pago (R-O-027/028). Reemplaza el rango fijo `A28:C31` de `getCommissionRate_`."""

    __tablename__ = "payment_methods"
    __table_args__ = voidable_table_args(
        parent_key(),
        pg_check(f"code ~ '{CODE_PATTERN}'", "code_formato"),
        check("commission_bps BETWEEN 0 AND 10000", "comision_bps_rango"),
        unique_alive("payment_methods", "code"),
    )

    code: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    commission_bps: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: Caja, Banco/CVU, Mercado Pago Point.
    settlement_account: Mapped[str | None] = mapped_column(Text, nullable=True)
    for_income: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    for_expense: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
