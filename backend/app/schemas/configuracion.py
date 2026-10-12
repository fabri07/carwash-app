"""Configuración del negocio y catálogos de operación — FASE-4-CONTRATO §3.2 y §4 (PR 4.2).

Mismas reglas que `team.py`: `extra="ignore"` en los cuerpos de entrada (un `tenant_id`
inyectado se descarta en silencio; el tenant sale del token) y campos ausentes no se tocan.

Los límites de largo que no están en la base (dirección, política, URL) son de la API: la
base solo pone los que son regla de negocio (`welcome_text` ≤ 280).
"""

import uuid
from datetime import datetime, time
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.application.services.settings_service import BusinessSettings
from app.domain.money import BPS_DENOMINATOR
from app.domain.phone import normalize_phone_ar
from app.domain.slug import SLUG_PATTERN
from app.persistence.models.catalog import CODE_PATTERN
from app.persistence.models.tenant import WELCOME_TEXT_MAX
from app.schemas.auth import _sin_nul
from app.schemas.common import CamelModel
from app.schemas.team import _nombre


def _texto_opcional(value: object) -> object:
    """Sin espacios alrededor; vacío es "sin dato" (`None`)."""
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return value


def _whatsapp(value: str | None) -> str | None:
    """Lo tipeado (`11 4444-5555`, `+54 9 11…`) → E.164. Inválido o ambiguo → 422 (R7)."""
    if value is None:
        return None
    result = normalize_phone_ar(value)
    if result.e164 is None:
        raise ValueError(f"not a valid Argentine mobile number ({result.reason})")
    return result.e164


def _upper(value: object) -> object:
    return value.strip().upper() if isinstance(value, str) else value


