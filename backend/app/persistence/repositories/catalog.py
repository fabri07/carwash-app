"""Repositorios del catálogo — FASE-3-CONTRATO §1.1.

Todas las firmas llevan `tenant_id` obligatorio: es la tercera red después de RLS y los
tests (el filtro es gratis y no se saca).
"""

import uuid

from sqlalchemy import select

from app.persistence.models.catalog import (
    BusinessHours,
    PaymentMethod,
    Resource,
    Service,
    ServicePrice,
    VehicleSize,
)
from app.persistence.repositories.base import BaseRepository, one_or_none


class VehicleSizeRepository(BaseRepository[VehicleSize]):
    model = VehicleSize


class ServiceRepository(BaseRepository[Service]):
    model = Service


class ServicePriceRepository(BaseRepository[ServicePrice]):
    model = ServicePrice

    async def find_for(
        self, service_id: uuid.UUID, vehicle_size_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> ServicePrice | None:
        """El precio vivo de servicio × tamaño (a lo sumo uno: único vivo)."""
        return await one_or_none(
            self._session,
            select(ServicePrice).where(
                ServicePrice.service_id == service_id,
                ServicePrice.vehicle_size_id == vehicle_size_id,
                *self._scope(tenant_id, False),
            ),
        )

    async def list_for_service(
        self, service_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[ServicePrice]:
        result = await self._session.scalars(
            select(ServicePrice)
            .where(ServicePrice.service_id == service_id, *self._scope(tenant_id, False))
            .order_by(ServicePrice.id)
        )
        return list(result.all())


class ResourceRepository(BaseRepository[Resource]):
    model = Resource


class BusinessHoursRepository(BaseRepository[BusinessHours]):
    model = BusinessHours


class PaymentMethodRepository(BaseRepository[PaymentMethod]):
    model = PaymentMethod

    async def find_by_code(self, code: str, tenant_id: uuid.UUID) -> PaymentMethod | None:
        return await one_or_none(
            self._session,
            select(PaymentMethod).where(PaymentMethod.code == code, *self._scope(tenant_id, False)),
        )
