"""Repositorios de la agenda — FASE-3-CONTRATO §1.3."""

import uuid
from datetime import datetime

from sqlalchemy import select

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
        """Holds vencidos del puesto, tomados con `FOR UPDATE`.

        Un solo comparador en todo el sistema: `hold_expires_at <= now` (**[corregir]** C-16).
        Orden por `id`: dos transacciones que vencen holds del mismo puesto toman los locks
        en el mismo orden y no se bloquean en cruz (deadlock).
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
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list(result.all())


class ScheduleBlockRepository(BaseRepository[ScheduleBlock]):
    model = ScheduleBlock
