"""Repositorio de cancelaciones — FASE-3-CONTRATO §1.4 y §2.4."""

import uuid

from sqlalchemy import select

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
