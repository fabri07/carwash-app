"""Servicios, categorías y plantillas — FASE-4-CONTRATO §3.3 y §4 (PR 4.3).

`extra="ignore"`: el `tenant_id` de un cuerpo se descarta. El `PATCH` de un servicio **no**
declara campos de precio ni de modalidad (§2.3): `extra="ignore"` los descartaría en silencio, y
el contrato lo deja en un test. `glossary_code` tampoco entra por el `POST`: solo lo fija la
plantilla. Los precios y la seña propia (D4-17) entran por `POST /services` y `PUT .../prices`.
"""

import uuid
from datetime import datetime, time
from typing import Annotated

from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from app.application.services.service_catalog_service import (
    MAX_RULE_ROWS,
    PriceInput,
    RuleInput,
)
from app.domain.enums import PricingMode, ServiceBookingMode
from app.domain.glossary import TemplateKind
from app.domain.search import normalize
from app.persistence.models.catalog import CATEGORY_NAME_MAX
from app.schemas.auth import _sin_nul
from app.schemas.catalog import SortOrder, Weekday, _Body, _naive_time, _nombre, _Out
from app.schemas.common import CamelModel


def _buscable(value: str) -> str:
    """El nombre tiene que dejar una clave de búsqueda: con solo caracteres de ancho cero o solo
    ideogramas (洗車), `normalize` queda vacío y el servicio sería inencontrable (Y8)."""
    cleaned = _nombre(value)
    if not normalize(cleaned):
        raise ValueError(
            "must contain at least one letter (a-z) or digit: a name made only of symbols, "
            "invisible or non-Latin characters cannot be searched"
        )
    return cleaned


CategoryName = Annotated[
    str, BeforeValidator(_sin_nul), Field(max_length=CATEGORY_NAME_MAX), AfterValidator(_buscable)
]
ServiceName = Annotated[
    str, BeforeValidator(_sin_nul), Field(max_length=80), AfterValidator(_buscable)
]
Notes = Annotated[str, BeforeValidator(_sin_nul), Field(max_length=500)]
GlossaryCode = Annotated[str, Field(pattern="^[a-z0-9_]{1,80}$")]
#: Tope de plata: $ 100.000.000,00 en centavos. Evita desbordes y typos de ceros.
MAX_PRICE_CENTS = 10_000_000_000
#: % de seña propio en puntos básicos (D4-17): mismo rango que el del negocio. `StrictInt` por lo
#: mismo que el precio.
DepositBps = Annotated[StrictInt, Field(ge=1, le=10000)]
MAX_DURATION_MIN = 24 * 60 * 14  # 14 días: el detailing con curado puede dejar el auto un tiempo


# ── Categorías ───────────────────────────────────────────────────────────────


class ServiceCategoryCreate(_Body):
    name: CategoryName
    sort_order: SortOrder = 0


class ServiceCategoryUpdate(_Body):
    name: CategoryName | None = None
    sort_order: SortOrder | None = None


class ServiceCategoryResponse(_Out):
    name: str
    sort_order: int


# ── Precios y servicios ──────────────────────────────────────────────────────


class ServicePriceInput(_Body):
    """Precio base de un tamaño (D4-6): siempre presente y positivo, en centavos."""

    vehicle_size_id: uuid.UUID
    # `StrictInt`: pydantic en modo lax acepta `true` como 1 (un precio de $0,01 por un typo).
    price_cents: Annotated[StrictInt, Field(gt=0, le=MAX_PRICE_CENTS)]
    duration_min: Annotated[StrictInt, Field(gt=0, le=MAX_DURATION_MIN)]

    def to_input(self) -> PriceInput:
        return PriceInput(self.vehicle_size_id, self.price_cents, self.duration_min)


class ScheduleRuleInput(_Body):
    """Una fila de regla horaria (FASE-5-CONTRATO §3.2): el servicio puede **arrancar** entre
    `window_start` y `last_start` (inclusive) del día ISO `weekday`. Hora local del negocio."""

    weekday: Weekday
    window_start: time
    last_start: time

    _sin_zona = field_validator("window_start", "last_start")(_naive_time)

    @model_validator(mode="after")
    def _ventana_valida(self) -> "ScheduleRuleInput":
        if self.last_start < self.window_start:
            raise ValueError("last_start must be at or after window_start")
        return self

    def to_input(self) -> RuleInput:
        return RuleInput(self.weekday, self.window_start, self.last_start)


