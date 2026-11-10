"""Base de vehículos del mercado argentino — FASE-4-CONTRATO D4-8, Y7 y §3.4 (PR 4.4).

Lee `app/data/vehiculos_ar.json`: marcas, modelos (con su carrocería) y colores comunes. Son datos
públicos del rubro, sin PII (§8). La base **se copia a cada negocio** al registrarse
(`provision_tenant`) y la migración `0008` la copió a los que ya existían (Y7): después cada
negocio la edita, oculta o completa sin tocar a los demás.

`VehicleBodyType` es un enum nativo (`vehicle_body_type`) y no un dato porque lo usa el código:
la carrocería de un modelo sugiere el tamaño del negocio (`body_type_sizes`, A10). No vive en
`enums.py` porque aquel archivo es el vocabulario congelado de la F3 (`0003_dominio`); este tipo lo
crea la `0008`, que congela sus valores y un test los compara con los de acá.

Pura: sin base. La carga se hace una vez por proceso.
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "vehiculos_ar.json"

VEHICLE_BODY_TYPE_ENUM = "vehicle_body_type"

#: Largos máximos (CHECK `nombre_largo` y schemas).
MAKE_NAME_MAX = 40
MODEL_NAME_MAX = 60
COLOR_NAME_MAX = 40


class VehicleBodyType(StrEnum):
    """Carrocería. `RURAL` es el familiar/station wagon; `UTILITARIO`, el furgón chico
    (Kangoo, Partner); `FURGON`, el grande (Sprinter, Master)."""

    SEDAN = "SEDAN"
    HATCHBACK = "HATCHBACK"
    RURAL = "RURAL"
    SUV = "SUV"
    PICKUP = "PICKUP"
    UTILITARIO = "UTILITARIO"
    FURGON = "FURGON"
    MOTO = "MOTO"
    OTRO = "OTRO"


@dataclass(frozen=True)
class BaseVehicleModel:
    name: str
    body_type: VehicleBodyType


@dataclass(frozen=True)
class BaseVehicleMake:
    name: str
    models: tuple[BaseVehicleModel, ...]


@dataclass(frozen=True)
class VehicleBase:
    makes: tuple[BaseVehicleMake, ...]
    colors: tuple[str, ...]

    @property
    def model_count(self) -> int:
        return sum(len(make.models) for make in self.makes)


def parse_vehicle_base(data: dict[str, object]) -> VehicleBase:
    raw_makes = data["makes"]
    raw_colors = data["colors"]
    assert isinstance(raw_makes, list) and isinstance(raw_colors, list)
    return VehicleBase(
        makes=tuple(
            BaseVehicleMake(
                name=make["name"],
                models=tuple(
                    BaseVehicleModel(name=m["name"], body_type=VehicleBodyType(m["body_type"]))
                    for m in make["models"]
                ),
            )
            for make in raw_makes
        ),
        colors=tuple(str(c) for c in raw_colors),
    )


@cache
def load_vehicle_base() -> VehicleBase:
    """La base del archivo, cacheada: es igual para todos los negocios y de solo lectura."""
    return parse_vehicle_base(json.loads(DATA_FILE.read_text(encoding="utf-8")))