def _slug(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


Address = Annotated[
    Annotated[str, StringConstraints(max_length=200)] | None,
    BeforeValidator(_sin_nul),
    BeforeValidator(_texto_opcional),
]
WelcomeText = Annotated[
    Annotated[str, StringConstraints(max_length=WELCOME_TEXT_MAX)] | None,
    BeforeValidator(_sin_nul),
    BeforeValidator(_texto_opcional),
]
CancellationPolicy = Annotated[
    Annotated[str, StringConstraints(max_length=2000)] | None,
    BeforeValidator(_sin_nul),
    BeforeValidator(_texto_opcional),
]
InstagramUrl = Annotated[
    Annotated[str, StringConstraints(max_length=200, pattern=r"^https?://\S+$")] | None,
    BeforeValidator(_sin_nul),
    BeforeValidator(_texto_opcional),
]
Whatsapp = Annotated[
    str | None,
    BeforeValidator(_sin_nul),
    BeforeValidator(_texto_opcional),
    AfterValidator(_whatsapp),
]
Slug = Annotated[
    str, BeforeValidator(_sin_nul), BeforeValidator(_slug), Field(pattern=SLUG_PATTERN)
]
Code = Annotated[
    str, BeforeValidator(_sin_nul), BeforeValidator(_upper), Field(pattern=CODE_PATTERN)
]
Label = Annotated[
    str, BeforeValidator(_sin_nul), StringConstraints(max_length=60), AfterValidator(_nombre)
]
SortOrder = Annotated[int, Field(ge=0, le=1000)]
Bps = Annotated[int, Field(ge=0, le=BPS_DENOMINATOR)]

#: Campos que no admiten `null` explícito en el `PATCH` (en la base son NOT NULL).
_NO_NULLABLE = frozenset(
    {
        "slug",
        "deposit_enabled",
        "deposit_bps",
        "tolerance_min",
        "late_cancel_threshold_min",
        "hold_min",
        "min_advance_min",
    }
)


# ── Configuración ─────────────────────────────────────────────────────────────


class SettingsUpdate(BaseModel):
    """`PATCH /settings` (OWNER). Campos ausentes no se tocan; `null` borra un texto opcional.

    `deposit_bps` se conserva con la seña apagada (D4-4): apagar la seña no lo pierde.
    """

    model_config = ConfigDict(extra="ignore")

    slug: Slug | None = None
    address: Address = None
    whatsapp_e164: Whatsapp = None
    instagram_url: InstagramUrl = None
    welcome_text: WelcomeText = None
    cancellation_policy: CancellationPolicy = None
    deposit_enabled: bool | None = None
    #: 1..10000: el % se conserva con la seña apagada, nunca es 0 (CHECK de la base).
    deposit_bps: Annotated[int, Field(ge=1, le=BPS_DENOMINATOR)] | None = None
    #: Hasta 4 h de tolerancia de llegada.
    tolerance_min: Annotated[int, Field(gt=0, le=240)] | None = None
    #: Hasta 7 días antes del turno.
    late_cancel_threshold_min: Annotated[int, Field(gt=0, le=10_080)] | None = None
    #: Hasta 1 día de retención esperando la seña.
    hold_min: Annotated[int, Field(gt=0, le=1_440)] | None = None
    #: Hasta 7 días de anticipación mínima.
    min_advance_min: Annotated[int, Field(ge=0, le=10_080)] | None = None

    @model_validator(mode="after")
    def _sin_null_en_obligatorios(self) -> Self:
        nulos = sorted(f for f in self.model_fields_set & _NO_NULLABLE if getattr(self, f) is None)
        if nulos:
            raise ValueError(f"must not be null: {', '.join(nulos)}")
        return self

    def changes(self) -> dict[str, object]:
        """Solo los campos de `tenant_settings` presentes en el cuerpo (sin `slug`)."""
        return {f: getattr(self, f) for f in self.model_fields_set if f != "slug"}


class SettingsResponse(BaseModel):
    slug: str
    address: str | None
    whatsapp_e164: str | None
    instagram_url: str | None
    welcome_text: str | None
    cancellation_policy: str | None
    deposit_enabled: bool
    deposit_bps: int
    tolerance_min: int
    late_cancel_threshold_min: int
    hold_min: int
    min_advance_min: int
    palette: str
    font: str
    onboarding_completed: bool
    updated_at: datetime

    @classmethod
    def of(cls, business: BusinessSettings) -> "SettingsResponse":
        s = business.settings
        return cls(
            slug=business.tenant.slug,
            address=s.address,
            whatsapp_e164=s.whatsapp_e164,
            instagram_url=s.instagram_url,
            welcome_text=s.welcome_text,
            cancellation_policy=s.cancellation_policy,
            deposit_enabled=s.deposit_enabled,
            deposit_bps=s.deposit_bps,
            tolerance_min=s.tolerance_min,
            late_cancel_threshold_min=s.late_cancel_threshold_min,
            hold_min=s.hold_min,
            min_advance_min=s.min_advance_min,
            palette=s.palette,
            font=s.font,
            onboarding_completed=s.onboarding_completed_at is not None,
            updated_at=s.updated_at,
        )


# ── Tamaños ───────────────────────────────────────────────────────────────────


class VehicleSizeCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: Code
    label: Label
    sort_order: SortOrder = 0


class VehicleSizeUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: Code | None = None
    label: Label | None = None
    sort_order: SortOrder | None = None


class VehicleSizeResponse(CamelModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    code: str
    label: str
    sort_order: int
    created_at: datetime
    updated_at: datetime


# ── Puestos ───────────────────────────────────────────────────────────────────


class ResourceCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Label
    sort_order: SortOrder = 0


class ResourceUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Label | None = None
    sort_order: SortOrder | None = None


class ResourceResponse(CamelModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    sort_order: int
    created_at: datetime
    updated_at: datetime


# ── Franjas horarias ──────────────────────────────────────────────────────────

Weekday = Annotated[int, Field(ge=1, le=7)]


class BusinessHoursCreate(BaseModel):
    """Franja de atención. `weekday` ISO: 1 = lunes … 7 = domingo. Varias por día."""

    model_config = ConfigDict(extra="ignore")

    weekday: Weekday
    opens_at: time
    closes_at: time

    @model_validator(mode="after")
    def _cierra_despues(self) -> Self:
        if self.closes_at <= self.opens_at:
            raise ValueError("closes_at must be after opens_at")
        return self


class BusinessHoursUpdate(BaseModel):
    """La franja resultante (lo nuevo con lo que ya estaba) la valida el servicio."""

    model_config = ConfigDict(extra="ignore")

    weekday: Weekday | None = None
    opens_at: time | None = None
    closes_at: time | None = None


class BusinessHoursResponse(CamelModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    weekday: int
    opens_at: time
    closes_at: time
    created_at: datetime
    updated_at: datetime


# ── Medios de pago ────────────────────────────────────────────────────────────

SettlementAccount = Annotated[
    Annotated[str, StringConstraints(max_length=60)] | None,
    BeforeValidator(_sin_nul),
    BeforeValidator(_texto_opcional),
]


class PaymentMethodCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: Code
    label: Label
    #: Comisión del medio (crédito, Point), en puntos básicos.
    commission_bps: Bps = 0
    #: A qué cuenta va la plata: Caja, Banco, Mercado Pago.
    settlement_account: SettlementAccount = None
    for_income: bool = True
    for_expense: bool = False


class PaymentMethodUpdate(BaseModel):
    """`settlement_account: ""` o `null` lo borra."""

    model_config = ConfigDict(extra="ignore")

    code: Code | None = None
    label: Label | None = None
    commission_bps: Bps | None = None
    settlement_account: SettlementAccount = None
    for_income: bool | None = None
    for_expense: bool | None = None


class PaymentMethodResponse(CamelModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    code: str
    label: str
    commission_bps: int
    settlement_account: str | None
    for_income: bool
    for_expense: bool
    created_at: datetime
    updated_at: datetime