ScheduleRules = Annotated[list[ScheduleRuleInput], Field(max_length=MAX_RULE_ROWS)]


class ScheduleRuleResponse(CamelModel):
    id: uuid.UUID
    weekday: int
    window_start: time
    last_start: time


class ServicePriceResponse(CamelModel):
    id: uuid.UUID
    vehicle_size_id: uuid.UUID
    price_cents: int
    duration_min: int


class ServiceCreate(_Body):
    name: ServiceName
    pricing_mode: PricingMode
    notes: Notes | None = None
    category_id: uuid.UUID | None = None
    #: % de seña propio (D4-17). Ausente o `null`: usa el del negocio.
    deposit_bps: DepositBps | None = None
    prices: Annotated[list[ServicePriceInput], Field(min_length=1)]
    #: F5 (§3.1): `SOLO_A_MANO` no aparece en el turnero.
    booking_mode: ServiceBookingMode = ServiceBookingMode.TURNERO
    #: F5 (§3.2): vacía = arranca en todo el horario del negocio.
    schedule_rules: ScheduleRules = []


class ServiceUpdate(_Body):
    """Sin precios ni modalidad. `notes: null` o `category_id: null` explícitos las borran.

    F5: `booking_mode` y `schedule_rules` (lista **completa**: reemplaza las reglas vivas; `[]` las
    borra; ausente no las toca). Ninguno de los dos acepta `null`.
    """

    name: ServiceName | None = None
    notes: Notes | None = None
    category_id: uuid.UUID | None = None
    booking_mode: ServiceBookingMode | None = None
    schedule_rules: ScheduleRules | None = None

    @model_validator(mode="after")
    def _name_no_nulo(self) -> "ServiceUpdate":
        for name in ("name", "booking_mode", "schedule_rules"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class ServicePricesPut(_Body):
    """Los precios de **todos** los tamaños vivos; `pricing_mode` opcional cambia la modalidad.

    `deposit_bps` opcional: si viene, fija el % de seña propio; `null` explícito lo borra (vuelve al
    del negocio); ausente no lo toca (D4-17).
    """

    pricing_mode: PricingMode | None = None
    deposit_bps: DepositBps | None = None
    prices: Annotated[list[ServicePriceInput], Field(min_length=1)]


class ServiceResponse(CamelModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    name: str
    pricing_mode: PricingMode
    notes: str | None
    category_id: uuid.UUID | None
    glossary_code: str | None
    #: % de seña propio (D4-17); `null` = usa el del negocio.
    deposit_bps: int | None
    #: F5 (§3.1).
    booking_mode: ServiceBookingMode
    #: F5 (§3.2): reglas vivas por día y hora; vacía = todo el horario del negocio.
    schedule_rules: list[ScheduleRuleResponse]
    prices: list[ServicePriceResponse]
    #: Tamaños vivos sin precio vivo: la UI los marca (§6). Vacío si el servicio está completo.
    missing_price_size_ids: list[uuid.UUID]


# ── Plantillas ───────────────────────────────────────────────────────────────


class TemplateTermResponse(CamelModel):
    glossary_code: str
    name: str
    type: str
    english: str
    synonyms: list[str]
    already_added: bool


class TemplateCategoryResponse(CamelModel):
    name: str
    sort_order: int
    items: list[TemplateTermResponse]


class TemplateResponse(CamelModel):
    kind: TemplateKind
    categories: list[TemplateCategoryResponse]


class TemplateApplyItem(_Body):
    glossary_code: GlossaryCode
    pricing_mode: PricingMode = PricingMode.PRECIO_FIJO
    prices: Annotated[list[ServicePriceInput], Field(min_length=1)]


class TemplateApply(_Body):
    kind: TemplateKind
    items: Annotated[list[TemplateApplyItem], Field(min_length=1, max_length=200)]


class TemplateSkippedResponse(CamelModel):
    glossary_code: str
    service_id: uuid.UUID
    reason: str


class TemplateApplyResponse(CamelModel):
    created: list[ServiceResponse]
    skipped: list[TemplateSkippedResponse]
    categories_created: list[ServiceCategoryResponse]
