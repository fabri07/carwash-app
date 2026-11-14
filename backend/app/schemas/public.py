"""Turnero público — FASE-5-CONTRATO §4.2 (PR 5.2).

Las respuestas **no** llevan `tenant_id`, ids de puesto ni nada de otro cliente (§6): el negocio
se nombra por su slug y la disponibilidad son horas. `extra="ignore"` en los cuerpos: lo que no
se pide se descarta.
"""

import datetime as dt
import uuid
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field

from app.application.services.booking_service import MAX_CANCELLATION_REASON
from app.domain.enums import (
    BookingStatus,
    CancellationClassification,
    DepositStatus,
    PricingMode,
)
from app.schemas.auth import _sin_nul
from app.schemas.branding import FontOut, PaletteOut
from app.schemas.common import CamelModel


def _trim(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _blank_to_none(value: object) -> object:
    return None if isinstance(value, str) and not value.strip() else value


Name = Annotated[
    str, BeforeValidator(_sin_nul), BeforeValidator(_trim), Field(min_length=1, max_length=80)
]
Phone = Annotated[str, BeforeValidator(_sin_nul), Field(min_length=1, max_length=40)]
Code = Annotated[str, BeforeValidator(_sin_nul), Field(min_length=1, max_length=20)]
Plate = Annotated[
    str, BeforeValidator(_sin_nul), BeforeValidator(_trim), Field(min_length=1, max_length=12)
]
Reason = Annotated[
    str,
    BeforeValidator(_sin_nul),
    BeforeValidator(_trim),
    Field(max_length=MAX_CANCELLATION_REASON),
]


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ── El negocio ───────────────────────────────────────────────────────────────


class PublicBusinessResponse(CamelModel):
    slug: str
    name: str
    #: Zona del negocio (Z9): el turnero muestra las horas en esta zona, no en la del navegador.
    timezone: str
    currency: str
    palette: PaletteOut
    font: FontOut
    logo_url: str | None
    welcome_text: str | None
    address: str | None
    whatsapp_e164: str | None
    cancellation_policy: str | None
    deposit_enabled: bool
    #: Alias/CBU/titular. Sin texto, el turnero dice "Coordiná el pago de la seña por WhatsApp".
    deposit_instructions: str | None
    #: Minutos que el turno queda retenido esperando la seña (D5-1).
    hold_min: int
    booking_horizon_days: int


# ── Catálogo ─────────────────────────────────────────────────────────────────


class PublicVehicleSize(CamelModel):
    id: uuid.UUID
    label: str


class PublicServicePrice(CamelModel):
    vehicle_size_id: uuid.UUID
    price_cents: int
    duration_min: int
    deposit_cents: int


class PublicServiceOut(CamelModel):
    id: uuid.UUID
    name: str
    category_name: str | None
    pricing_mode: PricingMode
    #: `false` en `A_COTIZAR`: "desde $X" y "Consultar por WhatsApp" (D5-5).
    bookable: bool
    deposit_bps: int
    prices: list[PublicServicePrice]


class PublicCatalogResponse(CamelModel):
    vehicle_sizes: list[PublicVehicleSize]
    services: list[PublicServiceOut]


# ── Disponibilidad ───────────────────────────────────────────────────────────


class PublicSlot(CamelModel):
    start_at: dt.datetime
    end_at: dt.datetime


class PublicAvailabilityResponse(CamelModel):
    date: dt.date
    timezone: str
    duration_min: int
    #: Solo horas: nunca qué puesto ni quién ocupa (§6).
    slots: list[PublicSlot]


# ── Reservar, ver y cancelar ─────────────────────────────────────────────────


class PublicBookingCreate(_Body):
    vehicle_size_id: uuid.UUID
    service_ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=20)]
    #: Con zona: uno de los `start_at` de la disponibilidad.
    start_at: AwareDatetime
    name: Name
    #: WhatsApp argentino; se normaliza a E.164 (422 `INVALID_PHONE` si no se puede).
    phone: Phone
    #: Opcional (D5-3).
    plate: Annotated[Plate | None, BeforeValidator(_blank_to_none)] = None
    #: Tiene que ser `true` (§4.2 paso 8).
    accept_policy: bool


class PublicBookingItem(CamelModel):
    name: str
    duration_min: int
    price_cents: int


class PublicBookingResponse(CamelModel):
    code: str
    status: BookingStatus
    start_at: dt.datetime
    end_at: dt.datetime
    timezone: str
    vehicle_size_label: str
    services: list[PublicBookingItem]
    total_cents: int
    #: Seña a pagar (0 sin seña o en lista de espera).
    deposit_cents: int
    #: Hasta cuándo se retiene el horario esperando la seña (`PENDIENTE_SEÑA`).
    hold_expires_at: dt.datetime | None
    can_cancel: bool


class PublicBookingLookup(_Body):
    code: Code
    phone: Phone


class PublicBookingCancel(PublicBookingLookup):
    reason: Annotated[Reason | None, BeforeValidator(_blank_to_none)] = None


class PublicCancellationResponse(CamelModel):
    code: str
    status: BookingStatus
    #: D-005: `NORMAL`, `TARDIA` o `POSTERIOR_AL_TURNO`.
    classification: CancellationClassification
    #: El caso de la seña que abrió la cancelación (`SIN_PAGO` si no había pagado).
    deposit_status: DepositStatus
