"""Catálogo de vehículos — FASE-4-CONTRATO §3.4 y §4 (PR 4.4).

`extra="ignore"`: el `tenant_id` de un cuerpo se descarta, igual que `is_custom` (lo que se crea por
la API siempre es propio del negocio). Ocultar y mostrar va por `PATCH` con `hidden`; no hay baja.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, Field

from app.domain.vehicle_catalog import (
    COLOR_NAME_MAX,
    MAKE_NAME_MAX,
    MODEL_NAME_MAX,
    VehicleBodyType,
)
from app.schemas.auth import _sin_nul
from app.schemas.catalog import _Body, _Out
from app.schemas.common import CamelModel
from app.schemas.services import _buscable

MakeName = Annotated[
    str, BeforeValidator(_sin_nul), Field(max_length=MAKE_NAME_MAX), AfterValidator(_buscable)
]
ModelName = Annotated[
    str, BeforeValidator(_sin_nul), Field(max_length=MODEL_NAME_MAX), AfterValidator(_buscable)
]
ColorName = Annotated[
    str, BeforeValidator(_sin_nul), Field(max_length=COLOR_NAME_MAX), AfterValidator(_buscable)
]


class _CatalogOut(_Out):
    name: str
    #: La sumó el negocio (no viene de la base del mercado).
    is_custom: bool
    #: Oculta en los selectores; se vuelve a mostrar con `PATCH {"hidden": false}`.
    hidden_at: datetime | None


# ── Marcas ───────────────────────────────────────────────────────────────────


class VehicleMakeCreate(_Body):
    name: MakeName


class VehicleMakeUpdate(_Body):
    name: MakeName | None = None
    hidden: bool | None = None


class VehicleMakeResponse(_CatalogOut):
    pass


# ── Modelos ──────────────────────────────────────────────────────────────────


class VehicleModelCreate(_Body):
    make_id: uuid.UUID
    name: ModelName
    body_type: VehicleBodyType


class VehicleModelUpdate(_Body):
    """La marca no se cambia: un modelo mal ubicado se oculta y se crea en la marca correcta."""

    name: ModelName | None = None
    body_type: VehicleBodyType | None = None
    hidden: bool | None = None


class VehicleModelResponse(_CatalogOut):
    make_id: uuid.UUID
    make_name: str
    body_type: VehicleBodyType
    #: Tamaño del negocio que sugiere su carrocería (`body_type_sizes`, A10), o `null`.
    suggested_vehicle_size_id: uuid.UUID | None


# ── Colores ──────────────────────────────────────────────────────────────────


class VehicleColorCreate(_Body):
    name: ColorName


class VehicleColorUpdate(_Body):
    name: ColorName | None = None
    hidden: bool | None = None


class VehicleColorResponse(_CatalogOut):
    pass


# ── Carrocería → tamaño ──────────────────────────────────────────────────────


class BodyTypeSizeResponse(CamelModel):
    body_type: VehicleBodyType
    #: `null`: esa carrocería no sugiere tamaño.
    vehicle_size_id: uuid.UUID | None


class BodyTypeSizeInput(_Body):
    body_type: VehicleBodyType
    vehicle_size_id: uuid.UUID | None


class BodyTypeSizesPut(_Body):
    """El mapa entero: una carrocería que no viene (o viene con `null`) deja de sugerir tamaño."""

    items: Annotated[list[BodyTypeSizeInput], Field(max_length=len(VehicleBodyType))]
