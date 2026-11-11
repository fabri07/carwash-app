"""Configuración del negocio — FASE-4-CONTRATO §3.2 y §4 (PR 4.2a).

`extra="ignore"`: un `tenant_id` inyectado en el cuerpo se descarta. En el PATCH un campo ausente
no se toca; `null` explícito borra solo los opcionales (dirección, WhatsApp, Instagram, bienvenida,
política). Los numéricos nunca aceptan `null`.
"""

import re
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import urlparse

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.domain.branding import FONTS, PALETTES, is_font, is_palette
from app.domain.slug import SLUG_MAX, SLUG_MIN
from app.schemas.auth import _sin_nul
from app.schemas.common import CamelModel

_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")
_CODE = re.compile(r"^[a-z0-9_-]{1,40}$")
_INSTAGRAM_HOSTS = frozenset({"instagram.com", "www.instagram.com"})
_MAX_MINUTES = 60 * 24 * 30  # un mes: cualquier cosa mayor es un typo


def _trim(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _blank_to_none(value: object) -> object:
    """`"   "` borra el opcional igual que `null`: nunca se guarda un texto vacío."""
    cleaned = value.strip() if isinstance(value, str) else value
    return None if cleaned == "" else cleaned


def _whatsapp(value: str) -> str:
    if not _E164.fullmatch(value):
        raise ValueError("must be an E.164 number, e.g. +5493515551234")
    return value


def _instagram(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or (parsed.hostname or "") not in _INSTAGRAM_HOSTS:
        raise ValueError("must be an https://instagram.com/... URL")
    if not parsed.path.strip("/"):
        raise ValueError("the URL must point to a profile")
    return value


def _lower(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


Text = Annotated[str, BeforeValidator(_sin_nul), BeforeValidator(_trim)]
Whatsapp = Annotated[
    str, BeforeValidator(_sin_nul), BeforeValidator(_trim), AfterValidator(_whatsapp)
]
InstagramUrl = Annotated[
    str,
    BeforeValidator(_sin_nul),
    BeforeValidator(_trim),
    Field(max_length=200),
    AfterValidator(_instagram),
]
Minutes = Annotated[int, Field(gt=0, le=_MAX_MINUTES)]
SlugField = Annotated[
    str,
    BeforeValidator(_sin_nul),
    BeforeValidator(_lower),
    Field(min_length=SLUG_MIN, max_length=SLUG_MAX, pattern=r"^[a-z0-9-]+$"),
]


def _palette(value: str) -> str:
    if not is_palette(value):
        raise ValueError(f"unknown palette; expected one of {sorted(PALETTES)}")
    return value


def _font(value: str) -> str:
    if not is_font(value):
        raise ValueError(f"unknown font; expected one of {sorted(FONTS)}")
    return value


#: El formato lo vuelve a exigir el CHECK de la base; la lista, el catálogo en código (PR 4.5).
StyleCode = Annotated[str, BeforeValidator(_lower), Field(pattern=_CODE.pattern)]
PaletteCode = Annotated[StyleCode, AfterValidator(_palette)]
FontCode = Annotated[StyleCode, AfterValidator(_font)]


class SettingsUpdate(BaseModel):
    """PATCH: los campos ausentes no se tocan."""

    model_config = ConfigDict(extra="ignore")

    slug: SlugField | None = None
    address: Annotated[Text, Field(max_length=300)] | None = None
    whatsapp_e164: Whatsapp | None = None
    instagram_url: InstagramUrl | None = None
    welcome_text: Annotated[Text, Field(max_length=280)] | None = None
    cancellation_policy: Annotated[Text, Field(max_length=2000)] | None = None
    deposit_enabled: bool | None = None
    deposit_bps: Annotated[int, Field(ge=1, le=10000)] | None = None
    tolerance_min: Minutes | None = None
    late_cancel_threshold_min: Minutes | None = None
    hold_min: Minutes | None = None
    min_advance_min: Annotated[int, Field(ge=0, le=_MAX_MINUTES)] | None = None
    quote_validity_hours: Annotated[int, Field(gt=0, le=24 * 30)] | None = None
    palette: PaletteCode | None = None
    font: FontCode | None = None

    @field_validator(
        "address",
        "welcome_text",
        "cancellation_policy",
        "whatsapp_e164",
        "instagram_url",
        mode="before",
    )
    @classmethod
    def _vacio_es_null(cls, value: object) -> object:
        return _blank_to_none(value)

    @model_validator(mode="before")
    @classmethod
    def _no_null_en_obligatorios(cls, data: Any) -> Any:
        """Un `null` explícito en un campo NOT NULL sería un 500 (CHECK/NOT NULL): acá es 422."""
        if isinstance(data, dict):
            for name in _REQUIRED:
                if name in data and data[name] is None:
                    raise ValueError(f"{name} cannot be null")
        return data


_REQUIRED = (
    "slug",
    "deposit_enabled",
    "deposit_bps",
    "tolerance_min",
    "late_cancel_threshold_min",
    "hold_min",
    "min_advance_min",
    "quote_validity_hours",
    "palette",
    "font",
)


class SettingsResponse(CamelModel):
    slug: str
    name: str
    timezone: str
    currency: str
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
    quote_validity_hours: int
    palette: str
    font: str
    logo_key: str | None
    #: URL pública del logo en R2 (`R2_PUBLIC_BASE_URL` + `logo_key`); `null` sin logo o sin R2.
    logo_url: str | None
    onboarding_completed_at: datetime | None
