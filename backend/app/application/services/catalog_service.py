"""Catálogo del lavadero — FASE-3-CONTRATO §1.1 y §4.

FASE-4-CONTRATO D4-6: **todo servicio tiene precio base por tamaño**, en las dos modalidades.
En `A_COTIZAR` es el "desde $X" y la cotización fija el final. La seña ya no es por precio:
es un % global en `tenant_settings` (D4-4).

**[corregir]** R-T-002: borrar el precio **no** convierte el servicio en "a cotizar"; cambiar
la modalidad es una operación explícita (`change_pricing_mode`).

ABM de tamaños, puestos, franjas y medios de pago (PR 4.2, `api/v1/catalog.py`): anular es la
única baja (ADR-0003). Un puesto con turnos que lo ocupan a futuro no se anula.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, time
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import InstrumentedAttribute

from app.application.services._base import ServiceBase, alive_unique
from app.application.services.errors import AlreadyExistsError, CatalogIncoherentError
from app.domain.booking_state import BLOCKING_BOOKING_STATUSES
from app.domain.enums import BookingStatus, PricingMode
from app.domain.exceptions import GuardFailedError, InvalidAmountError
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
from app.utils.datetime_utils import utcnow

#: Turnos que ocupan un puesto, o lo van a ocupar al aceptar la cotización.
_UPCOMING_STATUSES = BLOCKING_BOOKING_STATUSES | {BookingStatus.PENDIENTE_COTIZACION}


class ResourceInUseError(GuardFailedError):
    """El puesto tiene turnos que lo ocupan a futuro: anularlo los dejaría sin lugar."""


def check_price_coherence(price_cents: int | None) -> None:
    """D4-6: precio presente y > 0 en las dos modalidades. Sin precio → `CatalogIncoherentError`."""
    if price_cents is None:
        raise CatalogIncoherentError("every service needs a base price")
    if price_cents <= 0:
        raise InvalidAmountError(f"price must be > 0: {price_cents}")


def _check_hours(weekday: int, opens_at: time, closes_at: time) -> None:
    if not 1 <= weekday <= 7:  # ISO: 1 = lunes … 7 = domingo
        raise InvalidAmountError(f"weekday must be ISO 1..7: {weekday}")
    if closes_at <= opens_at:
        raise InvalidAmountError("a business-hours range must close after it opens")


def _check_commission(commission_bps: int) -> None:
    if not 0 <= commission_bps <= BPS_DENOMINATOR:
        raise InvalidAmountError(f"commission basis points must be in 0..{BPS_DENOMINATOR}")


class CatalogService(ServiceBase):
    async def _insert_unique[M: TenantScopedModel](self, entity: M, *columns: str) -> M:
        table = entity.__tablename__
        try:
            async with guarded_savepoint(self._session, alive_unique(table, *columns)):
                self._session.add(entity)
        except SavepointConflictError as exc:
            raise AlreadyExistsError(f"{table}: {columns} already taken") from exc
        return entity

    async def _list[M: TenantScopedModel](
        self,
        repo: BaseRepository[M],
        order_by: Sequence[ColumnElement[Any] | InstrumentedAttribute[Any]],
        *,
        limit: int,
        offset: int,
    ) -> Page[M]:
        await self._enter()
        return await repo.list_by_tenant(
            self._tenant_id, limit=limit, offset=offset, order_by=order_by
        )

    async def _get[M: TenantScopedModel](self, repo: BaseRepository[M], id: uuid.UUID) -> M:
        await self._enter()
        return await self._require(repo, id)

    async def _update[M: TenantScopedModel](
        self,
        repo: BaseRepository[M],
        id: uuid.UUID,
        changes: Mapping[str, object],
        *unique_columns: str,
    ) -> M:
        """Aplica solo los campos presentes (`None` = no se toca: ninguno es anulable acá
        salvo `settlement_account`, que se limpia con `""`)."""
        await self._enter()
        entity = await self._lock(repo, id)
        table = entity.__tablename__
        try:
            async with guarded_savepoint(
                self._session,
                alive_unique(table, *unique_columns) if unique_columns else lambda _: None,
            ):
                for column, value in changes.items():
                    if value is not None:
                        setattr(entity, column, value)
        except SavepointConflictError as exc:
            raise AlreadyExistsError(f"{table}: {unique_columns} already taken") from exc
        return entity

    async def _void[M: TenantScopedModel](self, repo: BaseRepository[M], id: uuid.UUID) -> M:
        await self._enter()
        entity = await self._lock(repo, id)
        entity.voided_at = utcnow()
        entity.void_reason = VoidReason.PEDIDO_DEL_USUARIO
        await self._session.flush()
        return entity

    # ── Tamaños ───────────────────────────────────────────────────────────────

    async def list_vehicle_sizes(self, *, limit: int, offset: int) -> Page[VehicleSize]:
        return await self._list(
            VehicleSizeRepository(self._session),
            [VehicleSize.sort_order, VehicleSize.label],
            limit=limit,
            offset=offset,
        )

    async def get_vehicle_size(self, id: uuid.UUID) -> VehicleSize:
        return await self._get(VehicleSizeRepository(self._session), id)

    async def update_vehicle_size(
        self,
        id: uuid.UUID,
        *,
        code: str | None = None,
        label: str | None = None,
        sort_order: int | None = None,
    ) -> VehicleSize:
        return await self._update(
            VehicleSizeRepository(self._session),
            id,
            {"code": code, "label": label, "sort_order": sort_order},
            "code",
        )

    async def void_vehicle_size(self, id: uuid.UUID) -> VehicleSize:
        """Los precios de ese tamaño quedan, sin uso: no se reserva con un tamaño anulado."""
        return await self._void(VehicleSizeRepository(self._session), id)

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
        """Alta o actualización del precio base servicio × tamaño (D4-6)."""
        await self._enter()
        await self._lock(ServiceRepository(self._session), service_id)
        await self._require(VehicleSizeRepository(self._session), vehicle_size_id)
        check_price_coherence(price_cents)
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
        """Cambia la modalidad. Los precios valen en las dos (D4-6): no hay que revalidarlos.
        Los turnos y jobs ya creados conservan su snapshot de `pricing_mode`."""
        await self._enter()
        service = await self._lock(ServiceRepository(self._session), service_id)
        service.pricing_mode = mode
        await self._session.flush()
        return service

    async def check_service(self, service_id: uuid.UUID) -> None:
        """Revalida que todos los precios vivos del servicio estén cargados y sean > 0 (D4-6)."""
        await self._enter()
        await self._require(ServiceRepository(self._session), service_id)
        prices = await ServicePriceRepository(self._session).list_for_service(
            service_id, self._tenant_id
        )
        for price in prices:
            check_price_coherence(price.price_cents)

    # ── Puestos ───────────────────────────────────────────────────────────────

    async def list_resources(self, *, limit: int, offset: int) -> Page[Resource]:
        return await self._list(
            ResourceRepository(self._session),
            [Resource.sort_order, Resource.name],
            limit=limit,
            offset=offset,
        )

    async def get_resource(self, id: uuid.UUID) -> Resource:
        return await self._get(ResourceRepository(self._session), id)

    async def update_resource(
        self, id: uuid.UUID, *, name: str | None = None, sort_order: int | None = None
    ) -> Resource:
        return await self._update(
            ResourceRepository(self._session),
            id,
            {"name": name, "sort_order": sort_order},
            "name",
        )

    async def void_resource(self, id: uuid.UUID, *, now: datetime) -> Resource:
        """`ResourceInUseError` si algún turno vivo lo ocupa, o lo va a ocupar al aceptar su
        cotización (D4-13), y todavía no terminó. Una vez anulado, ni el alta de un turno ni
        aceptar una cotización lo usan (`_require` excluye anulados)."""
        await self._enter()
        resource = await self._lock(ResourceRepository(self._session), id)
        busy = await self._session.scalar(
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.tenant_id == self._tenant_id,
                Booking.resource_id == id,
                Booking.status.in_(_UPCOMING_STATUSES),
                Booking.voided_at.is_(None),
                Booking.end_at > now,
            )
        )
        if busy:
            raise ResourceInUseError("the resource has upcoming bookings")
        resource.voided_at = utcnow()
        resource.void_reason = VoidReason.PEDIDO_DEL_USUARIO
        await self._session.flush()
        return resource

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

    # ── Franjas horarias ──────────────────────────────────────────────────────

    async def list_business_hours(self, *, limit: int, offset: int) -> Page[BusinessHours]:
        return await self._list(
            BusinessHoursRepository(self._session),
            [BusinessHours.weekday, BusinessHours.opens_at],
            limit=limit,
            offset=offset,
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
        """Valida la franja resultante (lo nuevo combinado con lo que ya estaba)."""
        await self._enter()
        hours = await self._lock(BusinessHoursRepository(self._session), id)
        new_weekday = weekday if weekday is not None else hours.weekday
        new_opens = opens_at if opens_at is not None else hours.opens_at
        new_closes = closes_at if closes_at is not None else hours.closes_at
        _check_hours(new_weekday, new_opens, new_closes)
        hours.weekday, hours.opens_at, hours.closes_at = new_weekday, new_opens, new_closes
        await self._session.flush()
        return hours

    async def void_business_hours(self, id: uuid.UUID) -> BusinessHours:
        return await self._void(BusinessHoursRepository(self._session), id)

    # ── Medios de pago ────────────────────────────────────────────────────────

    async def list_payment_methods(self, *, limit: int, offset: int) -> Page[PaymentMethod]:
        return await self._list(
            PaymentMethodRepository(self._session),
            [PaymentMethod.label],
            limit=limit,
            offset=offset,
        )

    async def get_payment_method(self, id: uuid.UUID) -> PaymentMethod:
        return await self._get(PaymentMethodRepository(self._session), id)

    async def update_payment_method(
        self,
        id: uuid.UUID,
        *,
        code: str | None = None,
        label: str | None = None,
        commission_bps: int | None = None,
        settlement_account: str | None = None,
        for_income: bool | None = None,
        for_expense: bool | None = None,
    ) -> PaymentMethod:
        if commission_bps is not None:
            _check_commission(commission_bps)
        method = await self._update(
            PaymentMethodRepository(self._session),
            id,
            {
                "code": code,
                "label": label,
                "commission_bps": commission_bps,
                "for_income": for_income,
                "for_expense": for_expense,
            },
            "code",
        )
        if settlement_account is not None:
            method.settlement_account = settlement_account.strip() or None
            await self._session.flush()
        return method

    async def void_payment_method(self, id: uuid.UUID) -> PaymentMethod:
        """Los cobros ya registrados lo siguen referenciando; no se ofrece para nuevos."""
        return await self._void(PaymentMethodRepository(self._session), id)
