"""Catálogo del lavadero — FASE-3-CONTRATO §1.1 y §4.

Lo central es `check_price_coherence`: la regla modalidad ↔ precio cruza `services` y
`service_prices`, así que no entra en un CHECK y la valida este servicio:

- `PRECIO_FIJO` ⇒ precio presente.
- `A_COTIZAR` ⇒ sin precio y seña 0 (D-006; sin precio no hay seña, R-C-032).

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


def check_price_coherence(mode: PricingMode, price_cents: int | None, deposit_bps: int) -> None:
    """Coherencia modalidad ↔ precio (§1.1). Levanta `CatalogIncoherentError`."""
    if not 0 <= deposit_bps <= BPS_DENOMINATOR:
        raise InvalidAmountError(f"deposit basis points must be in 0..{BPS_DENOMINATOR}")
    if price_cents is not None and price_cents <= 0:
        raise InvalidAmountError(f"price must be > 0: {price_cents}")
    if mode == PricingMode.PRECIO_FIJO and price_cents is None:
        raise CatalogIncoherentError("a fixed-price service needs a price")
    if mode == PricingMode.A_COTIZAR and price_cents is not None:
        raise CatalogIncoherentError("a quoted service has no catalog price")
    if mode == PricingMode.A_COTIZAR and deposit_bps != 0:
        raise CatalogIncoherentError("a quoted service has no deposit")


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
        price_cents: int | None,
        duration_min: int,
        deposit_bps: int = 0,
    ) -> ServicePrice:
        """Alta o actualización del precio servicio × tamaño, validando la coherencia."""
        await self._enter()
        service = await self._lock(ServiceRepository(self._session), service_id)
        await self._require(VehicleSizeRepository(self._session), vehicle_size_id)
        check_price_coherence(service.pricing_mode, price_cents, deposit_bps)
        if duration_min <= 0:
            raise InvalidAmountError(f"duration must be > 0: {duration_min}")
        repo = ServicePriceRepository(self._session)
        price = await repo.find_for(service_id, vehicle_size_id, self._tenant_id)
        if price is not None:
            price.price_cents = price_cents
            price.duration_min = duration_min
            price.deposit_bps = deposit_bps
            await self._session.flush()
            return price
        return await self._insert_unique(
            ServicePrice(
                tenant_id=self._tenant_id,
                service_id=service_id,
                vehicle_size_id=vehicle_size_id,
                price_cents=price_cents,
                duration_min=duration_min,
                deposit_bps=deposit_bps,
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
            check_price_coherence(mode, price.price_cents, price.deposit_bps)
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
            check_price_coherence(service.pricing_mode, price.price_cents, price.deposit_bps)

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
