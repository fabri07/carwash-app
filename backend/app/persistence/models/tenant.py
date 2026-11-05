"""Tenant — la raíz del aislamiento. No lleva `tenant_id` (ADR-0001, lista blanca).

FASE-3-CONTRATO X1: el lavadero ES el tenant (no se crea `businesses`). Suma `timezone` y
`currency`. FASE-4-CONTRATO Y10: suma `slug` (la URL pública); el resto de la configuración,
branding incluido, vive en `tenant_settings` (1:1).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.slug import SLUG_PATTERN, candidate_slug
from app.persistence.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.persistence.db.mixins import TenantMixin, VoidableMixin
from app.persistence.models._constraints import ALIVE, check, pg_check, voidable_table_args

DEFAULT_TIMEZONE = "America/Argentina/Cordoba"
DEFAULT_CURRENCY = "ARS"

# Defaults de `tenant_settings`. Los repite la migración 0005 (congelados ahí); un test los compara.
DEFAULT_DEPOSIT_BPS = 2000  # 20 % (D4-11)
DEFAULT_TOLERANCE_MIN = 20
DEFAULT_LATE_CANCEL_THRESHOLD_MIN = 720  # 12 h
DEFAULT_HOLD_MIN = 30
DEFAULT_MIN_ADVANCE_MIN = 5  # D-002
DEFAULT_QUOTE_VALIDITY_HOURS = 24  # D4-16
#: Hasta que el PR 4.5 defina el catálogo de paletas y tipografías.
DEFAULT_PALETTE = "default"
DEFAULT_FONT = "default"


def _default_slug(context: object) -> str:
    """Slug para un `Tenant` creado sin uno (tests, scripts): derivado del nombre, con un sufijo
    aleatorio para que dos negocios con el mismo nombre no choquen. `provision_tenant` arma el
    suyo, más limpio, y reintenta ante una colisión."""
    name = context.get_current_parameters()["name"]  # type: ignore[attr-defined]
    return candidate_slug(str(name), suffix=uuid.uuid4().hex[:6])


class Tenant(Base, UUIDPrimaryKeyMixin, TimestampMixin, VoidableMixin):
    __tablename__ = "tenants"
    __table_args__ = voidable_table_args(
        pg_check("currency ~ '^[A-Z]{3}$'", "currency_iso"),
        pg_check(f"slug ~ '{SLUG_PATTERN}'", "slug_formato"),
        # Único global entre negocios vivos: es la URL pública. Anular un negocio libera el slug.
        Index(
            "ux_tenants_slug",
            "slug",
            unique=True,
            postgresql_where=text(ALIVE),
            sqlite_where=text(ALIVE),
        ),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    #: URL pública (`/<slug>`, F5). Generado del nombre y editable por el dueño (Y10).
    slug: Mapped[str] = mapped_column(Text, nullable=False, default=_default_slug)
    #: Hardcodeada en el legacy. Fechas de turno y demora se calculan en esta zona.
    timezone: Mapped[str] = mapped_column(
        Text, nullable=False, default=DEFAULT_TIMEZONE, server_default=DEFAULT_TIMEZONE
    )
    currency: Mapped[str] = mapped_column(
        Text, nullable=False, default=DEFAULT_CURRENCY, server_default=DEFAULT_CURRENCY
    )


class TenantSettings(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """Configuración del negocio, 1:1 con el tenant — FASE-4-CONTRATO §3.2.

    **Sin anulación**: es una fila por negocio, siempre viva (no usa `VoidableMixin`). El 1:1 es
    `UNIQUE (tenant_id)`; la PK sigue siendo `id` (ADR-0004 prohíbe `tenant_id` como PK).

    Los umbrales que en la F3 eran parámetros sueltos (tolerancia de demora, cancelación tardía,
    retención de la seña) viven acá; las funciones de dominio siguen recibiéndolos por parámetro y
    los lee el servicio de aplicación.
    """

    __tablename__ = "tenant_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id"),
        check("welcome_text IS NULL OR length(welcome_text) <= 280", "bienvenida_largo"),
        pg_check(
            "whatsapp_e164 IS NULL OR whatsapp_e164 ~ '^\\+[1-9][0-9]{7,14}$'", "whatsapp_e164"
        ),
        check("deposit_bps BETWEEN 1 AND 10000", "sena_bps_rango"),
        check("tolerance_min > 0", "tolerancia_positiva"),
        check("late_cancel_threshold_min > 0", "umbral_tardia_positivo"),
        check("hold_min > 0", "retencion_positiva"),
        check("min_advance_min >= 0", "anticipacion_no_negativa"),
        check("quote_validity_hours > 0", "vigencia_cotizacion_positiva"),
        pg_check("palette ~ '^[a-z0-9_-]{1,40}$'", "paleta_formato"),
        pg_check("font ~ '^[a-z0-9_-]{1,40}$'", "fuente_formato"),
    )

    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    whatsapp_e164: Mapped[str | None] = mapped_column(Text, nullable=True)
    instagram_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    welcome_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_policy: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: D4-4: un interruptor para todos los servicios, con un único %.
    deposit_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: El % se conserva con la seña apagada (D4-11: sugerido 20 %).
    deposit_bps: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_DEPOSIT_BPS,
        server_default=str(DEFAULT_DEPOSIT_BPS),
    )
    #: Tolerancia de demora para `cancel_for_delay`.
    tolerance_min: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_TOLERANCE_MIN,
        server_default=str(DEFAULT_TOLERANCE_MIN),
    )
    #: Umbral de cancelación tardía (antes `DEFAULT_LATE_THRESHOLD_MIN`).
    late_cancel_threshold_min: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_LATE_CANCEL_THRESHOLD_MIN,
        server_default=str(DEFAULT_LATE_CANCEL_THRESHOLD_MIN),
    )
    #: Retención de un `PENDIENTE_SEÑA`: `hold_expires_at = ahora + hold_min`.
    hold_min: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_HOLD_MIN, server_default=str(DEFAULT_HOLD_MIN)
    )
    #: Anticipación mínima para reservar (D-002).
    min_advance_min: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_MIN_ADVANCE_MIN,
        server_default=str(DEFAULT_MIN_ADVANCE_MIN),
    )
    #: Vigencia de una cotización desde que se pide (D4-16). El turno a cotizar vence con ella.
    quote_validity_hours: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_QUOTE_VALIDITY_HOURS,
        server_default=str(DEFAULT_QUOTE_VALIDITY_HOURS),
    )

    #: Valores del catálogo en código (PR 4.5). Acá solo el formato.
    palette: Mapped[str] = mapped_column(
        Text, nullable=False, default=DEFAULT_PALETTE, server_default=DEFAULT_PALETTE
    )
    font: Mapped[str] = mapped_column(
        Text, nullable=False, default=DEFAULT_FONT, server_default=DEFAULT_FONT
    )
    #: Clave del logo en R2 (PR 4.5).
    logo_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: PR 4.6: el wizard de alta terminó.
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
