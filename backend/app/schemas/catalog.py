"""ABM de la configuración del catálogo — FASE-4-CONTRATO §4 (PR 4.2a).

Tamaños de vehículo, puestos, franjas de atención y medios de pago. `extra="ignore"`: el
`tenant_id` de un cuerpo se descarta. El `code` (tamaños y medios de pago) es la identidad y no
se edita.
"""

import uuid
from datetime import datetime, time
from typing import Annotated

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.persistence.models.catalog import CODE_PATTERN
from app.schemas.auth import _sin_nul
from app.schemas.common import CamelModel


def _upper(value: object) -> object:
    return value.strip().upper() if isinstance(value, str) else value


def _nombre(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


Code = Annotated[
    str, BeforeValidator(_sin_nul), BeforeValidator(_upper), Field(pattern=CODE_PATTERN)
]
Label = Annotated[str, BeforeValidator(_sin_nul), Field(max_length=60), AfterValidator(_nombre)]
SortOrder = Annotated[int, Field(ge=0, le=10000)]
Weekday = Annotated[int, Field(ge=1, le=7)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Out(CamelModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# ── Tamaños de vehículo ──────────────────────────────────────────────────────


class VehicleSizeCreate(_Body):
    code: Code
    label: Label
    sort_order: SortOrder = 0


class VehicleSizeUpdate(_Body):
    label: Label | None = None
    sort_order: SortOrder | None = None


class VehicleSizeResponse(_Out):
    code: str
    label: str
    sort_order: int


# ── Puestos ──────────────────────────────────────────────────────────────────


class ResourceCreate(_Body):
    name: Label
    sort_order: SortOrder = 0


class ResourceUpdate(_Body):
    name: Label | None = None
    sort_order: SortOrder | None = None


class ResourceResponse(_Out):
    name: str
    sort_order: int


# ── Franjas de atención ──────────────────────────────────────────────────────


class BusinessHoursCreate(_Body):
    weekday: Weekday
    opens_at: time
    closes_at: time

    @model_validator(mode="after")
    def _cierra_despues(self) -> "BusinessHoursCreate":
        if self.closes_at <= self.opens_at:
            raise ValueError("closes_at must be after opens_at")
        return self


class BusinessHoursUpdate(_Body):
    weekday: Weekday | None = None
    opens_at: time | None = None
    closes_at: time | None = None


class BusinessHoursResponse(_Out):
    weekday: int
    opens_at: time
    closes_at: time


# ── Medios de pago ───────────────────────────────────────────────────────────

CommissionBps = Annotated[int, Field(ge=0, le=10000)]
Account = Annotated[str, BeforeValidator(_sin_nul), Field(max_length=60), AfterValidator(_nombre)]


class PaymentMethodCreate(_Body):
    code: Code
    label: Label
    commission_bps: CommissionBps = 0
    settlement_account: Account | None = None
    for_income: bool = True
    for_expense: bool = False


class PaymentMethodUpdate(_Body):
    """`settlement_account: null` explícito borra la cuenta; ausente no la toca."""

    label: Label | None = None
    commission_bps: CommissionBps | None = None
    settlement_account: Account | None = None
    for_income: bool | None = None
    for_expense: bool | None = None


class PaymentMethodResponse(_Out):
    code: str
    label: str
    commission_bps: int
    settlement_account: str | None
    for_income: bool
    for_expense: bool
