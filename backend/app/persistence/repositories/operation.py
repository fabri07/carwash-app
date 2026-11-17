"""Lecturas de la agenda del día y la operación — FASE-6-CONTRATO §3.1 (PR 6.1).

Por lote, sin N+1: los turnos y los jobs de un día, sus servicios y sus pagos. Todo filtra por
`tenant_id` (tercera red, además de RLS) y por filas vivas.
"""

import uuid
from collections.abc import Collection
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import BookingStatus
from app.persistence.models.agenda import Booking
from app.persistence.models.catalog import Resource
from app.persistence.models.job import Job, JobItem
from app.persistence.models.money import Payment


class DayRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resources(self, tenant_id: uuid.UUID) -> list[Resource]:
        """Los puestos vivos, en el orden que el negocio les dio (una columna por puesto, D6-1)."""
        result = await self._session.scalars(
            select(Resource)
            .where(Resource.tenant_id == tenant_id, Resource.voided_at.is_(None))
            .order_by(Resource.sort_order, Resource.name, Resource.id)
        )
        return list(result.all())

    async def jobs_of_day(
        self, tenant_id: uuid.UUID, starts: datetime, ends: datetime, *, limit: int
    ) -> list[Job]:
        """Jobs vivos del día `[starts, ends)`: el del turno cae en la hora del turno
        (`scheduled_at`); el que no tuvo turno (walk-in o carga retrospectiva), en la de su
        llegada. Una carga retrospectiva queda así en **su** fecha, no en la de hoy."""
        result = await self._session.scalars(
            select(Job)
            .where(
                Job.tenant_id == tenant_id,
                Job.voided_at.is_(None),
                or_(
                    and_(Job.scheduled_at >= starts, Job.scheduled_at < ends),
                    and_(
                        Job.scheduled_at.is_(None),
                        Job.arrived_at >= starts,
                        Job.arrived_at < ends,
                    ),
                ),
            )
            .order_by(func.coalesce(Job.scheduled_at, Job.arrived_at), Job.id)
            .limit(limit)
        )
        return list(result.all())

    async def bookings_of_day(
        self,
        tenant_id: uuid.UUID,
        starts: datetime,
        ends: datetime,
        statuses: Collection[BookingStatus],
        *,
        limit: int,
    ) -> list[Booking]:
        result = await self._session.scalars(
            select(Booking)
            .where(
                Booking.tenant_id == tenant_id,
                Booking.voided_at.is_(None),
                Booking.status.in_(tuple(statuses)),
                Booking.start_at >= starts,
                Booking.start_at < ends,
            )
            .order_by(Booking.start_at, Booking.id)
            .limit(limit)
        )
        return list(result.all())

    async def booking_ids_with_job(
        self, tenant_id: uuid.UUID, booking_ids: Collection[uuid.UUID]
    ) -> set[uuid.UUID]:
        """De esos turnos, los que ya tienen un job vivo (se muestran como job, no como turno)."""
        if not booking_ids:
            return set()
        result = await self._session.scalars(
            select(Job.booking_id).where(
                Job.tenant_id == tenant_id,
                Job.voided_at.is_(None),
                Job.booking_id.in_(tuple(booking_ids)),
            )
        )
        return {b for b in result.all() if b is not None}

    async def job_items(
        self, tenant_id: uuid.UUID, job_ids: Collection[uuid.UUID]
    ) -> dict[uuid.UUID, list[JobItem]]:
        found: dict[uuid.UUID, list[JobItem]] = {}
        if not job_ids:
            return found
        result = await self._session.scalars(
            select(JobItem)
            .where(
                JobItem.tenant_id == tenant_id,
                JobItem.voided_at.is_(None),
                JobItem.job_id.in_(tuple(job_ids)),
            )
            .order_by(JobItem.job_id, JobItem.position)
        )
        for item in result.all():
            found.setdefault(item.job_id, []).append(item)
        return found

    async def job_payments(
        self, tenant_id: uuid.UUID, job_ids: Collection[uuid.UUID]
    ) -> dict[uuid.UUID, list[Payment]]:
        """Todos los pagos de cada job, anulados incluidos (el saldo descarta los anulados)."""
        found: dict[uuid.UUID, list[Payment]] = {}
        if not job_ids:
            return found
        result = await self._session.scalars(
            select(Payment)
            .where(Payment.tenant_id == tenant_id, Payment.job_id.in_(tuple(job_ids)))
            .order_by(Payment.job_id, Payment.occurred_at, Payment.id)
        )
        for payment in result.all():
            if payment.job_id is not None:
                found.setdefault(payment.job_id, []).append(payment)
        return found
