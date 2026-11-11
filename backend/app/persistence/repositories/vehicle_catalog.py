"""Repositorios del catálogo de vehículos — FASE-4-CONTRATO §3.4 (PR 4.4).

Sin métodos propios: las búsquedas y filtros los arma `VehicleCatalogService` (Y8) y el CRUD
genérico de `BaseRepository` ya lleva el `tenant_id` obligatorio.
"""

from app.persistence.models.vehicle_catalog import (
    BodyTypeSize,
    VehicleColor,
    VehicleMake,
    VehicleModel,
)
from app.persistence.repositories.base import BaseRepository


class VehicleMakeRepository(BaseRepository[VehicleMake]):
    model = VehicleMake


class VehicleModelRepository(BaseRepository[VehicleModel]):
    model = VehicleModel


class VehicleColorRepository(BaseRepository[VehicleColor]):
    model = VehicleColor


class BodyTypeSizeRepository(BaseRepository[BodyTypeSize]):
    model = BodyTypeSize
