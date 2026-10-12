"""Tenant — la raíz del aislamiento. No lleva `tenant_id` (ADR-0001, lista blanca).

FASE-3-CONTRATO X1: el lavadero ES el tenant (no se crea `businesses`). Suma `timezone` y
`currency`. FASE-4-CONTRATO Y10: `slug` (la URL pública de F5) vive acá; el resto de la
configuración, en `tenant_settings` (1:1).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.slug import SLUG_PATTERN
from app.persistence.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.persistence.db.mixins import TenantMixin, VoidableMixin
from app.persistence.models._constraints import ALIVE, check, pg_check, voidable_table_args

DEFAULT_TIMEZONE = "America/Argentina/Cordoba"
DEFAULT_CURRENCY = "ARS"


#: D4-11: el % sugerido al activar la seña, en puntos básicos.
DEFAULT_DEPOSIT_BPS = 2000
DEFAULT_TOLERANCE_MIN = 20
#: 12 h: antes `DEFAULT_LATE_THRESHOLD_MIN` de `domain/booking_state.py`.
DEFAULT_LATE_CANCEL_THRESHOLD_MIN = 720
DEFAULT_HOLD_MIN = 30
#: D-002: anticipación mínima para reservar.
DEFAULT_MIN_ADVANCE_MIN = 5
WELCOME_TEXT_MAX = 280
DEFAULT_PALETTE = "default"
DEFAULT_FONT = "default"
#: Clave de catálogo de paleta y fuente: la lista concreta la valida el schema (PR 4.5).
CATALOG_KEY_PATTERN = "^[a-z0-9-]{1,40}$"


class Tenant(Base, UUIDPrimaryKeyMixin, TimestampMixin, VoidableMixin):
    __tablename__ = "tenants"
    __table_args__ = voidable_table_args(
        pg_check("currency ~ '^[A-Z]{3}$'", "currency_iso"),
        pg_check(f"slug ~ '{SLUG_PATTERN}'", "slug_formato"),
        # Único global entre vivos (Y10): `tenants` no tiene `tenant_id`, no sirve `unique_alive`.
        Index(
            "ux_tenants_slug",
            "slug",
            unique=True,
            postgresql_where=text(ALIVE),
            sqlite_where=text(ALIVE),
        ),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    #: Hardcodeada en el legacy. Fechas de turno y demora se calculan en esta zona.
    timezone: Mapped[str] = mapped_column(
        Text, nullable=False, default=DEFAULT_TIMEZONE, server_default=DEFAULT_TIMEZONE
    )
    currency: Mapped[str] = mapped_column(
        Text, nullable=False, default=DEFAULT_CURRENCY, server_default=DEFAULT_CURRENCY
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)


class TenantSettings(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """Configuración del negocio, 1:1 con el tenant — FASE-4-CONTRATO §3.2.

    PK `id` + `UNIQUE (tenant_id)`: ADR-0004 prohíbe `tenant_id` como PK. Sin anulación: la
    fila nace con el tenant (`provision_tenant`) y vive lo que él. Los parámetros que en F3
    eran constantes o argumentos (tolerancia, cancelación tardía, retención) se leen de acá
    en los servicios de aplicación; el dominio los sigue recibiendo por parámetro.
    """

    __tablename__ = "tenant_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id"),
        check(f"length(welcome_text) <= {WELCOME_TEXT_MAX}", "bienvenida_largo"),
        # El % se conserva con la seña apagada (D4-4): nunca 0.
        check("deposit_bps BETWEEN 1 AND 10000", "sena_bps_rango"),
        check("tolerance_min > 0", "tolerancia_positiva"),
        check("late_cancel_threshold_min > 0", "cancelacion_tardia_positiva"),
        check("hold_min > 0", "retencion_positiva"),
        check("min_advance_min >= 0", "anticipacion_no_negativa"),
        pg_check(f"palette ~ '{CATALOG_KEY_PATTERN}'", "paleta_formato"),
        pg_check(f"font ~ '{CATALOG_KEY_PATTERN}'", "fuente_formato"),
    )

    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    whatsapp_e164: Mapped[str | None] = mapped_column(Text, nullable=True)
    instagram_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    welcome_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_policy: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: D4-4: un interruptor para todos los servicios.
    deposit_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    deposit_bps: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_DEPOSIT_BPS,
        server_default=str(DEFAULT_DEPOSIT_BPS),
    )
    tolerance_min: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_TOLERANCE_MIN,
        server_default=str(DEFAULT_TOLERANCE_MIN),
    )
    late_cancel_threshold_min: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_LATE_CANCEL_THRESHOLD_MIN,
        server_default=str(DEFAULT_LATE_CANCEL_THRESHOLD_MIN),
    )
    hold_min: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_HOLD_MIN, server_default=str(DEFAULT_HOLD_MIN)
    )
    min_advance_min: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_MIN_ADVANCE_MIN,
        server_default=str(DEFAULT_MIN_ADVANCE_MIN),
    )
    palette: Mapped[str] = mapped_column(
        Text, nullable=False, default=DEFAULT_PALETTE, server_default=DEFAULT_PALETTE
    )
    font: Mapped[str] = mapped_column(
        Text, nullable=False, default=DEFAULT_FONT, server_default=DEFAULT_FONT
    )
    #: Clave en R2 (PR 4.5).
    logo_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: PR 4.6.
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def effective_deposit_bps(self) -> int:
        """El % que se guarda en cada ítem al reservar: el configurado, o 0 con la seña apagada."""
        return self.deposit_bps if self.deposit_enabled else 0
