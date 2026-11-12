"""Repositorio de cancelaciones — FASE-3-CONTRATO §1.4 y §2.4."""

import uuid
from collections.abc import Collection
from datetime import datetime

from sqlalchemy import select

from app.domain.enums import CancellationClassification
from app.persistence.models.agenda import Booking
from app.persistence.models.cancellation import Cancellation
from app.persistence.repositories.base import BaseRepository, one_or_none


class CancellationRepository(BaseRepository[Cancellation]):
    model = Cancellation

    async def find_by_booking(
        self, booking_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Cancellation | None:
        """La cancelación del turno, anulada o no: el único `(tenant_id, booking_id)` no es
        parcial (A8), así que una anulada igual ocupa el lugar."""
        return await one_or_none(
            self._session,
            select(Cancellation).where(
                Cancellation.booking_id == booking_id, Cancellation.tenant_id == tenant_id
            ),
        )

    async def list_for_customer_since(
        self,
        customer_id: uuid.UUID,
        classifications: Collection[CancellationClassification],
        since: datetime,
        tenant_id: uuid.UUID,
    ) -> list[Cancellation]:
        """Cancelaciones vivas de turnos del cliente con esas clasificaciones, pedidas desde
        `since` (incidencias, D-014). El cruce con `bookings` es por la FK compuesta; un turno
        anulado (cargado por error) no cuenta aunque su cancelación siga viva (adenda 5.1-7)."""
        result = await self._session.scalars(
            select(Cancellation)
            .join(
                Booking,
                (Booking.tenant_id == Cancellation.tenant_id)
                & (Booking.id == Cancellation.booking_id),
            )
            .where(
                Booking.customer_id == customer_id,
                Booking.voided_at.is_(None),
                Cancellation.classification.in_(tuple(classifications)),
                Cancellation.requested_at >= since,
                *self._scope(tenant_id, False),
            )
            .order_by(Cancellation.requested_at, Cancellation.id)
        )
        return list(result.all())
