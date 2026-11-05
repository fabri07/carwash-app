"""Catálogo del lavadero — FASE-3-CONTRATO §1.1 y §4.

Lo central es `check_price`. Desde la F4 (D4-6) **todo servicio tiene precio base por
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
from typing import Any

from sqlalchemy import ColumnElement, func, select

from app.application.services._base import ServiceBase, alive_unique
from app.application.services.errors import (
    AlreadyExistsError,
    CatalogIncoherentError,
    InUseError,
    NotFoundError,
)
from app.domain.booking_state import BLOCKING_BOOKING_STATUSES
from app.domain.enums import PricingMode
from app.domain.exceptions import InvalidAmountError
from app.domain.money import BPS_DENOMINATOR
from app.domain.void import VoidReason
from app.persistence.db._savepoint import (
    SavepointConflictError,
    guarded_savepoint,
)
from app.persistence.db.base import TenantScopedModel
from app.persistence.models.agenda import Booking
from app.persistence.models.catalog import (
    BusinessHours,
    PaymentMethod,
    Resource,
    Service,
    ServicePrice,
    VehicleSize,
)
from app.persistence.repositories.base import BaseRepository, Page
from app.persistence.repositories.catalog import (
    BusinessHoursRepository,
    PaymentMethodRepository,
    ResourceRepository,
    ServicePriceRepository,
    ServiceRepository,
    VehicleSizeRepository,
)


def _check_hours(weekday: int, opens_at: time, closes_at: time) -> None:
    if not 1 <= weekday <= 7:  # ISO: 1 = lunes … 7 = domingo
        raise InvalidAmountError(f"weekday must be ISO 1..7: {weekday}")
    if closes_at <= opens_at:
        raise InvalidAmountError("a business-hours range must close after it opens")


def _check_commission(commission_bps: int) -> None:
    if not 0 <= commission_bps <= BPS_DENOMINATOR:
        raise InvalidAmountError(f"commission basis points must be in 0..{BPS_DENOMINATOR}")


def check_price(price_cents: int | None) -> None:
    """Todo precio de catálogo es un entero > 0, sea cual sea la modalidad (D4-6).

    Levanta `CatalogIncoherentError` si falta y `InvalidAmountError` si no es positivo.
    """
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
        await self._lock(ServiceRepository(self._session), service_id)
        await self._require(VehicleSizeRepository(self._session), vehicle_size_id)
        check_price(price_cents)
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
            check_price(price.price_cents)
        service.pricing_mode = mode
        await self._session.flush()
        return service

    async def check_service(self, service_id: uuid.UUID) -> None:
        """Revalida todos los precios vivos del servicio contra su modalidad."""
        await self._enter()
        await self._require(ServiceRepository(self._session), service_id)
        prices = await ServicePriceRepository(self._session).list_for_service(
            service_id, self._tenant_id
        )
        for price in prices:
            check_price(price.price_cents)

    async def create_resource(self, name: str, sort_order: int = 0) -> Resource:
        await self._enter()
        return await self._insert_unique(
            Resource(tenant_id=self._tenant_id, name=name, sort_order=sort_order), "name"
        )

    async def add_business_hours(
        self, weekday: int, opens_at: time, closes_at: time
    ) -> BusinessHours:
        await self._enter()
        _check_hours(weekday, opens_at, closes_at)
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
        _check_commission(commission_bps)
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

    # ── ABM de la configuración (F4.2): listar, leer, editar y anular ─────────────────────────
    # El código de un tamaño o medio de pago es su identidad: no se edita (se anula y se crea otro).

    async def _list[M: TenantScopedModel](
        self,
        repo: BaseRepository[M],
        *,
        limit: int,
        offset: int,
        order_by: tuple[ColumnElement[Any], ...] | None = None,
    ) -> Page[M]:
        await self._enter()
        return await repo.list_by_tenant(
            self._tenant_id, limit=limit, offset=offset, order_by=order_by
        )

    async def _get[M: TenantScopedModel](self, repo: BaseRepository[M], id: uuid.UUID) -> M:
        await self._enter()
        return await self._require(repo, id)

    async def _void[M: TenantScopedModel](self, repo: BaseRepository[M], id: uuid.UUID) -> None:
        await self._enter()
        if await repo.void(id, self._tenant_id, VoidReason.PEDIDO_DEL_USUARIO) is None:
            raise NotFoundError(repo.model.__tablename__, id)

    async def list_vehicle_sizes(self, *, limit: int, offset: int) -> Page[VehicleSize]:
        return await self._list(
            VehicleSizeRepository(self._session),
            limit=limit,
            offset=offset,
            order_by=(VehicleSize.sort_order.asc(), VehicleSize.label.asc()),
        )

    async def get_vehicle_size(self, id: uuid.UUID) -> VehicleSize:
        return await self._get(VehicleSizeRepository(self._session), id)

    async def update_vehicle_size(
        self, id: uuid.UUID, *, label: str | None = None, sort_order: int | None = None
    ) -> VehicleSize:
        await self._enter()
        size = await self._lock(VehicleSizeRepository(self._session), id)
        if label is not None:
            size.label = label
        if sort_order is not None:
            size.sort_order = sort_order
        await self._session.flush()
        return size

    async def void_vehicle_size(self, id: uuid.UUID) -> None:
        """No se anula un tamaño con precios vivos: sin él esos servicios quedarían sin precio."""
        await self._enter()
        size = await self._require(VehicleSizeRepository(self._session), id)
        prices = await self._session.scalar(
            select(func.count())
            .select_from(ServicePrice)
            .where(
                ServicePrice.tenant_id == self._tenant_id,
                ServicePrice.vehicle_size_id == size.id,
                ServicePrice.voided_at.is_(None),
            )
        )
        if prices:
            raise InUseError("the vehicle size still has prices: remove them first")
        await self._void(VehicleSizeRepository(self._session), id)

    async def list_resources(self, *, limit: int, offset: int) -> Page[Resource]:
        return await self._list(
            ResourceRepository(self._session),
            limit=limit,
            offset=offset,
            order_by=(Resource.sort_order.asc(), Resource.name.asc()),
        )

    async def get_resource(self, id: uuid.UUID) -> Resource:
        return await self._get(ResourceRepository(self._session), id)

    async def update_resource(
        self, id: uuid.UUID, *, name: str | None = None, sort_order: int | None = None
    ) -> Resource:
        await self._enter()
        resource = await self._lock(ResourceRepository(self._session), id)
        try:
            async with guarded_savepoint(self._session, alive_unique("resources", "name")):
                if name is not None:
                    resource.name = name
                if sort_order is not None:
                    resource.sort_order = sort_order
        except SavepointConflictError as exc:
            raise AlreadyExistsError("resources: ('name',) already taken") from exc
        return resource

    async def void_resource(self, id: uuid.UUID) -> None:
        """No se anula un puesto con turnos que lo ocupan hoy o más adelante."""
        await self._enter()
        resource = await self._require(ResourceRepository(self._session), id)
        busy = await self._session.scalar(
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.tenant_id == self._tenant_id,
                Booking.resource_id == resource.id,
                Booking.voided_at.is_(None),
                Booking.status.in_(BLOCKING_BOOKING_STATUSES),
                Booking.end_at > func.now(),
            )
        )
        if busy:
            raise InUseError("the resource has upcoming bookings: move or cancel them first")
        await self._void(ResourceRepository(self._session), id)

    async def list_business_hours(self, *, limit: int, offset: int) -> Page[BusinessHours]:
        return await self._list(
            BusinessHoursRepository(self._session),
            limit=limit,
            offset=offset,
            order_by=(BusinessHours.weekday.asc(), BusinessHours.opens_at.asc()),
        )

    async def get_business_hours(self, id: uuid.UUID) -> BusinessHours:
        return await self._get(BusinessHoursRepository(self._session), id)

    async def update_business_hours(
        self,
        id: uuid.UUID,
        *,
        weekday: int | None = None,
        opens_at: time | None = None,
        closes_at: time | None = None,
    ) -> BusinessHours:
        await self._enter()
        hours = await self._lock(BusinessHoursRepository(self._session), id)
        new_weekday = hours.weekday if weekday is None else weekday
        new_opens = hours.opens_at if opens_at is None else opens_at
        new_closes = hours.closes_at if closes_at is None else closes_at
        _check_hours(new_weekday, new_opens, new_closes)
        hours.weekday, hours.opens_at, hours.closes_at = new_weekday, new_opens, new_closes
        await self._session.flush()
        return hours

    async def void_business_hours(self, id: uuid.UUID) -> None:
        await self._void(BusinessHoursRepository(self._session), id)

    async def list_payment_methods(self, *, limit: int, offset: int) -> Page[PaymentMethod]:
        return await self._list(PaymentMethodRepository(self._session), limit=limit, offset=offset)

    async def get_payment_method(self, id: uuid.UUID) -> PaymentMethod:
        return await self._get(PaymentMethodRepository(self._session), id)

    async def update_payment_method(
        self,
        id: uuid.UUID,
        *,
        label: str | None = None,
        commission_bps: int | None = None,
        settlement_account: str | None = None,
        clear_settlement_account: bool = False,
        for_income: bool | None = None,
        for_expense: bool | None = None,
    ) -> PaymentMethod:
        await self._enter()
        method = await self._lock(PaymentMethodRepository(self._session), id)
        if commission_bps is not None:
            _check_commission(commission_bps)
            method.commission_bps = commission_bps
        if label is not None:
            method.label = label
        if clear_settlement_account:
            method.settlement_account = None
        elif settlement_account is not None:
            method.settlement_account = settlement_account
        if for_income is not None:
            method.for_income = for_income
        if for_expense is not None:
            method.for_expense = for_expense
        await self._session.flush()
        return method

    async def void_payment_method(self, id: uuid.UUID) -> None:
        """No se anula el último medio de pago vivo: el negocio quedaría sin forma de cobrar."""
        await self._enter()
        method = await self._require(PaymentMethodRepository(self._session), id)
        others = await self._session.scalar(
            select(func.count())
            .select_from(PaymentMethod)
            .where(
                PaymentMethod.tenant_id == self._tenant_id,
                PaymentMethod.id != method.id,
                PaymentMethod.voided_at.is_(None),
            )
        )
        if not others:
            raise InUseError("it is the last payment method: add another before removing it")
        await self._void(PaymentMethodRepository(self._session), id)
