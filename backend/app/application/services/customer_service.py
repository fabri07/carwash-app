"""Clientes y vehículos: resolución por clave normalizada — FASE-3-CONTRATO §1.2 y §4.

**[corregir]** R-C-004/005: el cliente es entidad de primera clase (nunca se reconstruye) y
hay **una** política de identidad telefónica: el E.164 móvil de `app.domain.phone`.

- Teléfono normalizable → get-or-create por `(tenant_id, phone_e164)`.
- Teléfono ambiguo o inválido → **no se adivina**: se crea un cliente nuevo sin
  `phone_e164`, con lo tipeado en `phone_raw`, y el resultado lo dice (`phone.ambiguous`)
  para que la UI pida confirmación. Duplicados así los reconcilia una persona.

La carrera (dos altas simultáneas del mismo teléfono o patente) la resuelve el único parcial:
el INSERT va en un SAVEPOINT (`_savepoint.py`) y, si choca, se relee la fila ganadora.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.application.services._base import ServiceBase, alive_unique
from app.domain.enums import Channel, PlateFormat
from app.domain.exceptions import GuardFailedError
from app.domain.phone import PhoneResult, normalize_phone_ar
from app.domain.plate import classify_plate, normalize_plate
from app.persistence.db._savepoint import SavepointConflictError, guarded_savepoint
from app.persistence.models.customer import Customer, CustomerVehicle, Vehicle
from app.persistence.repositories.catalog import VehicleSizeRepository
from app.persistence.repositories.customers import (
    CustomerRepository,
    CustomerVehicleRepository,
    VehicleRepository,
)

#: `CHECK (length(name) BETWEEN 1 AND 80)`.
MAX_NAME_LENGTH = 80
#: `CHECK (length(brand_model) <= 40)`.
MAX_BRAND_MODEL_LENGTH = 40


@dataclass(frozen=True, slots=True)
class CustomerResolution:
    customer: Customer
    created: bool
    phone: PhoneResult


@dataclass(frozen=True, slots=True)
class VehicleResolution:
    vehicle: Vehicle
    created: bool
    #: `None` si no hay patente; `OTRO` se acepta con advertencia (no bloquea la recepción).
    plate_format: PlateFormat | None


def _clean_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not 1 <= len(cleaned) <= MAX_NAME_LENGTH:
        raise GuardFailedError(f"customer name must have 1..{MAX_NAME_LENGTH} characters")
    return cleaned


class CustomerService(ServiceBase):
    async def resolve_by_phone(
        self,
        phone_raw: str | None,
        *,
        name: str,
        email: str | None = None,
        channel: Channel | None = None,
    ) -> CustomerResolution:
        """Devuelve el cliente del teléfono o lo crea. El canal de origen se fija una vez."""
        await self._enter()
        cleaned_name = _clean_name(name)
        phone = normalize_phone_ar(phone_raw)
        raw = (phone_raw or "").strip() or None
        repo = CustomerRepository(self._session)
        if phone.e164 is None:
            customer = Customer(
                tenant_id=self._tenant_id,
                name=cleaned_name,
                phone_raw=raw,
                email=email,
                acquisition_channel=channel,
            )
            self._session.add(customer)
            await self._session.flush()
            return CustomerResolution(customer, created=True, phone=phone)

        existing = await repo.find_by_phone(phone.e164, self._tenant_id)
        if existing is not None:
            return CustomerResolution(await self._touch(existing, channel), False, phone)
        customer = Customer(
            tenant_id=self._tenant_id,
            name=cleaned_name,
            phone_e164=phone.e164,
            phone_raw=raw,
            email=email,
            acquisition_channel=channel,
        )
        try:
            async with guarded_savepoint(self._session, alive_unique("customers", "phone_e164")):
                self._session.add(customer)
        except SavepointConflictError:
            # Otra transacción lo creó entre la búsqueda y el INSERT: gana la suya.
            winner = await repo.find_by_phone(phone.e164, self._tenant_id)
            if winner is None:  # pragma: no cover  # solo si la ganadora se anuló en el medio
                raise
            return CustomerResolution(await self._touch(winner, channel), False, phone)
        return CustomerResolution(customer, created=True, phone=phone)

    async def _touch(self, customer: Customer, channel: Channel | None) -> Customer:
        # Canal de ORIGEN: se fija una vez (**[corregir]** R-C-002 guardaba el último).
        if customer.acquisition_channel is None and channel is not None:
            customer.acquisition_channel = channel
            await self._session.flush()
        return customer


class VehicleService(ServiceBase):
    async def resolve_by_plate(
        self,
        plate_raw: str | None,
        *,
        vehicle_size_id: uuid.UUID | None = None,
        brand_model: str | None = None,
        color: str | None = None,
    ) -> VehicleResolution:
        """Devuelve el vehículo de la patente o lo crea.

        Sin patente (vacía o centinela `SIN000`) siempre crea uno nuevo: no hay clave con
        qué deduplicar. Una patente de más de 10 caracteres levanta `InvalidPlateError`.
        """
        await self._enter()
        normalized = normalize_plate(plate_raw)
        if brand_model is not None and len(brand_model) > MAX_BRAND_MODEL_LENGTH:
            raise GuardFailedError(f"brand/model must have at most {MAX_BRAND_MODEL_LENGTH} chars")
        if vehicle_size_id is not None:
            await self._require(VehicleSizeRepository(self._session), vehicle_size_id)
        plate_format = classify_plate(normalized) if normalized is not None else None
        repo = VehicleRepository(self._session)
        if normalized is not None:
            existing = await repo.find_by_plate(normalized, self._tenant_id)
            if existing is not None:
                return VehicleResolution(
                    await self._complete(existing, vehicle_size_id), False, plate_format
                )
        vehicle = Vehicle(
            tenant_id=self._tenant_id,
            plate=(plate_raw or "").strip() if normalized is not None else None,
            plate_normalized=normalized,
            plate_format=plate_format,
            vehicle_size_id=vehicle_size_id,
            brand_model=brand_model,
            color=color,
        )
        try:
            async with guarded_savepoint(
                self._session, alive_unique("vehicles", "plate_normalized")
            ):
                self._session.add(vehicle)
        except SavepointConflictError:
            assert normalized is not None  # sin patente no hay único con qué chocar
            winner = await repo.find_by_plate(normalized, self._tenant_id)
            if winner is None:  # pragma: no cover  # solo si la ganadora se anuló en el medio
                raise
            return VehicleResolution(
                await self._complete(winner, vehicle_size_id), False, plate_format
            )
        return VehicleResolution(vehicle, created=True, plate_format=plate_format)

    async def _complete(self, vehicle: Vehicle, vehicle_size_id: uuid.UUID | None) -> Vehicle:
        # El tamaño habitual se completa si faltaba; no se pisa (el del trabajo va en el job).
        if vehicle.vehicle_size_id is None and vehicle_size_id is not None:
            vehicle.vehicle_size_id = vehicle_size_id
            await self._session.flush()
        return vehicle

    async def link_customer(
        self,
        customer_id: uuid.UUID,
        vehicle_id: uuid.UUID,
        *,
        is_primary: bool = False,
        valid_from: datetime | None = None,
    ) -> CustomerVehicle:
        """Vínculo vigente cliente ↔ auto (R-C-010). Idempotente: si ya existe, lo devuelve.

        Se permiten N clientes vigentes sobre el mismo auto (familia, empresa).
        """
        await self._enter()
        await self._require(CustomerRepository(self._session), customer_id)
        await self._require(VehicleRepository(self._session), vehicle_id)
        repo = CustomerVehicleRepository(self._session)
        existing = await repo.find_active_link(customer_id, vehicle_id, self._tenant_id)
        if existing is not None:
            return existing
        link = CustomerVehicle(
            tenant_id=self._tenant_id,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            is_primary=is_primary,
        )
        if valid_from is not None:
            link.valid_from = valid_from
        try:
            async with guarded_savepoint(
                self._session, alive_unique("customer_vehicles", "customer_id", "vehicle_id")
            ):
                self._session.add(link)
        except SavepointConflictError:
            winner = await repo.find_active_link(customer_id, vehicle_id, self._tenant_id)
            if winner is None:  # pragma: no cover  # solo si la ganadora se cerró en el medio
                raise
            return winner
        return link
