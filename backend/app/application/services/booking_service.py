"""Turnos — FASE-3-CONTRATO §1.3, §2.1 y §4.

Toda transición sale de `app.domain.booking_state` (whitelist); acá se agregan las guardas que
miran la base: `SELECT … FOR UPDATE` sobre el turno y `from == estado actual` (C-18), y el
`EXCLUDE` como juez final del horario.

**Vencimiento perezoso** (§1.3): el `WHERE` de un `EXCLUDE` no puede depender de `now()`.
Antes de insertar o confirmar un turno, el servicio vence **en la misma transacción** los holds
del mismo puesto con `hold_expires_at <= now`. Así la base nunca rechaza una reserva por un hold
ya vencido; el job periódico de F5 es solo limpieza.

`create` es **mínimo**: calcula estado, precio y seña del catálogo y deja que el `EXCLUDE`
decida. La validación de disponibilidad (franjas, bloqueos, reglas horarias) es F5.

FASE-4-CONTRATO (PR 4.2): la seña es global (`tenant_settings`, D4-4); todo servicio tiene precio
base (D4-6); un turno a cotizar no ocupa el puesto (D4-13). La anticipación mínima, la
retención y el umbral de cancelación tardía se leen de `tenant_settings` y se le pasan al
dominio por parámetro.

Orden de locks: este servicio solo toma locks de turnos. Los holds vencidos del puesto se toman
con `FOR UPDATE SKIP LOCKED` (F12): un hold que otra transacción tiene tomado lo resuelve esa
transacción, así dos operaciones que bloquearon cada una su turno y vencen holds del mismo
puesto no se esperan en cruz (deadlock). `JobService` toma job → turno; `QuoteService`
cotización → turno. Nadie toma turno → job, así que no hay ciclo de espera.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services._base import (
    ServiceBase,
    alive_unique,
    as_aware,
    first_match,
    require_instant,
    require_key,
)
from app.application.services._items import catalog_items
from app.application.services._payments import PaymentInput, create_payment
from app.application.services.errors import (
    AlreadyExistsError,
    IdempotencyKeyReusedError,
    SlotTakenError,
)
from app.application.services.settings_service import load_settings
from app.domain.booking_state import (
    BookingAction,
    anticipation_min,
    classify_cancellation,
    next_booking_status,
)
from app.domain.deposit import deposit_status_for
from app.domain.enums import (
    BookingSource,
    BookingStatus,
    CancellationClassification,
    CancellationInitiator,
    Channel,
    PaymentKind,
    PricingMode,
    QuoteStatus,
)
from app.domain.exceptions import GuardFailedError, InvalidTransition
from app.domain.quote_state import QuoteAction, next_quote_status
from app.persistence.db._savepoint import (
    SavepointConflictError,
    guarded_savepoint,
    unique_violation_classifier,
)
from app.persistence.models.agenda import (
    BOOKING_OVERLAP_CONSTRAINT,
    Booking,
    BookingItem,
    ScheduleBlock,
)
from app.persistence.models.cancellation import (
    CANCELLATIONS_BOOKING_UNIQUE,
    DEFAULT_REASON,
    Cancellation,
)
from app.persistence.models.catalog import PaymentMethod
from app.persistence.models.money import Payment
from app.persistence.models.quote import Quote
from app.persistence.repositories.agenda import (
    HOLD_STATUSES,
    BookingItemRepository,
    BookingRepository,
)
from app.persistence.repositories.cancellations import CancellationRepository
from app.persistence.repositories.catalog import (
    PaymentMethodRepository,
    ResourceRepository,
    VehicleSizeRepository,
)
from app.persistence.repositories.customers import CustomerRepository, VehicleRepository
from app.persistence.repositories.money import PaymentRepository, deposits_paid
from app.persistence.repositories.quotes import QuoteRepository

#: `CHECK (length(reason) <= 160)` de `cancellations`.
MAX_CANCELLATION_REASON = 160
#: Motivo del caso que abre `record_deposit_after_slot_lost` (F5).
SLOT_LOST_REASON = "La seña llegó después de que venció el hold y otro turno tomó el horario"

_OVERLAP = unique_violation_classifier("overlap", constraint=BOOKING_OVERLAP_CONSTRAINT)
_CODE = alive_unique("bookings", "code")
_CANCELLATION = unique_violation_classifier(
    "cancellation",
    constraint=CANCELLATIONS_BOOKING_UNIQUE,
    columns=("cancellations.tenant_id", "cancellations.booking_id"),
)


@dataclass(frozen=True, slots=True)
class DepositConfirmation:
    booking: Booking
    payment: Payment


def _conflict_error(exc: SavepointConflictError) -> Exception:
    if exc.constraint == "overlap":
        return SlotTakenError("the resource is already booked in that interval")
    return AlreadyExistsError("booking code already taken")


class BookingService(ServiceBase):
    def __init__(
        self, session: AsyncSession, tenant_id: uuid.UUID, actor_user_id: uuid.UUID | None
    ) -> None:
        super().__init__(session, tenant_id, actor_user_id)
        self._bookings = BookingRepository(self._session)
        self._payments = PaymentRepository(self._session)

    # ── Vencimiento perezoso ──────────────────────────────────────────────────

    async def expire_holds(self, resource_id: uuid.UUID, now: datetime) -> list[Booking]:
        """Vence los `PENDIENTE_SEÑA` del puesto con `hold_expires_at <= now`.

        Toma los turnos con `FOR UPDATE SKIP LOCKED` (en orden de `id`) y los pasa a
        `VENCIDO` (no terminal, R-T-030). Devuelve los que venció. Un hold que otra
        transacción tiene tomado no se espera (F12): lo resuelve esa transacción, y el
        `EXCLUDE` sigue siendo la garantía del horario (si esa transacción revierte, un
        alta encima choca con `SlotTakenError` y se reintenta).
        """
        await self._enter()
        require_instant(now)
        expired = await self._bookings.lock_expired_holds(resource_id, now, self._tenant_id)
        for booking in expired:
            booking.status = next_booking_status(booking.status, BookingAction.EXPIRE_HOLD)
        if expired:
            await self._session.flush()
        return expired

    # ── Alta mínima (la disponibilidad es F5) ─────────────────────────────────

    async def create(
        self,
        *,
        code: str,
        source: BookingSource,
        channel: Channel,
        resource_id: uuid.UUID,
        start_at: datetime,
        customer_id: uuid.UUID,
        service_ids: Sequence[uuid.UUID],
        vehicle_size_id: uuid.UUID,
        now: datetime,
        vehicle_id: uuid.UUID | None = None,
        notes: str | None = None,
        terms_version: str | None = None,
        terms_accepted_at: datetime | None = None,
    ) -> Booking:
        """Alta de un turno con estado, precio, duración y seña **snapshot** del catálogo.

        Los servicios **se suman** (adenda C1): un ítem por servicio, y el turno guarda los
        totales (duración y precio sumados; seña, la suma de la de cada ítem).

        - Con un ítem `A_COTIZAR` (a lo sumo uno) → `PENDIENTE_COTIZACION`: precio = suma de los
          bases, seña 0 hasta aceptar, **sin ocupar el puesto ni retener horario** (D4-13), y
          nace su cotización `PENDIENTE` para ese servicio.
        - Con seña requerida > 0 → `PENDIENTE_SEÑA`, con hold de `hold_min`. Sin seña →
          `CONFIRMADO`.

        Un inicio antes de `now + min_advance_min` es `GuardFailedError` (D-002). Un choque del
        `EXCLUDE` es `SlotTakenError`; un código repetido, `AlreadyExistsError`.
        """
        await self._enter()
        require_instant(start_at)
        require_instant(now)
        settings = await load_settings(self._session, self._tenant_id)
        if start_at < now + timedelta(minutes=settings.min_advance_min):
            raise GuardFailedError(
                f"bookings need at least {settings.min_advance_min} min of advance"
            )
        await self._require(ResourceRepository(self._session), resource_id)
        await self._require(CustomerRepository(self._session), customer_id)
        await self._require(VehicleSizeRepository(self._session), vehicle_size_id)
        if vehicle_id is not None:
            await self._require(VehicleRepository(self._session), vehicle_id)
        items = await catalog_items(
            self._session,
            self._tenant_id,
            service_ids,
            vehicle_size_id,
            deposit_bps=settings.effective_deposit_bps,
        )
        duration = sum(item.duration_min for item in items)
        quoted = next((item for item in items if item.quoted), None)
        price = sum(item.price_cents for item in items)
        # El ítem cotizado no lleva seña al reservar (D4-7); el turno a cotizar, ninguna:
        # la seña completa se calcula al aceptar la cotización.
        deposit = 0 if quoted is not None else sum(item.deposit_cents for item in items)
        if quoted is not None:
            action = BookingAction.CREATE_FOR_QUOTE
        elif deposit > 0:
            action = BookingAction.CREATE_WITH_DEPOSIT
        else:
            action = BookingAction.CREATE_CONFIRMED
        status = next_booking_status(None, action)
        hold_expires_at = (
            now + timedelta(minutes=settings.hold_min) if status in HOLD_STATUSES else None
        )

        await self.expire_holds(resource_id, now)
        booking = Booking(
            tenant_id=self._tenant_id,
            code=code,
            source=source,
            channel=channel,
            status=status,
            resource_id=resource_id,
            start_at=start_at,
            end_at=start_at + timedelta(minutes=duration),
            hold_expires_at=hold_expires_at,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            vehicle_size_id=vehicle_size_id,
            duration_min=duration,
            price_cents=price,
            deposit_required_cents=deposit,
            notes=notes,
            terms_version=terms_version,
            terms_accepted_at=terms_accepted_at,
        )
        try:
            async with guarded_savepoint(self._session, first_match(_OVERLAP, _CODE)):
                self._session.add(booking)
        except SavepointConflictError as exc:
            raise _conflict_error(exc) from exc
        for position, item in enumerate(items):
            self._session.add(
                BookingItem(
                    tenant_id=self._tenant_id,
                    booking_id=booking.id,
                    service_id=item.service_id,
                    position=position,
                    service_name_snapshot=item.name,
                    duration_min=item.duration_min,
                    pricing_mode=item.pricing_mode,
                    price_cents=item.price_cents,
                    deposit_bps=item.deposit_bps,
                )
            )
        await self._session.flush()

        if quoted is not None:
            quote = Quote(
                tenant_id=self._tenant_id,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                service_id=quoted.service_id,
                vehicle_size_id=vehicle_size_id,
                booking_id=booking.id,
                status=next_quote_status(None, QuoteAction.CREATE),
                requested_at=now,
                # La vigencia la fija quien cotiza (`QuoteService.quote`), no un hold (D4-13).
            )
            self._session.add(quote)
            await self._session.flush()
            booking.quote_id = quote.id
            await self._session.flush()
        return booking

    async def add_block(
        self,
        *,
        starts_at: datetime,
        ends_at: datetime,
        resource_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> ScheduleBlock:
        """Bloqueo de agenda (R-T-013). No cancela turnos vigentes (R-T-052): lo mira F5."""
        await self._enter()
        require_instant(starts_at)
        require_instant(ends_at)
        if ends_at <= starts_at:
            raise GuardFailedError("a block must end after it starts")
        if resource_id is not None:
            await self._require(ResourceRepository(self._session), resource_id)
        block = ScheduleBlock(
            tenant_id=self._tenant_id,
            resource_id=resource_id,
            starts_at=starts_at,
            ends_at=ends_at,
            created_by_user_id=self._actor(),
        )
        if reason is not None and reason.strip():
            block.reason = reason.strip()
        self._session.add(block)
        await self._session.flush()
        return block

    # ── Seña y confirmación ───────────────────────────────────────────────────

    async def _require_accepted_quote(self, booking: Booking) -> None:
        """Un turno con ítem `A_COTIZAR` no se confirma ni toma seña sin su cotización
        `ACEPTADO` (FASE-4-CONTRATO §5): el precio base no alcanza."""
        items = await BookingItemRepository(self._session).list_for_booking(
            booking.id, self._tenant_id
        )
        if not any(item.pricing_mode == PricingMode.A_COTIZAR for item in items):
            return
        quote = (
            await QuoteRepository(self._session).get_by_id(booking.quote_id, self._tenant_id)
            if booking.quote_id is not None
            else None
        )
        if quote is None or quote.status != QuoteStatus.ACEPTADO:
            raise GuardFailedError("a booking to be quoted needs its accepted quote first")

    async def _replayed_deposit(self, booking: Booking, key: str) -> Payment | None:
        existing = await self._payments.get_by_key(key, self._tenant_id)
        if existing is None:
            return None
        if existing.booking_id != booking.id or existing.kind != PaymentKind.SENA:
            raise IdempotencyKeyReusedError(key)
        return existing

    async def _deposit_method(self, payment: PaymentInput) -> PaymentMethod:
        return await self._require(
            PaymentMethodRepository(self._session), payment.payment_method_id
        )

    async def _record_deposit(
        self, booking: Booking, payment: PaymentInput, method: PaymentMethod | None = None
    ) -> Payment:
        if method is None:
            method = await self._deposit_method(payment)
        return await create_payment(
            self._session,
            self._tenant_id,
            self._actor(),
            method,
            payment,
            kind=PaymentKind.SENA,
            booking_id=booking.id,
        )

    async def confirm_deposit(
        self, booking_id: uuid.UUID, payment: PaymentInput, *, now: datetime
    ) -> DepositConfirmation:
        """Registra la `SEÑA` y pasa `PENDIENTE_SEÑA → CONFIRMADO` (importe > 0).

        Idempotente por la clave del pago: un reenvío devuelve el pago original. Antes de
        confirmar vence los holds del puesto: si el de este turno ya venció, queda `VENCIDO`
        y la seña se registra con `confirm_late`, donde el `EXCLUDE` decide.
        """
        await self._enter()
        key = require_key(payment.idempotency_key)
        require_instant(now)
        booking = await self._lock(self._bookings, booking_id)
        replayed = await self._replayed_deposit(booking, key)
        if replayed is not None:
            return DepositConfirmation(booking, replayed)
        await self._require_accepted_quote(booking)
        await self.expire_holds(booking.resource_id, now)
        target = next_booking_status(booking.status, BookingAction.RECORD_DEPOSIT)
        recorded = await self._record_deposit(booking, payment)
        booking.status = target
        booking.hold_expires_at = None
        await self._session.flush()
        return DepositConfirmation(booking, recorded)

    async def confirm_late(
        self, booking_id: uuid.UUID, *, now: datetime, payment: PaymentInput | None = None
    ) -> Booking:
        """`VENCIDO → CONFIRMADO` (confirmación tardía): el `EXCLUDE` decide si el horario
        sigue libre (`SlotTakenError` si no). Con `payment`, registra además la seña.

        Guardas (F4): un turno con ítem `A_COTIZAR` exige su cotización `ACEPTADO`, aunque
        tenga precio base (un turno a cotizar vencido se vuelve a cotizar, no se confirma
        tarde); si pide seña (`deposit_required_cents > 0`) exige su pago, y si no la pide
        rechaza uno. Si el horario ya no está libre, la seña se
        registra con `record_deposit_after_slot_lost`.
        """
        await self._enter()
        require_instant(now)
        booking = await self._lock(self._bookings, booking_id)
        if payment is not None:
            key = require_key(payment.idempotency_key)
            if await self._replayed_deposit(booking, key) is not None:
                return booking
        target = next_booking_status(booking.status, BookingAction.LATE_CONFIRM)
        await self._require_accepted_quote(booking)
        if booking.deposit_required_cents > 0 and payment is None:
            raise GuardFailedError("the booking requires a deposit: confirm late with its payment")
        if booking.deposit_required_cents == 0 and payment is not None:
            raise GuardFailedError("the booking requires no deposit: confirm late without payment")
        # El medio se resuelve antes de tocar el turno: un medio inexistente no deja el turno
        # confirmado sin su seña.
        method = await self._deposit_method(payment) if payment is not None else None
        await self.expire_holds(booking.resource_id, now)
        try:
            async with guarded_savepoint(self._session, _OVERLAP):
                booking.status = target
                booking.hold_expires_at = None
        except SavepointConflictError as exc:
            raise _conflict_error(exc) from exc
        if payment is not None:
            await self._record_deposit(booking, payment, method)
        return booking

    async def record_deposit_after_slot_lost(
        self, booking_id: uuid.UUID, payment: PaymentInput, *, now: datetime
    ) -> Cancellation:
        """La seña de un turno `VENCIDO` cuyo horario ya tomó otro turno (F5).

        La plata llegó y no hay horario que confirmar (`confirm_late` choca con el
        `EXCLUDE`): se registra el pago `SEÑA` en el turno **sin cambiar su estado** y se abre
        el caso de la seña en `cancellations` (`NEGOCIO`, `OPERATIVA`,
        `REPROGRAMACION_O_DEVOLUCION_PENDIENTE`, `VENCIDO → VENCIDO`), que se cierra con
        `DepositResolutionService` (devolver o reprogramar). `now` es la hora del server.

        Si el horario sigue libre, `GuardFailedError`: corresponde `confirm_late`. Un turno
        tiene un solo caso: un segundo pago sobre un caso ya abierto se rechaza (lo resuelve
        una persona). Idempotente por la clave del pago: un reenvío devuelve el caso.
        """
        await self._enter()
        key = require_key(payment.idempotency_key)
        require_instant(now)
        actor = self._actor()
        cancellations = CancellationRepository(self._session)
        booking = await self._lock(self._bookings, booking_id)
        if await self._replayed_deposit(booking, key) is not None:
            existing = await cancellations.find_by_booking(booking.id, self._tenant_id)
            if existing is None:  # la clave la usó otra seña de este turno
                raise IdempotencyKeyReusedError(key)
            return existing
        if booking.status != BookingStatus.VENCIDO:
            raise InvalidTransition("booking", booking.status, "RECORD_DEPOSIT_AFTER_SLOT_LOST")
        await self.expire_holds(booking.resource_id, now)
        if not await self._bookings.slot_taken(booking, self._tenant_id):
            raise GuardFailedError("the slot is still free: use confirm_late")
        if await cancellations.find_by_booking(booking.id, self._tenant_id) is not None:
            raise GuardFailedError("the booking already has a deposit case: resolve it first")

        await self._record_deposit(booking, payment)
        paid = deposits_paid(await self._payments.list_for_booking(booking.id, self._tenant_id))
        classification = CancellationClassification.OPERATIVA
        cancellation = Cancellation(
            tenant_id=self._tenant_id,
            booking_id=booking.id,
            initiator=CancellationInitiator.NEGOCIO,
            classification=classification,
            requested_at=now,
            anticipation_min=anticipation_min(as_aware(booking.start_at), now),
            reason=SLOT_LOST_REASON,
            previous_status=booking.status,
            resulting_status=booking.status,
            deposit_paid_cents=paid,
            deposit_status=deposit_status_for(classification, paid),
            actor_user_id=actor,
            terms_version=booking.terms_version,
        )
        self._session.add(cancellation)
        await self._session.flush()
        return cancellation

    # ── Cancelaciones ─────────────────────────────────────────────────────────

    async def cancel_by_client(
        self,
        booking_id: uuid.UUID,
        *,
        now: datetime,
        reason: str | None = None,
    ) -> Cancellation:
        """El cliente cancela: `CANCELADO_CLIENTE` (anticipación ≥ 0) o
        `AUSENTE_CON_AVISO_POSTERIOR` (< 0). Clasifica (R-T-037) y abre el caso de la seña
        con `deposit_status_for`. Sin actor = desde la web.

        `now` es la hora **del server**, nunca un dato del cliente (F7): la clasificación
        decide si la seña se puede retener (`EN_REVISION` → `RETENIDA`), y un cliente que
        mandara una hora anterior se la sacaría de encima. Queda como `requested_at`.

        El umbral de cancelación tardía es `late_cancel_threshold_min` del negocio.

        Idempotente por turno: si ya hay una cancelación, la devuelve (R-T-035).
        """
        return await self._cancel(
            booking_id,
            requested_at=now,
            reason=reason,
            initiator=CancellationInitiator.CLIENTE,
        )

    async def cancel_operational(
        self, booking_id: uuid.UUID, *, requested_at: datetime, reason: str
    ) -> Cancellation:
        """El negocio cancela (un solo caso de uso, **[corregir]** C-10). Actor y motivo
        obligatorios. No desde `RECIBIDO`: con el auto adentro se cancela el job por demora."""
        if not (reason or "").strip():
            raise GuardFailedError("an operational cancellation needs a reason")
        self._actor()
        return await self._cancel(
            booking_id,
            requested_at=requested_at,
            reason=reason,
            initiator=CancellationInitiator.NEGOCIO,
        )

    async def _cancel(
        self,
        booking_id: uuid.UUID,
        *,
        requested_at: datetime,
        reason: str | None,
        initiator: CancellationInitiator,
    ) -> Cancellation:
        await self._enter()
        require_instant(requested_at)
        cancellations = CancellationRepository(self._session)
        booking = await self._lock(self._bookings, booking_id)
        existing = await cancellations.find_by_booking(booking.id, self._tenant_id)
        if existing is not None:
            return existing

        anticipation = anticipation_min(as_aware(booking.start_at), requested_at)
        if initiator == CancellationInitiator.CLIENTE:
            action = (
                BookingAction.CANCEL_BY_CLIENT
                if anticipation >= 0
                else BookingAction.CANCEL_BY_CLIENT_AFTER_START
            )
            settings = await load_settings(self._session, self._tenant_id)
            classification = classify_cancellation(anticipation, settings.late_cancel_threshold_min)
        else:
            action = BookingAction.CANCEL_OPERATIONAL
            classification = CancellationClassification.OPERATIVA
        target = next_booking_status(booking.status, action)

        cleaned = (reason or "").strip() or DEFAULT_REASON
        if len(cleaned) > MAX_CANCELLATION_REASON:
            raise GuardFailedError(f"reason must have at most {MAX_CANCELLATION_REASON} chars")
        paid = deposits_paid(await self._payments.list_for_booking(booking.id, self._tenant_id))
        cancellation = Cancellation(
            tenant_id=self._tenant_id,
            booking_id=booking.id,
            initiator=initiator,
            classification=classification,
            requested_at=requested_at,
            anticipation_min=anticipation,
            reason=cleaned,
            previous_status=booking.status,
            resulting_status=target,
            deposit_paid_cents=paid,
            deposit_status=deposit_status_for(classification, paid),
            actor_user_id=self._actor_user_id,
            # La versión DEL TURNO, no la vigente (R-T-057).
            terms_version=booking.terms_version,
        )
        try:
            async with guarded_savepoint(self._session, _CANCELLATION):
                booking.status = target
                self._session.add(cancellation)
        except SavepointConflictError:  # pragma: no cover  # el lock del turno lo evita
            winner = await cancellations.find_by_booking(booking.id, self._tenant_id)
            if winner is None:
                raise
            return winner
        return cancellation

    async def mark_no_show(self, booking_id: uuid.UUID) -> Booking:
        """`CONFIRMADO → NO_ASISTIO` (**[diseñar]**, C-04). Sin fila en `cancellations`."""
        await self._enter()
        self._actor()
        booking = await self._lock(self._bookings, booking_id)
        booking.status = next_booking_status(booking.status, BookingAction.MARK_NO_SHOW)
        await self._session.flush()
        return booking

    async def get(self, booking_id: uuid.UUID) -> Booking:
        await self._enter()
        return await self._require(self._bookings, booking_id)
