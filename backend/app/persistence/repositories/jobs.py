"""Repositorios de la operación — FASE-3-CONTRATO §1.4.

`JobEventRepository` no hereda de `BaseRepository`: `job_events` no es `TenantScopedModel`
(sin `updated_at` ni anulación) y es append-only (X8). Solo inserta y lista; **no hay** método
que actualice ni borre, y la base además lo prohíbe (permisos + trigger).
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models.job import Job, JobEvent, JobInspection
from app.persistence.repositories.base import BaseRepository, Page, one_or_none


class JobRepository(BaseRepository[Job]):
    model = Job

    async def find_by_booking(self, booking_id: uuid.UUID, tenant_id: uuid.UUID) -> Job | None:
        """El job vivo del turno (a lo sumo uno: único vivo parcial, R-O-010)."""
        return await one_or_none(
            self._session,
            select(Job).where(Job.booking_id == booking_id, *self._scope(tenant_id, False)),
        )


class JobInspectionRepository(BaseRepository[JobInspection]):
    model = JobInspection

    async def find_by_job(self, job_id: uuid.UUID, tenant_id: uuid.UUID) -> JobInspection | None:
        return await one_or_none(
            self._session,
            select(JobInspection).where(
                JobInspection.job_id == job_id, *self._scope(tenant_id, False)
            ),
        )


class JobEventRepository:
    """Historia append-only de los jobs: insertar y leer, nada más."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: JobEvent) -> JobEvent:
        self._session.add(event)
        await self._session.flush()
        return event

    async def get_by_id(self, id: uuid.UUID, tenant_id: uuid.UUID) -> JobEvent | None:
        return await one_or_none(
            self._session,
            select(JobEvent).where(JobEvent.id == id, JobEvent.tenant_id == tenant_id),
        )

    async def get_by_key(self, idempotency_key: str, tenant_id: uuid.UUID) -> JobEvent | None:
        """El evento con esa clave de idempotencia (`UNIQUE (tenant_id, idempotency_key)`)."""
        return await one_or_none(
            self._session,
            select(JobEvent).where(
                JobEvent.idempotency_key == idempotency_key, JobEvent.tenant_id == tenant_id
            ),
        )

    async def list_for_job(self, job_id: uuid.UUID, tenant_id: uuid.UUID) -> list[JobEvent]:
        """La historia del job en orden de registro.

        Se ordena por `created_at` (reloj del server) y no por `occurred_at` (reloj del
        cliente): con la cola offline dos dispositivos con relojes distintos producen
        `occurred_at` "imposibles" (X10), y el orden que importa para derivar estado
        (p. ej. retención vigente) es el que la máquina aceptó.
        """
        result = await self._session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.tenant_id == tenant_id)
            .order_by(JobEvent.created_at, JobEvent.occurred_at, JobEvent.id)
        )
        return list(result.all())

    async def list_by_tenant(
        self, tenant_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> Page[JobEvent]:
        scope = JobEvent.tenant_id == tenant_id
        total = await one_or_none(
            self._session, select(func.count()).select_from(JobEvent).where(scope)
        )
        result = await self._session.scalars(
            select(JobEvent)
            .where(scope)
            .order_by(JobEvent.created_at.desc(), JobEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return Page(items=list(result.all()), total=int(total or 0))
