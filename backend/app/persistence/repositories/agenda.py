"""Repositorios de la agenda — FASE-3-CONTRATO §1.3."""

import uuid
from datetime import datetime

from sqlalchemy import exists, select

from app.domain.booking_state import BLOCKING_BOOKING_STATUSES
from app.domain.enums import BookingStatus
from app.persistence.models.agenda import Booking, ScheduleBlock
from app.persistence.repositories.base import BaseRepository

#: Estados con hold (`hold_expires_at`): los únicos que vencen (§2.1).
HOLD_STATUSES: tuple[BookingStatus, ...] = (
    BookingStatus.PENDIENTE_SENA,
    BookingStatus.PENDIENTE_COTIZACION,
)


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


class ScheduleBlockRepository(BaseRepository[ScheduleBlock]):
    model = ScheduleBlock
