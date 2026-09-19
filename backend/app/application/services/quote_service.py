"""Cotizaciones — FASE-3-CONTRATO §2.3 y §4. **[diseñar]**: en el legacy son insert-only.

`PENDIENTE → COTIZADO` (exige precio y duración) `→ ACEPTADO | RECHAZADO`;
`PENDIENTE, COTIZADO → CANCELADO | VENCIDO`. Aceptar con turno pasa precio y duración al turno
y lo confirma (o lo deja esperando la seña): el job hereda el precio acordado (**[corregir]**
R-C-029, entraba como "Adicional").

Orden de locks: cotización → turno (como `JobService`, que toma job → turno).

Rechazar o cancelar **no** toca el turno `PENDIENTE_COTIZACION`: su hold vence solo
(vencimiento perezoso) o lo cancela alguien con `BookingService`. Decidirlo acá sería una regla
nueva sobre el turno que el contrato no fija.
"""

import uuid
from datetime import datetime, timedelta

from app.application.services._base import ServiceBase, as_aware, require_instant
from app.application.services.booking_service import BookingService
from app.application.services.errors import SlotTakenError
from app.domain.booking_state import BookingAction, hold_expired, next_booking_status
from app.domain.deposit import deposit_required
from app.domain.enums import BookingStatus, SuppliesPurchaser
from app.domain.exceptions import GuardFailedError, InvalidAmountError
from app.domain.quote_state import QuoteAction, check_quote_terms, next_quote_status
from app.persistence.db._savepoint import (
    SavepointConflictError,
    guarded_savepoint,
    unique_violation_classifier,
)
from app.persistence.models.agenda import BOOKING_OVERLAP_CONSTRAINT
from app.persistence.models.quote import Quote
from app.persistence.repositories.agenda import BookingRepository
from app.persistence.repositories.catalog import ServiceRepository, VehicleSizeRepository
from app.persistence.repositories.customers import CustomerRepository, VehicleRepository
from app.persistence.repositories.quotes import QuoteRepository

_OVERLAP = unique_violation_classifier("overlap", constraint=BOOKING_OVERLAP_CONSTRAINT)


