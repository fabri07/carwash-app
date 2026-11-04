"""Catálogo del lavadero — FASE-3-CONTRATO §1.1 y §4.

Lo central es `check_price_coherence`. Desde la F4 (D4-6) **todo servicio tiene precio base por
tamaño**, en las dos modalidades: en `PRECIO_FIJO` es el precio; en `A_COTIZAR` es la referencia
"desde $X" y la cotización fija el final. Nada se crea sin precio. La seña ya no vive en el precio:
es global, del negocio (`tenant_settings`, D4-4).

**[corregir]** R-T-002: borrar el precio **no** convierte el servicio en "a cotizar"; cambiar
la modalidad es una operación explícita (`change_pricing_mode`) que revalida los precios.

Las altas (tamaño, servicio, puesto, franja, medio de pago) son mínimas: el ABM completo con
su UI es F4. Existen para que el seed de staging y los tests ejerciten el dominio por acá.
"""

import uuid
from datetime import time

from app.application.services._base import ServiceBase, alive_unique
from app.application.services.errors import AlreadyExistsError, CatalogIncoherentError
from app.domain.enums import PricingMode
from app.domain.exceptions import InvalidAmountError
from app.domain.money import BPS_DENOMINATOR
from app.persistence.db._savepoint import (
    SavepointConflictError,
    guarded_savepoint,
)
from app.persistence.db.base import TenantScopedModel
from app.persistence.models.catalog import (
    BusinessHours,
    PaymentMethod,
    Resource,
    Service,
    ServicePrice,
    VehicleSize,
)
from app.persistence.repositories.catalog import (
    PaymentMethodRepository,
    ServicePriceRepository,
    ServiceRepository,
    VehicleSizeRepository,
)


def check_price_coherence(mode: PricingMode, price_cents: int | None) -> None:
    """Todo precio de catálogo es un entero > 0, sea cual sea la modalidad (D4-6).

    Levanta `CatalogIncoherentError` si falta y `InvalidAmountError` si no es positivo. `mode` se
    conserva en la firma: la regla es la misma en las dos modalidades, pero es el punto único
    donde agregar una distinción si algún día aparece.
    """
    del mode
    if price_cents is None:
        raise CatalogIncoherentError("every service needs a base price per vehicle size")
    if price_cents <= 0:
        raise InvalidAmountError(f"price must be > 0: {price_cents}")


class CatalogService(ServiceBase):
    async def _insert_unique[M: TenantScopedModel](self, entity: M, *columns: str) -> M:
        table = entity.__tablename__
        try:
            async with guarded_savepoint(self._session, alive_unique(table, *columns)):
                self._session.add(entity)
        except SavepointConflictError as exc:
            raise AlreadyExistsError(f"{table}: {columns} already taken") from exc
        return entity

    async def create_vehicle_size(self, code: str, label: str, sort_order: int = 0) -> VehicleSize:
        await self._enter()
        return await self._insert_unique(
            VehicleSize(tenant_id=self._tenant_id, code=code, label=label, sort_order=sort_order),
            "code",
        )

    async def create_service(
        self, name: str, pricing_mode: PricingMode, notes: str | None = None
    ) -> Service:
        await self._enter()
        return await self._insert_unique(
            Service(tenant_id=self._tenant_id, name=name, pricing_mode=pricing_mode, notes=notes),
            "name",
        )

    async def set_price(
        self,
        service_id: uuid.UUID,
        vehicle_size_id: uuid.UUID,
        *,
        price_cents: int,
        duration_min: int,
    ) -> ServicePrice:
        """Alta o actualización del precio base servicio × tamaño (D4-6): siempre con precio."""
        await self._enter()
        service = await self._lock(ServiceRepository(self._session), service_id)
        await self._require(VehicleSizeRepository(self._session), vehicle_size_id)
        check_price_coherence(service.pricing_mode, price_cents)
        if duration_min <= 0:
            raise InvalidAmountError(f"duration must be > 0: {duration_min}")
        repo = ServicePriceRepository(self._session)
        price = await repo.find_for(service_id, vehicle_size_id, self._tenant_id)
        if price is not None:
            price.price_cents = price_cents
            price.duration_min = duration_min
            await self._session.flush()
            return price
        return await self._insert_unique(
            ServicePrice(
                tenant_id=self._tenant_id,
                service_id=service_id,
                vehicle_size_id=vehicle_size_id,
                price_cents=price_cents,
                duration_min=duration_min,
            ),
            "service_id",
            "vehicle_size_id",
        )

    async def change_pricing_mode(self, service_id: uuid.UUID, mode: PricingMode) -> Service:
        """Cambia la modalidad solo si **todos** los precios vivos quedan coherentes."""
        await self._enter()
        service = await self._lock(ServiceRepository(self._session), service_id)
        prices = await ServicePriceRepository(self._session).list_for_service(
            service_id, self._tenant_id
        )
        for price in prices:
            check_price_coherence(mode, price.price_cents)
        service.pricing_mode = mode
        await self._session.flush()
        return service

    async def check_service(self, service_id: uuid.UUID) -> None:
        """Revalida todos los precios vivos del servicio contra su modalidad."""
        await self._enter()
        service = await self._require(ServiceRepository(self._session), service_id)
        prices = await ServicePriceRepository(self._session).list_for_service(
            service_id, self._tenant_id
        )
        for price in prices:
            check_price_coherence(service.pricing_mode, price.price_cents)

    async def create_resource(self, name: str, sort_order: int = 0) -> Resource:
        await self._enter()
        return await self._insert_unique(
            Resource(tenant_id=self._tenant_id, name=name, sort_order=sort_order), "name"
        )

    async def add_business_hours(
        self, weekday: int, opens_at: time, closes_at: time
    ) -> BusinessHours:
        await self._enter()
        if not 1 <= weekday <= 7:  # ISO: 1 = lunes … 7 = domingo
            raise InvalidAmountError(f"weekday must be ISO 1..7: {weekday}")
        if closes_at <= opens_at:
            raise InvalidAmountError("a business-hours range must close after it opens")
        hours = BusinessHours(
            tenant_id=self._tenant_id, weekday=weekday, opens_at=opens_at, closes_at=closes_at
        )
        self._session.add(hours)
        await self._session.flush()
        return hours

    async def create_payment_method(
        self,
        code: str,
        label: str,
        *,
        commission_bps: int = 0,
        settlement_account: str | None = None,
        for_income: bool = True,
        for_expense: bool = False,
    ) -> PaymentMethod:
        await self._enter()
        if not 0 <= commission_bps <= BPS_DENOMINATOR:
            raise InvalidAmountError(f"commission basis points must be in 0..{BPS_DENOMINATOR}")
        return await self._insert_unique(
            PaymentMethod(
                tenant_id=self._tenant_id,
                code=code,
                label=label,
                commission_bps=commission_bps,
                settlement_account=settlement_account,
                for_income=for_income,
                for_expense=for_expense,
            ),
            "code",
        )

    async def find_payment_method(self, code: str) -> PaymentMethod | None:
        await self._enter()
        return await PaymentMethodRepository(self._session).find_by_code(code, self._tenant_id)
