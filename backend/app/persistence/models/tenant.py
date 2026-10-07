"""Tenant — la raíz del aislamiento. No lleva `tenant_id` (ADR-0001, lista blanca).

FASE-3-CONTRATO X1: el lavadero ES el tenant (no se crea `businesses`). Suma `timezone` y
`currency`; el resto de la configuración del negocio es Fase 4.
"""

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.persistence.db.mixins import VoidableMixin
from app.persistence.models._constraints import pg_check, voidable_table_args

DEFAULT_TIMEZONE = "America/Argentina/Cordoba"
DEFAULT_CURRENCY = "ARS"


class Tenant(Base, UUIDPrimaryKeyMixin, TimestampMixin, VoidableMixin):
    __tablename__ = "tenants"
    __table_args__ = voidable_table_args(
        pg_check("currency ~ '^[A-Z]{3}$'", "currency_iso"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    #: Hardcodeada en el legacy. Fechas de turno y demora se calculan en esta zona.
    timezone: Mapped[str] = mapped_column(
        Text, nullable=False, default=DEFAULT_TIMEZONE, server_default=DEFAULT_TIMEZONE
    )
    currency: Mapped[str] = mapped_column(
        Text, nullable=False, default=DEFAULT_CURRENCY, server_default=DEFAULT_CURRENCY
    )
