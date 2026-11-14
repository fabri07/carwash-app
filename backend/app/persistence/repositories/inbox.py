"""Consultas de la bandeja del dueño — FASE-5-CONTRATO §4.1 (PR 5.3).

Listas paginadas con su total (ADR-0006) y lecturas por lote para armar cada fila sin N+1
(clientes, vehículos, servicios y señas de una página de turnos). Todo filtra por `tenant_id`
(tercera red, además de RLS).
"""

import uuid
from collections.abc import Collection
from datetime import datetime

from sqlalchemy import ColumnElement, func, select

from app.domain.enums import BookingStatus, DepositStatus, PaymentKind
from app.persistence.models.agenda import Booking, BookingItem, CustomerIncidentMark
from app.persistence.models.cancellation import Cancellation
from app.persistence.models.customer import Customer, Vehicle
from app.persistence.models.money import Payment
from app.persistence.repositories.base import BaseRepository, Page


class InboxBookingRepository(BaseRepository[Booking]):
    model = Booking

    def _filters(
        self,
        tenant_id: uuid.UUID,
        statuses: Collection[BookingStatus] | None,
        starts_from: datetime | None,
        starts_before: datetime | None,
    ) -> list[ColumnElement[bool]]:
        conditions = self._scope(tenant_id, False)
        if statuses:
            conditions.append(Booking.status.in_(tuple(statuses)))
        if starts_from is not None:
            conditions.append(Booking.start_at >= starts_from)
        if starts_before is not None:
            conditions.append(Booking.start_at < starts_before)
        return conditions

    async def list_filtered(
        self,
        tenant_id: uuid.UUID,
        *,
        statuses: Collection[BookingStatus] | None,
        starts_from: datetime | None,
        starts_before: datetime | None,
        limit: int,
        offset: int,
    ) -> Page[Booking]:
        """Turnos vivos por estado y `[starts_from, starts_before)`, por inicio (luego `id`)."""
        conditions = self._filters(tenant_id, statuses, starts_from, starts_before)
        total = await self._session.scalar(
            select(func.count()).select_from(Booking).where(*conditions)
        )
        result = await self._session.scalars(
            select(Booking)
            .where(*conditions)
            .order_by(Booking.start_at, Booking.id)
            .limit(limit)
            .offset(offset)
        )
        return Page(items=list(result.all()), total=int(total or 0))

    async def count_upcoming(
        self, status: BookingStatus, now: datetime, tenant_id: uuid.UUID
    ) -> int:
        """Turnos vivos en `status` que todavía no empezaron (`start_at > now`)."""
        total = await self._session.scalar(
            select(func.count())
            .select_from(Booking)
            .where(Booking.status == status, Booking.start_at > now, *self._scope(tenant_id, False))
        )
        return int(total or 0)

    async def customers(
        self, ids: Collection[uuid.UUID], tenant_id: uuid.UUID
    ) -> dict[uuid.UUID, Customer]:
        """Clientes de los turnos, anulados incluidos (el turno sigue mostrando a quién era)."""
        if not ids:
            return {}
        result = await self._session.scalars(
            select(Customer).where(Customer.id.in_(tuple(ids)), Customer.tenant_id == tenant_id)
        )
        return {c.id: c for c in result.all()}

    async def vehicles(
        self, ids: Collection[uuid.UUID], tenant_id: uuid.UUID
    ) -> dict[uuid.UUID, Vehicle]:
        if not ids:
            return {}
        result = await self._session.scalars(
            select(Vehicle).where(Vehicle.id.in_(tuple(ids)), Vehicle.tenant_id == tenant_id)
        )
        return {v.id: v for v in result.all()}

    async def items(
        self, booking_ids: Collection[uuid.UUID], tenant_id: uuid.UUID
    ) -> dict[uuid.UUID, list[BookingItem]]:
        """Servicios vivos de cada turno, en el orden en que se cargaron."""
        found: dict[uuid.UUID, list[BookingItem]] = {}
        if not booking_ids:
            return found
        result = await self._session.scalars(
            select(BookingItem)
            .where(
                BookingItem.booking_id.in_(tuple(booking_ids)),
                BookingItem.tenant_id == tenant_id,
                BookingItem.voided_at.is_(None),
            )
            .order_by(BookingItem.booking_id, BookingItem.position)
        )
        for item in result.all():
            found.setdefault(item.booking_id, []).append(item)
        return found

    async def deposits_paid(
        self, booking_ids: Collection[uuid.UUID], tenant_id: uuid.UUID
    ) -> dict[uuid.UUID, int]:
        """Señas vivas cobradas por turno (como `deposits_paid`, en una sola consulta)."""
        if not booking_ids:
            return {}
        result = await self._session.execute(
            select(Payment.booking_id, func.sum(Payment.amount_cents))
            .where(
                Payment.booking_id.in_(tuple(booking_ids)),
                Payment.tenant_id == tenant_id,
                Payment.kind == PaymentKind.SENA,
                Payment.voided_at.is_(None),
            )
            .group_by(Payment.booking_id)
        )
        return {row[0]: int(row[1] or 0) for row in result.all() if row[0] is not None}


class InboxCancellationRepository(BaseRepository[Cancellation]):
    model = Cancellation

    def _open(
        self, tenant_id: uuid.UUID, statuses: Collection[DepositStatus]
    ) -> list[ColumnElement[bool]]:
        return [Cancellation.deposit_status.in_(tuple(statuses)), *self._scope(tenant_id, False)]

    async def list_open(
        self,
        statuses: Collection[DepositStatus],
        tenant_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
    ) -> Page[Cancellation]:
        """Casos de seña abiertos, el más viejo primero (el que más espera)."""
        conditions = self._open(tenant_id, statuses)
        total = await self._session.scalar(
            select(func.count()).select_from(Cancellation).where(*conditions)
        )
        result = await self._session.scalars(
            select(Cancellation)
            .where(*conditions)
            .order_by(Cancellation.requested_at, Cancellation.id)
            .limit(limit)
            .offset(offset)
        )
        return Page(items=list(result.all()), total=int(total or 0))

    async def count_open(self, statuses: Collection[DepositStatus], tenant_id: uuid.UUID) -> int:
        total = await self._session.scalar(
            select(func.count()).select_from(Cancellation).where(*self._open(tenant_id, statuses))
        )
        return int(total or 0)


class InboxIncidentMarkRepository(BaseRepository[CustomerIncidentMark]):
    model = CustomerIncidentMark

    async def list_for_customer(
        self, customer_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[CustomerIncidentMark]:
        """Marcas vivas del cliente, la más nueva primero."""
        result = await self._session.scalars(
            select(CustomerIncidentMark)
            .where(CustomerIncidentMark.customer_id == customer_id, *self._scope(tenant_id, False))
            .order_by(CustomerIncidentMark.created_at.desc(), CustomerIncidentMark.id.desc())
        )
        return list(result.all())
