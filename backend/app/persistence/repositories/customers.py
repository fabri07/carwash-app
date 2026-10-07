"""Repositorios de clientes y vehículos — FASE-3-CONTRATO §1.2."""

import uuid

from sqlalchemy import select

from app.persistence.models.customer import Customer, CustomerVehicle, Vehicle
from app.persistence.repositories.base import BaseRepository, one_or_none


class CustomerRepository(BaseRepository[Customer]):
    model = Customer

    async def find_by_phone(self, phone_e164: str, tenant_id: uuid.UUID) -> Customer | None:
        """El cliente vivo con ese E.164 (único vivo parcial)."""
        return await one_or_none(
            self._session,
            select(Customer).where(
                Customer.phone_e164 == phone_e164, *self._scope(tenant_id, False)
            ),
        )


class VehicleRepository(BaseRepository[Vehicle]):
    model = Vehicle

    async def find_by_plate(self, plate_normalized: str, tenant_id: uuid.UUID) -> Vehicle | None:
        """El vehículo vivo con esa patente normalizada (único vivo parcial)."""
        return await one_or_none(
            self._session,
            select(Vehicle).where(
                Vehicle.plate_normalized == plate_normalized, *self._scope(tenant_id, False)
            ),
        )


class CustomerVehicleRepository(BaseRepository[CustomerVehicle]):
    model = CustomerVehicle

    async def find_active_link(
        self, customer_id: uuid.UUID, vehicle_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> CustomerVehicle | None:
        """El vínculo vigente (`valid_to IS NULL`) entre el cliente y el auto."""
        return await one_or_none(
            self._session,
            select(CustomerVehicle).where(
                CustomerVehicle.customer_id == customer_id,
                CustomerVehicle.vehicle_id == vehicle_id,
                CustomerVehicle.valid_to.is_(None),
                *self._scope(tenant_id, False),
            ),
        )
