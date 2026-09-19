"""El lavado — FASE-3-CONTRATO §2.2 y §4.

`job_events` es la fuente de verdad; `jobs.status` y los `*_at` son su caché. Cada operación:

1. toma el job (o el turno, al recibir) con `SELECT … FOR UPDATE`;
2. **después** del lock, busca la clave de idempotencia en `job_events`. Si existe y es de
   esta misma operación sobre este mismo job, devuelve el resultado anterior sin efectos; si
   es de otra, `IdempotencyKeyReusedError`;
3. valida la transición con `app.domain.job_state.apply_event` (whitelist) y las guardas;
4. muta y escribe su `job_events` con `occurred_at` del cliente, `actor_user_id` y la clave.

**Idempotencia ante la carrera.** Dos reenvíos concurrentes con la misma clave compiten por
el lock del job (o del turno): el segundo espera, y al obtenerlo su lectura de la clave ya ve
el evento del primero (READ COMMITTED: cada sentencia ve lo comiteado antes de empezar). El
alta de un walk-in no tiene fila que bloquear: ahí la red es `UNIQUE (tenant_id,
idempotency_key)`. El INSERT va en un SAVEPOINT (`_savepoint.guarded_savepoint`); si choca, se
relee el evento ganador y se devuelve su job. En ningún caso la violación llega como 500.

Los eventos encadenados derivan su clave de la del llamador: `f"{key}:settled"` (cobro
completo al finalizar, cobrar, anular o ajustar) y `f"{key}:retained"` (retención de la seña al
cancelar por demora).

Orden de locks: job → turno. `receive` toma el turno y crea el job (no hay job que bloquear).

Transacción: del llamador. Ante cualquier excepción, rollback; el servicio no deja nada a medias
a propósito, pero tampoco lo deshace.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services._base import (
    ServiceBase,
    alive_unique,
    as_aware,
    first_match,
    require_instant,
    require_key,
)
from app.application.services._payments import PaymentInput, create_payment, void_payment_row
from app.application.services.errors import (
    CatalogIncoherentError,
    IdempotencyKeyReusedError,
    QuoteAlreadyUsedError,
)
from app.domain.booking_state import BookingAction, next_booking_status
from app.domain.delay import arrival_delay_min
from app.domain.deposit import balance_due, net_paid, total_agreed
from app.domain.enums import (
    Channel,
    DirtLevel,
    JobEventType,
    JobStatus,
    PaymentKind,
    PricingMode,
    QuoteStatus,
)
from app.domain.exceptions import GuardFailedError, InvalidAmountError
from app.domain.job_state import (
    apply_event,
    check_delay_cancellation,
    require_reason,
    retention_is_active,
)
from app.domain.void import VoidReason
from app.persistence.db._savepoint import (
    SavepointConflictError,
    guarded_savepoint,
    unique_violation_classifier,
)
from app.persistence.models.agenda import Booking
from app.persistence.models.job import (
    JOB_EVENTS_IDEMPOTENCY_UNIQUE,
    JOBS_QUOTE_UNIQUE,
    Job,
    JobEvent,
    JobInspection,
)
from app.persistence.models.money import Payment
from app.persistence.repositories.agenda import BookingRepository
from app.persistence.repositories.catalog import (
    PaymentMethodRepository,
    ResourceRepository,
    ServicePriceRepository,
    ServiceRepository,
    VehicleSizeRepository,
)
from app.persistence.repositories.customers import CustomerRepository, VehicleRepository
from app.persistence.repositories.jobs import (
    JobEventRepository,
    JobInspectionRepository,
    JobRepository,
)
from app.persistence.repositories.money import PaymentRepository, deposits_paid
from app.persistence.repositories.quotes import QuoteRepository
from app.persistence.repositories.user_repository import UserRepository

_EVENT_KEY = unique_violation_classifier(
    "job_event_key",
    constraint=JOB_EVENTS_IDEMPOTENCY_UNIQUE,
    columns=("job_events.tenant_id", "job_events.idempotency_key"),
)

#: Una cotización se usa en un solo job (F3): la red final ante la carrera.
_QUOTE = alive_unique("jobs", "quote_id")

_J = JobStatus
_E = JobEventType

#: Estados cerrados por cobro: anular un pago que deje saldo > 0 los haría mentir (A1).
_SETTLED_STATUSES = frozenset({_J.COBRADO, _J.RETIRADO})


def settled_key(key: str) -> str:
    return f"{key}:settled"


def retained_key(key: str) -> str:
    return f"{key}:retained"


class JobService(ServiceBase):
    def __init__(
        self, session: AsyncSession, tenant_id: uuid.UUID, actor_user_id: uuid.UUID | None
    ) -> None:
        super().__init__(session, tenant_id, actor_user_id)
        self._jobs = JobRepository(session)
        self._events = JobEventRepository(session)
        self._bookings = BookingRepository(session)
        self._payments = PaymentRepository(session)

    # ── Lectura ───────────────────────────────────────────────────────────────

    async def get(self, job_id: uuid.UUID) -> Job:
        await self._enter()
        return await self._require(self._jobs, job_id)

    async def history(self, job_id: uuid.UUID) -> list[JobEvent]:
        await self._enter()
        await self._require(self._jobs, job_id)
        return await self._events.list_for_job(job_id, self._tenant_id)

    async def balance(self, job_id: uuid.UUID) -> int:
        """Saldo derivado (X9): total pactado − pagos netos vivos. Negativo = a favor."""
        await self._enter()
        return await self._balance(await self._require(self._jobs, job_id))

    async def _balance(self, job: Job, *, without: uuid.UUID | None = None) -> int:
        total = total_agreed(job.base_price_cents, job.surcharge_cents, job.discount_cents)
        return balance_due(total, await self._lines(job, without=without))

    async def _lines(
        self, job: Job, *, without: uuid.UUID | None = None
    ) -> list[tuple[PaymentKind, int, bool]]:
        payments = await self._payments.list_for_job(job.id, self._tenant_id)
        return [
            (p.kind, p.amount_cents, p.voided_at is not None) for p in payments if p.id != without
        ]

    # ── Piezas comunes ────────────────────────────────────────────────────────

    async def _replay(
        self, key: str, job_id: uuid.UUID, event_type: JobEventType
    ) -> JobEvent | None:
        """El evento previo con esta clave, si es de la misma operación sobre el mismo job."""
        event = await self._events.get_by_key(key, self._tenant_id)
        if event is None:
            return None
        if event.job_id != job_id or event.event_type != event_type:
            raise IdempotencyKeyReusedError(key)
        return event

    async def _record(
        self,
        job: Job,
        event_type: JobEventType,
        *,
        key: str,
        occurred_at: datetime,
        metadata: dict[str, Any] | None = None,
        changes: dict[str, Any] | None = None,
    ) -> JobEvent:
        """Valida con el dominio, aplica `changes` al job y escribe el evento, juntos.

        Todo dentro del SAVEPOINT: si la clave choca (otra operación ya la usó), se revierte
        también la mutación del job y el llamador recibe `IdempotencyKeyReusedError`.
        """
        target = apply_event(job.status, event_type)
        event = JobEvent(
            tenant_id=self._tenant_id,
            job_id=job.id,
            event_type=event_type,
            from_status=job.status if target is not None else None,
            to_status=target,
            occurred_at=occurred_at,
            actor_user_id=self._actor(),
            idempotency_key=key,
            event_metadata=metadata or {},
        )
        try:
            async with guarded_savepoint(self._session, _EVENT_KEY):
                for name, value in (changes or {}).items():
                    setattr(job, name, value)
                if target is not None:
                    job.status = target
                self._session.add(event)
        except SavepointConflictError as exc:
            raise IdempotencyKeyReusedError(key) from exc
        return event

    async def _settle_if_paid(self, job: Job, key: str, occurred_at: datetime) -> None:
        """`JOB_SETTLED` encadenado: `FINALIZADO` con saldo derivado == 0 pasa a `COBRADO`."""
        if job.status != _J.FINALIZADO or await self._balance(job) != 0:
            return
        await self._record(
            job,
            _E.JOB_SETTLED,
            key=settled_key(key),
            occurred_at=occurred_at,
            changes={"settled_at": occurred_at},
        )

    async def _lock_job(self, job_id: uuid.UUID) -> Job:
        return await self._lock(self._jobs, job_id)

    async def _lock_booking_of(self, job: Job) -> Booking | None:
        if job.booking_id is None:
            return None
        return await self._lock(self._bookings, job.booking_id)

    # ── Recepción ─────────────────────────────────────────────────────────────

    async def receive(
        self,
        *,
        idempotency_key: str,
        arrived_at: datetime,
        booking_id: uuid.UUID | None = None,
        vehicle_id: uuid.UUID | None = None,
        vehicle_size_id: uuid.UUID | None = None,
        service_id: uuid.UUID | None = None,
        customer_id: uuid.UUID | None = None,
        channel: Channel | None = None,
        resource_id: uuid.UUID | None = None,
        quote_id: uuid.UUID | None = None,
        responsible_user_id: uuid.UUID | None = None,
        notes: str | None = None,
    ) -> Job:
        """`JOB_RECEIVED` (∅ → `PRESENTE`, siempre: **[corregir]** R-O-001).

        **Con turno** (`booking_id`): idempotente por turno (si ya tiene job, lo devuelve);
        snapshots de servicio, precio y seña del turno. El camino de la cotización lo decide
        el **snapshot del turno** (F13), no el `pricing_mode` vivo del servicio: un turno con
        `quote_id` exige esa cotización `ACEPTADO` (y solo esa: un `quote_id` distinto se
        rechaza, F3) y toma su precio; si no, el `price_cents` del turno.
        `arrival_delay_min` con signo; turno → `RECIBIDO`; las señas del turno pasan a apuntar
        al job. El vehículo es el del turno o el que se pasa (la web no exige patente, el job sí).

        **Walk-in**: exige vehículo, tamaño, servicio y canal; precio del catálogo, o de la
        cotización `ACEPTADO` que se pase si el servicio es `A_COTIZAR`. La cotización tiene que
        ser **suelta** (`booking_id IS NULL`, F3): la de un turno se usa recibiendo el turno.

        Una cotización se usa en **un** job vivo (`ux_jobs_tenant_id_quote_id`, F3): reusarla
        es `QuoteAlreadyUsedError`, también ante la carrera.
        """
        await self._enter()
        key = require_key(idempotency_key)
        require_instant(arrived_at)
        responsible = responsible_user_id or self._actor()
        await self._require(UserRepository(self._session), responsible)
        if resource_id is not None:
            await self._require(ResourceRepository(self._session), resource_id)
        if vehicle_id is not None:
            await self._require(VehicleRepository(self._session), vehicle_id)
        if booking_id is not None:
            return await self._receive_booking(
                key, arrived_at, booking_id, vehicle_id, resource_id, quote_id, responsible, notes
            )
        return await self._receive_walk_in(
            key,
            arrived_at,
            vehicle_id=vehicle_id,
            vehicle_size_id=vehicle_size_id,
            service_id=service_id,
            customer_id=customer_id,
            channel=channel,
            resource_id=resource_id,
            quote_id=quote_id,
            responsible=responsible,
            notes=notes,
        )

    async def _replayed_receive(self, key: str) -> Job | None:
        event = await self._events.get_by_key(key, self._tenant_id)
        if event is None:
            return None
        if event.event_type != _E.JOB_RECEIVED:
            raise IdempotencyKeyReusedError(key)
        return await self._require(self._jobs, event.job_id, include_voided=True)

    async def _accepted_quote_price(
        self,
        quote_id: uuid.UUID | None,
        service_id: uuid.UUID,
        vehicle_size_id: uuid.UUID,
        *,
        booking_id: uuid.UUID | None,
    ) -> int:
        """`A_COTIZAR` no se recibe sin cotización `ACEPTADO` (D-006, C-17).

        `booking_id`: el turno que se recibe, o `None` en un walk-in. La cotización tiene que
        ser la de ese turno, y la de un walk-in tiene que ser suelta (F3).
        """
        if quote_id is None:
            raise GuardFailedError("a quoted service needs an accepted quote to be received")
        quote = await self._require(QuoteRepository(self._session), quote_id)
        if booking_id is None and quote.booking_id is not None:
            raise GuardFailedError(
                "the quote belongs to a booking: receive the booking, not a walk-in"
            )
        if booking_id is not None and quote.booking_id != booking_id:
            raise GuardFailedError("the quote is not the booking's quote")
        if quote.status != QuoteStatus.ACEPTADO:
            raise GuardFailedError(f"the quote is {quote.status}, not ACEPTADO")
        if quote.service_id != service_id or quote.vehicle_size_id != vehicle_size_id:
            raise GuardFailedError("the quote is for another service or vehicle size")
        assert quote.agreed_price_cents is not None  # CHECK cotizado_completo
        return quote.agreed_price_cents

    async def _receive_booking(
        self,
        key: str,
        arrived_at: datetime,
        booking_id: uuid.UUID,
        vehicle_id: uuid.UUID | None,
        resource_id: uuid.UUID | None,
        quote_id: uuid.UUID | None,
        responsible: uuid.UUID,
        notes: str | None,
    ) -> Job:
        booking = await self._lock(self._bookings, booking_id)
        replayed = await self._replayed_receive(key)
        if replayed is not None:
            if replayed.booking_id != booking.id:
                raise IdempotencyKeyReusedError(key)
            return replayed
        existing = await self._jobs.find_by_booking(booking.id, self._tenant_id)
        if existing is not None:
            return existing  # recibir es idempotente por turno (**[corregir]** R-O-010)

        booking_target = next_booking_status(booking.status, BookingAction.RECEIVE)
        if quote_id is not None and quote_id != booking.quote_id:
            raise GuardFailedError("the quote is not the booking's quote")
        # F13: el snapshot del turno decide, no el `pricing_mode` vivo del servicio (el
        # catálogo puede cambiar entre la reserva y la llegada).
        price = booking.price_cents
        if booking.quote_id is not None:
            price = await self._accepted_quote_price(
                booking.quote_id,
                booking.service_id,
                booking.vehicle_size_id,
                booking_id=booking.id,
            )
        if price is None:
            raise GuardFailedError("the booking has no price to receive it with")
        vehicle = vehicle_id or booking.vehicle_id
        if vehicle is None:
            raise GuardFailedError("a job needs a vehicle: the booking has none")

        scheduled_at = as_aware(booking.start_at)
        target = apply_event(None, _E.JOB_RECEIVED)
        assert target is not None
        job = Job(
            tenant_id=self._tenant_id,
            booking_id=booking.id,
            quote_id=booking.quote_id,
            customer_id=booking.customer_id,
            vehicle_id=vehicle,
            vehicle_size_id=booking.vehicle_size_id,
            service_id=booking.service_id,
            resource_id=resource_id or booking.resource_id,
            responsible_user_id=responsible,
            channel=booking.channel,
            status=target,
            service_name_snapshot=booking.service_name_snapshot,
            base_price_cents=price,
            deposit_required_cents=booking.deposit_required_cents,
            scheduled_at=scheduled_at,
            arrived_at=arrived_at,
            arrival_delay_min=arrival_delay_min(scheduled_at, arrived_at),
            notes=notes,
        )
        try:
            async with guarded_savepoint(self._session, first_match(_EVENT_KEY, _QUOTE)):
                self._session.add(job)
                await self._session.flush()
                await self._record_received(job, key, arrived_at, booking=booking)
        except SavepointConflictError as exc:
            if exc.constraint == JOBS_QUOTE_UNIQUE:
                raise QuoteAlreadyUsedError("the quote is already used by another job") from exc
            # Con el turno bloqueado, un reenvío de ESTE turno ya se devolvió arriba: la
            # clave la usó otra operación.
            raise IdempotencyKeyReusedError(key) from exc
        booking.status = booking_target
        if booking.vehicle_id is None:
            booking.vehicle_id = vehicle
        # Las señas se cobran antes de que exista el job (R-O-030): ahora apuntan a él.
        for payment in await self._payments.lock_unlinked_for_booking(booking.id, self._tenant_id):
            payment.job_id = job.id
        await self._session.flush()
        return job

    async def _record_received(
        self, job: Job, key: str, arrived_at: datetime, *, booking: Booking | None
    ) -> None:
        metadata: dict[str, Any] = {"source": "BOOKING" if booking is not None else "WALK_IN"}
        if booking is not None:
            metadata["booking_id"] = str(booking.id)
        await self._events.add(
            JobEvent(
                tenant_id=self._tenant_id,
                job_id=job.id,
                event_type=_E.JOB_RECEIVED,
                from_status=None,
                to_status=job.status,
                occurred_at=arrived_at,
                actor_user_id=self._actor(),
                idempotency_key=key,
                event_metadata=metadata,
            )
        )

    async def _receive_walk_in(
        self,
        key: str,
        arrived_at: datetime,
        *,
        vehicle_id: uuid.UUID | None,
        vehicle_size_id: uuid.UUID | None,
        service_id: uuid.UUID | None,
        customer_id: uuid.UUID | None,
        channel: Channel | None,
        resource_id: uuid.UUID | None,
        quote_id: uuid.UUID | None,
        responsible: uuid.UUID,
        notes: str | None,
    ) -> Job:
        replayed = await self._replayed_receive(key)
        if replayed is not None:
            return replayed
        if vehicle_id is None or vehicle_size_id is None or service_id is None or channel is None:
            raise GuardFailedError("a walk-in needs vehicle, vehicle size, service and channel")
        await self._require(VehicleSizeRepository(self._session), vehicle_size_id)
        if customer_id is not None:
            await self._require(CustomerRepository(self._session), customer_id)
        service = await self._require(ServiceRepository(self._session), service_id)
        if service.pricing_mode == PricingMode.A_COTIZAR:
            price = await self._accepted_quote_price(
                quote_id, service_id, vehicle_size_id, booking_id=None
            )
        else:
            row = await ServicePriceRepository(self._session).find_for(
                service_id, vehicle_size_id, self._tenant_id
            )
            if row is None or row.price_cents is None:
                raise CatalogIncoherentError("the service has no price for that vehicle size")
            price = row.price_cents

        target = apply_event(None, _E.JOB_RECEIVED)
        assert target is not None
        job = Job(
            tenant_id=self._tenant_id,
            quote_id=quote_id if service.pricing_mode == PricingMode.A_COTIZAR else None,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            vehicle_size_id=vehicle_size_id,
            service_id=service_id,
            resource_id=resource_id,
            responsible_user_id=responsible,
            channel=channel,
            status=target,
            service_name_snapshot=service.name,
            base_price_cents=price,
            arrived_at=arrived_at,
            notes=notes,
        )
        try:
            async with guarded_savepoint(self._session, first_match(_EVENT_KEY, _QUOTE)):
                self._session.add(job)
                await self._session.flush()
                await self._record_received(job, key, arrived_at, booking=None)
        except SavepointConflictError as exc:
            # Carrera: otro reenvío con la misma clave comiteó primero. Gana el suyo (el
            # único de la cotización puede saltar antes que el de la clave: el job se inserta
            # primero, por eso se mira la clave antes de culpar a la cotización).
            replayed = await self._replayed_receive(key)
            if replayed is not None:
                return replayed
            if exc.constraint == JOBS_QUOTE_UNIQUE:
                raise QuoteAlreadyUsedError("the quote is already used by another job") from exc
            raise  # pragma: no cover  # clave ajena que la ganadora revirtió
        return job

    # ── Transiciones ──────────────────────────────────────────────────────────

    async def start(self, job_id: uuid.UUID, *, idempotency_key: str, occurred_at: datetime) -> Job:
        """`PRESENTE → EN_PROCESO`. No exige inspección (D-007)."""
        return await self._simple(
            job_id, _E.JOB_STARTED, idempotency_key, occurred_at, "started_at"
        )

    async def pick_up(
        self, job_id: uuid.UUID, *, idempotency_key: str, occurred_at: datetime
    ) -> Job:
        """`COBRADO → RETIRADO`. No es obligatorio: quedarse en `COBRADO` es "terminado sin
        retirar" (D-003). Con deuda no se retira (pregunta 4, hoy no)."""
        return await self._simple(
            job_id, _E.JOB_PICKED_UP, idempotency_key, occurred_at, "picked_up_at"
        )

    async def _simple(
        self,
        job_id: uuid.UUID,
        event_type: JobEventType,
        idempotency_key: str,
        occurred_at: datetime,
        timestamp_field: str,
    ) -> Job:
        await self._enter()
        key = require_key(idempotency_key)
        require_instant(occurred_at)
        job = await self._lock_job(job_id)
        if await self._replay(key, job.id, event_type) is not None:
            return job
        await self._record(
            job,
            event_type,
            key=key,
            occurred_at=occurred_at,
            changes={timestamp_field: occurred_at},
        )
        return job

    async def finish(
        self, job_id: uuid.UUID, *, idempotency_key: str, occurred_at: datetime
    ) -> Job:
        """`EN_PROCESO → FINALIZADO`, separado del cobro (**[corregir]** R-O-026). El turno
        pasa a `ATENDIDO`; si el saldo ya es 0, encadena `JOB_SETTLED`."""
        await self._enter()
        key = require_key(idempotency_key)
        require_instant(occurred_at)
        job = await self._lock_job(job_id)
        if await self._replay(key, job.id, _E.JOB_FINISHED) is not None:
            return job
        apply_event(job.status, _E.JOB_FINISHED)  # valida antes de tocar el turno
        booking = await self._lock_booking_of(job)
        booking_target = (
            next_booking_status(booking.status, BookingAction.JOB_FINISHED)
            if booking is not None
            else None
        )
        await self._record(
            job,
            _E.JOB_FINISHED,
            key=key,
            occurred_at=occurred_at,
            changes={"finished_at": occurred_at},
        )
        if booking is not None and booking_target is not None:
            booking.status = booking_target
        await self._settle_if_paid(job, key, occurred_at)
        await self._session.flush()
        return job

    async def cancel_for_delay(
        self,
        job_id: uuid.UUID,
        *,
        idempotency_key: str,
        occurred_at: datetime,
        tolerance_min: int,
        reason: str | None = None,
    ) -> Job:
        """`PRESENTE → CANCELADO_DEMORA` (acción humana, D-001.2).

        Guardas: el job tiene turno (un walk-in no se cancela por demora) y la demora,
        **recalculada con el `occurred_at` del evento**, supera la tolerancia (parámetro).
        Un `FINALIZADO` no se cancela (**[corregir]** R-O-016, C-7). El turno pasa a
        `CANCELADO_DEMORA` y se emite `DEPOSIT_RETAINED` con las señas cobradas (aunque sean
        0: la auditoría queda completa y la reversión tiene contra qué ir).

        F2: con cobros `SALDO` vivos (netos de devoluciones) se rechaza: primero se anulan o se
        devuelven. Si no, esa plata quedaría atrapada en un job que ya no cobra ni anula.
        """
        await self._enter()
        key = require_key(idempotency_key)
        require_instant(occurred_at)
        job = await self._lock_job(job_id)
        if await self._replay(key, job.id, _E.JOB_CANCELLED_DELAY) is not None:
            return job
        apply_event(job.status, _E.JOB_CANCELLED_DELAY)
        scheduled_at = as_aware(job.scheduled_at) if job.scheduled_at is not None else None
        delay = check_delay_cancellation(scheduled_at, occurred_at, tolerance_min)
        payments = await self._payments.list_for_job(job.id, self._tenant_id)
        live = [p for p in payments if p.voided_at is None]
        balance_paid = sum(p.amount_cents for p in live if p.kind == PaymentKind.SALDO) - sum(
            p.amount_cents for p in live if p.kind == PaymentKind.DEVOLUCION
        )
        if balance_paid > 0:
            raise GuardFailedError(
                "the job has live balance payments: void or refund them before cancelling "
                "for delay"
            )
        booking = await self._lock_booking_of(job)
        assert booking is not None  # scheduled_at NN ⇒ el job vino de un turno
        booking_target = next_booking_status(booking.status, BookingAction.JOB_CANCELLED_DELAY)
        metadata: dict[str, Any] = {"delay_min": delay, "tolerance_min": tolerance_min}
        if reason is not None and reason.strip():
            metadata["reason"] = reason.strip()
        await self._record(
            job, _E.JOB_CANCELLED_DELAY, key=key, occurred_at=occurred_at, metadata=metadata
        )
        booking.status = booking_target
        retained = deposits_paid(payments)
        await self._record(
            job,
            _E.DEPOSIT_RETAINED,
            key=retained_key(key),
            occurred_at=occurred_at,
            metadata={"amount_cents": retained},
        )
        await self._session.flush()
        return job

    async def reverse_retention(
        self, job_id: uuid.UUID, *, idempotency_key: str, occurred_at: datetime, reason: str
    ) -> Job:
        """`DEPOSIT_RETENTION_REVERSED`: motivo obligatorio (CHECK en la base) y actor. Exige
        una retención vigente; es un evento nuevo, nunca un `UPDATE` (D-001.5). Adónde va la
        plata (devolución, crédito) es la pregunta 5 del dueño: acá solo se audita."""
        await self._enter()
        key = require_key(idempotency_key)
        require_instant(occurred_at)
        job = await self._lock_job(job_id)
        if await self._replay(key, job.id, _E.DEPOSIT_RETENTION_REVERSED) is not None:
            return job
        apply_event(job.status, _E.DEPOSIT_RETENTION_REVERSED)
        cleaned = require_reason(reason)
        events = await self._events.list_for_job(job.id, self._tenant_id)
        if not retention_is_active(e.event_type for e in events):
            raise GuardFailedError("there is no active deposit retention to reverse")
        retained = next(e for e in reversed(events) if e.event_type == _E.DEPOSIT_RETAINED)
        await self._record(
            job,
            _E.DEPOSIT_RETENTION_REVERSED,
            key=key,
            occurred_at=occurred_at,
            metadata={
                "reason": cleaned,
                "amount_cents": retained.event_metadata.get("amount_cents", 0),
                "retained_event_id": str(retained.id),
            },
        )
        return job

    # ── Dinero ────────────────────────────────────────────────────────────────

    async def record_payment(
        self, job_id: uuid.UUID, payment: PaymentInput, *, kind: PaymentKind = PaymentKind.SALDO
    ) -> Payment:
        """Un cobro más (1:N, **[corregir]** R-O-026: un segundo cobro suma, no pisa).

        `SALDO` entra a caja; `DEVOLUCION` sale (resta del pagado). La seña es del turno
        (`BookingService.confirm_deposit`). Comisión con la tasa snapshot del medio. Si deja
        saldo 0 en `FINALIZADO`, encadena `JOB_SETTLED`. No en `CANCELADO_DEMORA`.

        Guardas de importe:

        - `SALDO` no supera el saldo pendiente (F8; el contrato deja "rechazar o confirmar" y
          la confirmación explícita es UI de F7). En `COBRADO`/`RETIRADO` el pendiente es 0.
        - `DEVOLUCION` no supera lo pagado neto vivo del job (F1), y en `COBRADO`/`RETIRADO`
          no puede dejar saldo > 0 (la guarda A1 de `void_payment`). Devolver un saldo a
          favor (negativo) hasta 0 sí se puede.
        """
        await self._enter()
        key = require_key(payment.idempotency_key)
        require_instant(payment.occurred_at)
        if kind == PaymentKind.SENA:
            raise GuardFailedError("a deposit is recorded on the booking, not on the job")
        job = await self._lock_job(job_id)
        event = await self._replay(key, job.id, _E.PAYMENT_RECORDED)
        if event is not None:
            return await self._payment_of(event)
        apply_event(job.status, _E.PAYMENT_RECORDED)
        await self._check_amount(job, kind, payment.amount_cents)
        method = await self._require(
            PaymentMethodRepository(self._session), payment.payment_method_id
        )
        recorded = await create_payment(
            self._session,
            self._tenant_id,
            self._actor(),
            method,
            payment,
            kind=kind,
            job_id=job.id,
        )
        await self._record(
            job,
            _E.PAYMENT_RECORDED,
            key=key,
            occurred_at=payment.occurred_at,
            metadata={
                "payment_id": str(recorded.id),
                "amount_cents": recorded.amount_cents,
                "kind": kind.value,
            },
        )
        await self._settle_if_paid(job, key, payment.occurred_at)
        await self._session.flush()
        return recorded

    async def _check_amount(self, job: Job, kind: PaymentKind, amount_cents: int) -> None:
        """Guardas de importe de `record_payment` (F1 y F8). El importe `<= 0` lo rechaza
        `create_payment`."""
        if amount_cents <= 0:
            return
        lines = await self._lines(job)
        total = total_agreed(job.base_price_cents, job.surcharge_cents, job.discount_cents)
        paid = net_paid(lines)
        due = total - paid
        if kind == PaymentKind.SALDO:
            if amount_cents > due:
                raise InvalidAmountError(
                    f"payment {amount_cents} exceeds the balance due {max(due, 0)}"
                )
            return
        if amount_cents > paid:
            raise InvalidAmountError(
                f"refund {amount_cents} exceeds the net paid on the job {max(paid, 0)}"
            )
        if job.status in _SETTLED_STATUSES and due + amount_cents > 0:
            raise GuardFailedError(
                f"this refund would leave a {job.status} job with a balance due (A1)"
            )

    async def _payment_of(self, event: JobEvent) -> Payment:
        payment_id = uuid.UUID(str(event.event_metadata["payment_id"]))
        return await self._require(self._payments, payment_id, include_voided=True)

    async def void_payment(
        self,
        payment_id: uuid.UUID,
        *,
        idempotency_key: str,
        occurred_at: datetime,
        reason: str,
        void_reason: VoidReason = VoidReason.ERROR_DE_CARGA,
    ) -> Payment:
        """Anula un pago de un job: `VoidableMixin` + `PAYMENT_VOIDED` con motivo.

        **A1**: si el job está `COBRADO`/`RETIRADO` y el saldo resultante sería > 0, se
        rechaza (no hay transición de vuelta y `jobs.status` mentiría). Anular un cobro
        duplicado, que deja saldo 0, sí se puede. Reabrir es la pregunta 11 del dueño.
        """
        await self._enter()
        key = require_key(idempotency_key)
        require_instant(occurred_at)
        cleaned = require_reason(reason)
        target = await self._require(self._payments, payment_id, include_voided=True)
        if target.job_id is None:
            raise GuardFailedError("only a job payment is voided here")
        job = await self._lock_job(target.job_id)
        event = await self._replay(key, job.id, _E.PAYMENT_VOIDED)
        if event is not None:
            return await self._payment_of(event)
        apply_event(job.status, _E.PAYMENT_VOIDED)
        payment = await self._payments.get_for_update(payment_id, self._tenant_id)
        if payment is None:
            raise GuardFailedError("the payment is already voided")
        if job.status in _SETTLED_STATUSES and await self._balance(job, without=payment.id) > 0:
            raise GuardFailedError(
                f"voiding this payment would leave a {job.status} job with a balance due (A1)"
            )
        await void_payment_row(self._session, self._tenant_id, payment, void_reason)
        await self._record(
            job,
            _E.PAYMENT_VOIDED,
            key=key,
            occurred_at=occurred_at,
            metadata={
                "payment_id": str(payment.id),
                "amount_cents": payment.amount_cents,
                "reason": cleaned,
            },
        )
        await self._settle_if_paid(job, key, occurred_at)
        await self._session.flush()
        return payment

    async def adjust_price(
        self,
        job_id: uuid.UUID,
        *,
        idempotency_key: str,
        occurred_at: datetime,
        surcharge_cents: int,
        discount_cents: int,
        reason: str,
    ) -> Job:
        """`PRICE_ADJUSTED`: fija recargo y descuento (separados, R-O-032) con motivo.

        Solo en `PRESENTE`, `EN_PROCESO`, `FINALIZADO` (A3). Si en `FINALIZADO` el ajuste
        deja el saldo en 0, encadena `JOB_SETTLED`: si no, el job quedaría sin salida.
        """
        await self._enter()
        key = require_key(idempotency_key)
        require_instant(occurred_at)
        job = await self._lock_job(job_id)
        if await self._replay(key, job.id, _E.PRICE_ADJUSTED) is not None:
            return job
        apply_event(job.status, _E.PRICE_ADJUSTED)
        cleaned = require_reason(reason)
        if surcharge_cents < 0 or discount_cents < 0:
            raise InvalidAmountError("surcharge and discount must be >= 0")
        total_agreed(job.base_price_cents, surcharge_cents, discount_cents)
        await self._record(
            job,
            _E.PRICE_ADJUSTED,
            key=key,
            occurred_at=occurred_at,
            metadata={
                "reason": cleaned,
                "previous_surcharge_cents": job.surcharge_cents,
                "previous_discount_cents": job.discount_cents,
                "surcharge_cents": surcharge_cents,
                "discount_cents": discount_cents,
            },
            changes={
                "surcharge_cents": surcharge_cents,
                "discount_cents": discount_cents,
                "discount_reason": cleaned if discount_cents > 0 else None,
            },
        )
        await self._settle_if_paid(job, key, occurred_at)
        await self._session.flush()
        return job

    # ── Inspección (opcional, D-007) ──────────────────────────────────────────

    async def record_inspection(
        self,
        job_id: uuid.UUID,
        *,
        idempotency_key: str,
        occurred_at: datetime,
        dirt_level: DirtLevel | None = None,
        pre_existing_damage: str | None = None,
        valuables: str | None = None,
        photo_consent: bool | None = None,
        checklist: dict[str, bool] | None = None,
    ) -> JobInspection:
        """`INSPECTION_RECORDED`. 0..1 por job: la segunda **actualiza** la existente.

        Decisión: la inspección es un formulario que el operador completa de a partes (llega
        el auto, después aparece un rayón); fallar obligaría a anular y recargar. Cada llamada
        trae el formulario **entero** (reemplaza todos los campos) y deja su evento, así la
        historia de cambios queda en `job_events`.
        """
        await self._enter()
        key = require_key(idempotency_key)
        require_instant(occurred_at)
        inspections = JobInspectionRepository(self._session)
        job = await self._lock_job(job_id)
        if await self._replay(key, job.id, _E.INSPECTION_RECORDED) is not None:
            existing = await inspections.find_by_job(job.id, self._tenant_id)
            assert existing is not None
            return existing
        apply_event(job.status, _E.INSPECTION_RECORDED)
        inspection = await inspections.find_by_job(job.id, self._tenant_id)
        updated = inspection is not None
        if inspection is None:
            inspection = JobInspection(tenant_id=self._tenant_id, job_id=job.id)
            self._session.add(inspection)
        inspection.dirt_level = dirt_level
        inspection.pre_existing_damage = pre_existing_damage
        inspection.valuables = valuables
        inspection.photo_consent = photo_consent
        inspection.checklist = checklist
        inspection.inspected_by_user_id = self._actor()
        inspection.inspected_at = occurred_at
        await self._session.flush()
        await self._record(
            job,
            _E.INSPECTION_RECORDED,
            key=key,
            occurred_at=occurred_at,
            metadata={"inspection_id": str(inspection.id), "updated": updated},
        )
        return inspection