class QuoteService(ServiceBase):
    async def create(
        self,
        *,
        customer_id: uuid.UUID,
        service_id: uuid.UUID,
        vehicle_size_id: uuid.UUID,
        requested_at: datetime,
        vehicle_id: uuid.UUID | None = None,
        expires_at: datetime | None = None,
        notes: str | None = None,
    ) -> Quote:
        """Cotización sin turno (R-C-028). La de un turno `A_COTIZAR` la crea `BookingService`."""
        await self._enter()
        require_instant(requested_at)
        await self._require(CustomerRepository(self._session), customer_id)
        await self._require(ServiceRepository(self._session), service_id)
        await self._require(VehicleSizeRepository(self._session), vehicle_size_id)
        if vehicle_id is not None:
            await self._require(VehicleRepository(self._session), vehicle_id)
        quote = Quote(
            tenant_id=self._tenant_id,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            service_id=service_id,
            vehicle_size_id=vehicle_size_id,
            status=next_quote_status(None, QuoteAction.CREATE),
            requested_at=requested_at,
            expires_at=expires_at,
            notes=notes,
        )
        self._session.add(quote)
        await self._session.flush()
        return quote

    async def quote(
        self,
        quote_id: uuid.UUID,
        *,
        agreed_price_cents: int,
        agreed_duration_min: int,
        quoted_at: datetime,
        supplies_purchaser: SuppliesPurchaser | None = None,
        supplies_cost_cents: int | None = None,
        expires_at: datetime | None = None,
    ) -> Quote:
        """`PENDIENTE → COTIZADO`: exige precio y duración positivos (R-C-024: nunca `0`)."""
        await self._enter()
        require_instant(quoted_at)
        quote = await self._lock(QuoteRepository(self._session), quote_id)
        target = next_quote_status(quote.status, QuoteAction.QUOTE)
        check_quote_terms(agreed_price_cents, agreed_duration_min)
        if supplies_cost_cents is not None and supplies_cost_cents < 0:
            raise InvalidAmountError("supplies cost must be >= 0")
        quote.status = target
        quote.agreed_price_cents = agreed_price_cents
        quote.agreed_duration_min = agreed_duration_min
        quote.quoted_at = quoted_at
        if supplies_purchaser is not None:
            quote.supplies_purchaser = supplies_purchaser
        quote.supplies_cost_cents = supplies_cost_cents
        if expires_at is not None:
            quote.expires_at = expires_at
        await self._session.flush()
        return quote

    async def accept(
        self,
        quote_id: uuid.UUID,
        *,
        decided_at: datetime,
        now: datetime,
        hold_expires_at: datetime | None = None,
    ) -> Quote:
        """`COTIZADO → ACEPTADO`. Con turno: precio y duración pasan al turno, que queda
        `CONFIRMADO` (sin seña) o `PENDIENTE_SEÑA` (con seña, exige hold).

        Si la duración acordada alarga el turno sobre otro, el `EXCLUDE` lo rechaza
        (`SlotTakenError`). Antes vence los holds del puesto (§1.3): si el del propio turno
        ya venció, el turno está `VENCIDO` y aceptar falla (`InvalidTransition`).

        Una cotización con `expires_at <= now` (el comparador de los holds, C-16) no se
        acepta (F6): `GuardFailedError`, aunque todavía nadie la haya pasado a `VENCIDO`.
        """
        await self._enter()
        require_instant(decided_at)
        require_instant(now)
        quote = await self._lock(QuoteRepository(self._session), quote_id)
        target = next_quote_status(quote.status, QuoteAction.ACCEPT)
        if quote.expires_at is not None and hold_expired(as_aware(quote.expires_at), now):
            raise GuardFailedError("the quote has expired: it cannot be accepted")
        if quote.booking_id is not None:
            await self._pass_to_booking(quote, now, hold_expires_at)
        quote.status = target
        quote.decided_at = decided_at
        quote.decided_by_user_id = self._actor()
        await self._session.flush()
        return quote

    async def _pass_to_booking(
        self, quote: Quote, now: datetime, hold_expires_at: datetime | None
    ) -> None:
        assert quote.booking_id is not None
        assert quote.agreed_price_cents is not None and quote.agreed_duration_min is not None
        self._actor()
        booking = await self._lock(BookingRepository(self._session), quote.booking_id)
        await BookingService(self._session, self._tenant_id, self._actor_user_id).expire_holds(
            booking.resource_id, now
        )
        deposit = deposit_required(quote.agreed_price_cents, booking.deposit_bps)
        action = (
            BookingAction.ACCEPT_QUOTE_WITH_DEPOSIT
            if deposit > 0
            else BookingAction.ACCEPT_QUOTE_CONFIRMED
        )
        booking_target = next_booking_status(booking.status, action)
        # Un `PENDIENTE_COTIZACION` siempre tiene hold (CHECK): si no se pasa uno nuevo, sigue
        # corriendo el mismo.
        hold = hold_expires_at if hold_expires_at is not None else booking.hold_expires_at
        try:
            async with guarded_savepoint(self._session, _OVERLAP):
                booking.price_cents = quote.agreed_price_cents
                booking.duration_min = quote.agreed_duration_min
                booking.end_at = as_aware(booking.start_at) + timedelta(
                    minutes=quote.agreed_duration_min
                )
                booking.deposit_required_cents = deposit
                booking.status = booking_target
                booking.hold_expires_at = (
                    hold if booking_target == BookingStatus.PENDIENTE_SENA else None
                )
        except SavepointConflictError as exc:
            raise SlotTakenError("the agreed duration overlaps another booking") from exc

    async def reject(self, quote_id: uuid.UUID, *, decided_at: datetime) -> Quote:
        return await self._decide(quote_id, QuoteAction.REJECT, decided_at)

    async def cancel(self, quote_id: uuid.UUID, *, decided_at: datetime) -> Quote:
        return await self._decide(quote_id, QuoteAction.CANCEL, decided_at)

    async def _decide(self, quote_id: uuid.UUID, action: QuoteAction, at: datetime) -> Quote:
        await self._enter()
        require_instant(at)
        quote = await self._lock(QuoteRepository(self._session), quote_id)
        quote.status = next_quote_status(quote.status, action)
        quote.decided_at = at
        quote.decided_by_user_id = self._actor()
        await self._session.flush()
        return quote

    async def expire(self, quote_id: uuid.UUID, *, now: datetime) -> Quote:
        """`PENDIENTE, COTIZADO → VENCIDO`, solo con `expires_at <= now` (mismo comparador
        que el hold del turno, C-16)."""
        await self._enter()
        require_instant(now)
        quote = await self._lock(QuoteRepository(self._session), quote_id)
        target = next_quote_status(quote.status, QuoteAction.EXPIRE)
        expires_at = quote.expires_at
        if expires_at is None or not hold_expired(as_aware(expires_at), now):
            raise GuardFailedError("the quote has not expired yet")
        quote.status = target
        await self._session.flush()
        return quote
