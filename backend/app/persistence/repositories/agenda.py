"""Repositorios de la agenda — FASE-3-CONTRATO §1.3."""

import uuid
from collections.abc import Collection
from datetime import datetime

from sqlalchemy import and_, exists, or_, select

from app.domain.booking_state import BLOCKING_BOOKING_STATUSES
from app.domain.enums import BookingStatus
from app.persistence.models.agenda import (
    Booking,
    BookingItem,
    CustomerIncidentMark,
    ScheduleBlock,
    ScheduleOpening,
)
from app.persistence.repositories.base import BaseRepository

#: Estados con hold (`hold_expires_at`): los únicos que vencen por reloj del turno (§2.1).
#: `PENDIENTE_COTIZACION` no: no ocupa el puesto y vence con su cotización (D4-15).
HOLD_STATUSES: tuple[BookingStatus, ...] = (BookingStatus.PENDIENTE_SENA,)


class BookingRepository(BaseRepository[Booking]):
    model = Booking

    async def lock_expired_holds(
        self, resource_id: uuid.UUID, now: datetime, tenant_id: uuid.UUID
    ) -> list[Booking]:
        """Holds vencidos del puesto, tomados con `FOR UPDATE SKIP LOCKED`.

        Un solo comparador en todo el sistema: `hold_expires_at <= now` (**[corregir]** C-16).

        `SKIP LOCKED` (F12): el orden por `id` no alcanza cuando cada transacción ya tiene
        tomado **su** turno (confirmar la seña, aceptar la cotización) y ese turno es un hold
        vencido que la otra quiere vencer: se esperarían en cruz (deadlock, `40P01`). El hold
        que otra transacción tiene tomado lo resuelve ella; el `EXCLUDE` sigue siendo la
        garantía del horario. Los propios no se saltean (el lock es de esta transacción).
        """
        result = await self._session.scalars(
            select(Booking)
            .where(
                Booking.resource_id == resource_id,
                Booking.status.in_(HOLD_STATUSES),
                Booking.hold_expires_at <= now,
                *self._scope(tenant_id, False),
            )
            .order_by(Booking.id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return list(result.all())

    async def slot_taken(self, booking: Booking, tenant_id: uuid.UUID) -> bool:
        """¿Otro turno vivo que bloquea ocupa parte del intervalo de `booking` en su puesto?

        La misma condición que el `EXCLUDE` (`[start, end)`, estados que bloquean, vivos).
        """
        other = exists().where(
            Booking.resource_id == booking.resource_id,
            Booking.id != booking.id,
            Booking.status.in_(BLOCKING_BOOKING_STATUSES),
            Booking.start_at < booking.end_at,
            Booking.end_at > booking.start_at,
            *self._scope(tenant_id, False),
        )
        return bool(await self._session.scalar(select(other)))

    async def list_occupying(
        self, starts_at: datetime, ends_at: datetime, now: datetime, tenant_id: uuid.UUID
    ) -> list[Booking]:
        """Lo que ocupa un puesto para el motor de disponibilidad (FASE-5-CONTRATO §5).

        Turnos vivos que pisan `[starts_at, ends_at)` en `CONFIRMADO`, `RECIBIDO` o
        `PENDIENTE_SEÑA` **con el hold vigente** (`hold_expires_at > now`, el complemento exacto
        de `hold_expired`): un hold vencido no ocupa aunque `expire_holds` no lo haya pasado a
        `VENCIDO` todavía.
        """
        result = await self._session.scalars(
            select(Booking)
            .where(
                Booking.start_at < ends_at,
                Booking.end_at > starts_at,
                or_(
                    Booking.status.in_((BookingStatus.CONFIRMADO, BookingStatus.RECIBIDO)),
                    and_(
                        Booking.status.in_(HOLD_STATUSES),
                        Booking.hold_expires_at > now,
                    ),
                ),
                *self._scope(tenant_id, False),
            )
            .order_by(Booking.start_at, Booking.id)
        )
        return list(result.all())

    async def list_with_status_overlapping(
        self,
        starts_at: datetime,
        ends_at: datetime,
        statuses: Collection[BookingStatus],
        tenant_id: uuid.UUID,
        *,
        resource_id: uuid.UUID | None = None,
    ) -> list[Booking]:
        """Turnos vivos en `statuses` que pisan `[starts_at, ends_at)`; de un puesto o de todos.

        Lo usa el alta de un bloqueo para avisar qué turnos quedan adentro (no los cancela).
        """
        conditions = [
            Booking.start_at < ends_at,
            Booking.end_at > starts_at,
            Booking.status.in_(tuple(statuses)),
            *self._scope(tenant_id, False),
        ]
        if resource_id is not None:
            conditions.append(Booking.resource_id == resource_id)
        result = await self._session.scalars(
            select(Booking).where(*conditions).order_by(Booking.start_at, Booking.id)
        )
        return list(result.all())

    async def list_for_customer_with_status_since(
        self,
        customer_id: uuid.UUID,
        statuses: Collection[BookingStatus],
        since: datetime,
        tenant_id: uuid.UUID,
    ) -> list[Booking]:
        """Turnos vivos del cliente en `statuses` con `start_at >= since` (incidencias, D-014)."""
        result = await self._session.scalars(
            select(Booking)
            .where(
                Booking.customer_id == customer_id,
                Booking.status.in_(tuple(statuses)),
                Booking.start_at >= since,
                *self._scope(tenant_id, False),
            )
            .order_by(Booking.start_at, Booking.id)
        )
        return list(result.all())


class BookingItemRepository(BaseRepository[BookingItem]):
    model = BookingItem

    async def list_for_booking(
        self, booking_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[BookingItem]:
        """Los servicios vivos del turno, en el orden en que se cargaron (adenda C1)."""
        result = await self._session.scalars(
            select(BookingItem)
            .where(BookingItem.booking_id == booking_id, *self._scope(tenant_id, False))
            .order_by(BookingItem.position)
        )
        return list(result.all())


class ScheduleBlockRepository(BaseRepository[ScheduleBlock]):
    model = ScheduleBlock

    async def list_overlapping(
        self, starts_at: datetime, ends_at: datetime | None, tenant_id: uuid.UUID
    ) -> list[ScheduleBlock]:
        """Bloqueos vivos que pisan `[starts_at, ends_at)` (`ends_at=None`: de ahí en adelante)."""
        conditions = [ScheduleBlock.ends_at > starts_at, *self._scope(tenant_id, False)]
        if ends_at is not None:
            conditions.append(ScheduleBlock.starts_at < ends_at)
        result = await self._session.scalars(
            select(ScheduleBlock)
            .where(*conditions)
            .order_by(ScheduleBlock.starts_at, ScheduleBlock.id)
        )
        return list(result.all())


class ScheduleOpeningRepository(BaseRepository[ScheduleOpening]):
    model = ScheduleOpening

    async def list_overlapping(
        self, starts_at: datetime, ends_at: datetime | None, tenant_id: uuid.UUID
    ) -> list[ScheduleOpening]:
        """Aperturas vivas que pisan `[starts_at, ends_at)` (`ends_at=None`: en adelante)."""
        conditions = [ScheduleOpening.ends_at > starts_at, *self._scope(tenant_id, False)]
        if ends_at is not None:
            conditions.append(ScheduleOpening.starts_at < ends_at)
        result = await self._session.scalars(
            select(ScheduleOpening)
            .where(*conditions)
            .order_by(ScheduleOpening.starts_at, ScheduleOpening.id)
        )
        return list(result.all())


class CustomerIncidentMarkRepository(BaseRepository[CustomerIncidentMark]):
    model = CustomerIncidentMark

    async def list_for_customer_since(
        self, customer_id: uuid.UUID, since: datetime, tenant_id: uuid.UUID
    ) -> list[CustomerIncidentMark]:
        """Marcas vivas del cliente creadas desde `since` (D-014: las anuladas no cuentan)."""
        result = await self._session.scalars(
            select(CustomerIncidentMark)
            .where(
                CustomerIncidentMark.customer_id == customer_id,
                CustomerIncidentMark.created_at >= since,
                *self._scope(tenant_id, False),
            )
            .order_by(CustomerIncidentMark.created_at, CustomerIncidentMark.id)
        )
        return list(result.all())
