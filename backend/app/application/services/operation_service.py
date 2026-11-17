"""Agenda del día y turnos a mano — FASE-6-CONTRATO §3.1 y §3.3 (PR 6.1).

Lo que la pantalla de operación **lee** (el día por puesto, el detalle de un job con su saldo) y el
alta de un turno "solo a mano". Las **transiciones** del job (llegó, iniciar, finalizar, retirar,
cancelar por demora), los cobros y la carga retrospectiva viven en `JobService`; el alta del turno,
en `BookingService`: acá no hay una segunda máquina de estados.

Los días son **hora local del negocio** (`tenants.timezone`, §5): el front no calcula zonas. Todo lo
que recibe un id lo busca en el tenant del servicio: uno ajeno es `NotFoundError`, igual que uno
inexistente.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from app.application.services._base import ServiceBase, as_aware, require_instant, require_key
from app.application.services._payments import PaymentInput
from app.application.services.agenda_service import LocalRange, check_day
from app.application.services.booking_service import BookingService
from app.application.services.customer_service import CustomerService, VehicleService
from app.application.services.errors import AlreadyExistsError, NotFoundError
from app.application.services.job_service import JobService, RetroPayment
from app.domain import booking_code
from app.domain.deposit import balance_due, net_paid, total_agreed
from app.domain.enums import (
    BookingSource,
    BookingStatus,
    Channel,
    JobStatus,
)
from app.domain.exceptions import GuardFailedError
from app.domain.plate import normalize_plate
from app.persistence.models.agenda import Booking, BookingItem
from app.persistence.models.catalog import Resource
from app.persistence.models.customer import Customer, Vehicle
from app.persistence.models.job import Job, JobInspection, JobItem
from app.persistence.models.money import Payment
from app.persistence.models.tenant import Tenant
from app.persistence.repositories.agenda import BookingRepository
from app.persistence.repositories.catalog import PaymentMethodRepository
from app.persistence.repositories.inbox import InboxBookingRepository
from app.persistence.repositories.jobs import (
    JobEventRepository,
    JobInspectionRepository,
    JobItemRepository,
    JobRepository,
)
from app.persistence.repositories.money import PaymentRepository, deposits_paid
from app.persistence.repositories.operation import DayRepository

#: Tope de turnos y de jobs de un día: un lavadero hace decenas. No es paginación (un día es una
#: pantalla): si se pasa, la respuesta lo dice con `truncated` en vez de recortar en silencio.
DAY_CAP = 500
#: Turnos que se muestran en la agenda del día cuando todavía no tienen job: los que ocupan el
#: puesto o esperan la llegada. Los cancelados, vencidos y la lista de espera no son operación.
_DAY_BOOKING_STATUSES = (
    BookingStatus.PENDIENTE_SENA,
    BookingStatus.PENDIENTE_COTIZACION,
    BookingStatus.CONFIRMADO,
    BookingStatus.NO_ASISTIO,
)
#: Nombre del cliente cuando el dueño no lo sabe ("sin nombre", D6-3).
NO_NAME = "Sin nombre"


@dataclass(frozen=True)
class DayEntry:
    """Un renglón de la agenda del día: un turno todavía sin job, o un job."""

    kind: Literal["BOOKING", "JOB"]
    booking: Booking | None
    job: Job | None
    customer: Customer | None
    vehicle: Vehicle | None
    service_names: list[str]
    resource_id: uuid.UUID | None
    start_at: datetime
    end_at: datetime
    #: Total pactado: el del job, o el precio del turno (`None` si todavía no tiene precio).
    total_cents: int | None
    #: Pagado neto vivo (en un turno, las señas).
    paid_cents: int
    #: Saldo derivado (X9); `None` si el turno no tiene precio todavía.
    balance_due_cents: int | None
    #: Desde cuándo el servidor deja "cancelar por demora" (D6-2); `None` si no corresponde.
    cancel_delay_from: datetime | None

    @property
    def entry_id_uuid(self) -> uuid.UUID:
        """El id del job (`JOB`) o del turno (`BOOKING`)."""
        row = self.job if self.job is not None else self.booking
        assert row is not None
        return row.id

    @property
    def entry_id(self) -> str:
        """Desempate estable del orden del día."""
        return str(self.entry_id_uuid)


@dataclass(frozen=True)
class DayView:
    day: date
    timezone: str
    tolerance_min: int
    resources: list[Resource]
    entries: list[DayEntry]
    truncated: bool


@dataclass(frozen=True)
class JobDetail:
    job: Job
    items: list[JobItem]
    customer: Customer | None
    vehicle: Vehicle | None
    payments: list[Payment]
    booking_code: str | None
    total_cents: int
    paid_cents: int
    balance_due_cents: int
    tolerance_min: int
    cancel_delay_from: datetime | None
    inspection: JobInspection | None


@dataclass(frozen=True, slots=True)
class ManualDeposit:
    """La seña que el dueño ya cobró al cargar el turno. `amount_cents` `None` = la pedida."""

    payment_method_id: uuid.UUID
    amount_cents: int | None = None
    reference: str | None = None


@dataclass(frozen=True)
class ManualBooking:
    booking: Booking
    #: Señas vivas cobradas (0 si el turno nace `PENDIENTE_SEÑA` y todavía no se cobró).
    deposit_paid_cents: int


def cancel_delay_from(job: Job, tolerance_min: int) -> datetime | None:
    """El instante desde el que se puede cancelar por demora: la demora (en minutos enteros, floor)
    tiene que **superar** la tolerancia (`check_delay_cancellation`), o sea `tolerance + 1` min
    después del turno. Solo un job `PRESENTE` que vino de un turno; el servidor lo vuelve a
    validar con el `occurred_at` del toque."""
    if job.status != JobStatus.PRESENTE or job.scheduled_at is None:
        return None
    return as_aware(job.scheduled_at) + timedelta(minutes=tolerance_min + 1)


class OperationService(ServiceBase):
    async def _tenant(self) -> Tenant:
        tenant = await self._session.get(Tenant, self._tenant_id)
        if tenant is None or tenant.voided_at is not None:  # pragma: no cover  # el del token
            raise NotFoundError("tenants", self._tenant_id)
        return tenant

    # ── Agenda del día ────────────────────────────────────────────────────────

    async def day(self, day: date) -> DayView:
        """Turnos (sin job) y jobs del día local del negocio, por puesto y por hora."""
        await self._enter()
        tenant = await self._tenant()
        tolerance = (await self._settings()).tolerance_min
        starts, ends = LocalRange(check_day(day)).instants(ZoneInfo(tenant.timezone))
        repo = DayRepository(self._session)
        jobs = await repo.jobs_of_day(self._tenant_id, starts, ends, limit=DAY_CAP + 1)
        bookings = await repo.bookings_of_day(
            self._tenant_id, starts, ends, _DAY_BOOKING_STATUSES, limit=DAY_CAP + 1
        )
        truncated = len(jobs) > DAY_CAP or len(bookings) > DAY_CAP
        jobs, bookings = jobs[:DAY_CAP], bookings[:DAY_CAP]
        received = await repo.booking_ids_with_job(self._tenant_id, [b.id for b in bookings])
        bookings = [b for b in bookings if b.id not in received]

        inbox = InboxBookingRepository(self._session)
        customers = await inbox.customers(
            {j.customer_id for j in jobs if j.customer_id is not None}
            | {b.customer_id for b in bookings},
            self._tenant_id,
        )
        vehicles = await inbox.vehicles(
            {j.vehicle_id for j in jobs} | {b.vehicle_id for b in bookings if b.vehicle_id},
            self._tenant_id,
        )
        job_items = await repo.job_items(self._tenant_id, [j.id for j in jobs])
        job_payments = await repo.job_payments(self._tenant_id, [j.id for j in jobs])
        booking_items = await inbox.items([b.id for b in bookings], self._tenant_id)
        booking_deposits = await inbox.deposits_paid([b.id for b in bookings], self._tenant_id)

        entries = [
            self._job_entry(j, customers, vehicles, job_items, job_payments, tolerance)
            for j in jobs
        ] + [
            self._booking_entry(b, customers, vehicles, booking_items, booking_deposits)
            for b in bookings
        ]
        entries.sort(key=lambda e: (e.start_at, e.kind, e.entry_id))
        return DayView(
            day=day,
            timezone=tenant.timezone,
            tolerance_min=tolerance,
            resources=await repo.resources(self._tenant_id),
            entries=entries,
            truncated=truncated,
        )

    @staticmethod
    def _job_entry(
        job: Job,
        customers: dict[uuid.UUID, Customer],
        vehicles: dict[uuid.UUID, Vehicle],
        items: dict[uuid.UUID, list[JobItem]],
        payments: dict[uuid.UUID, list[Payment]],
        tolerance_min: int,
    ) -> DayEntry:
        lines = items.get(job.id, [])
        start = as_aware(job.scheduled_at or job.arrived_at)
        end = start + timedelta(minutes=sum(i.duration_min for i in lines))
        if job.finished_at is not None and job.scheduled_at is None:
            # Un walk-in o una carga retrospectiva dura lo que duró de verdad.
            end = max(as_aware(job.finished_at), start)
        total = total_agreed(job.base_price_cents, job.surcharge_cents, job.discount_cents)
        pay = [(p.kind, p.amount_cents, p.voided_at is not None) for p in payments.get(job.id, [])]
        return DayEntry(
            kind="JOB",
            booking=None,
            job=job,
            customer=customers.get(job.customer_id) if job.customer_id else None,
            vehicle=vehicles.get(job.vehicle_id),
            service_names=[i.service_name_snapshot for i in lines],
            resource_id=job.resource_id,
            start_at=start,
            end_at=end,
            total_cents=total,
            paid_cents=net_paid(pay),
            balance_due_cents=balance_due(total, pay),
            cancel_delay_from=cancel_delay_from(job, tolerance_min),
        )

    @staticmethod
    def _booking_entry(
        booking: Booking,
        customers: dict[uuid.UUID, Customer],
        vehicles: dict[uuid.UUID, Vehicle],
        items: dict[uuid.UUID, list[BookingItem]],
        deposits: dict[uuid.UUID, int],
    ) -> DayEntry:
        paid = deposits.get(booking.id, 0)
        price = booking.price_cents
        return DayEntry(
            kind="BOOKING",
            booking=booking,
            job=None,
            customer=customers.get(booking.customer_id),
            vehicle=vehicles.get(booking.vehicle_id) if booking.vehicle_id else None,
            service_names=[i.service_name_snapshot for i in items.get(booking.id, [])],
            resource_id=booking.resource_id,
            start_at=as_aware(booking.start_at),
            end_at=as_aware(booking.end_at),
            total_cents=price,
            paid_cents=paid,
            balance_due_cents=price - paid if price is not None else None,
            cancel_delay_from=None,
        )

    # ── Detalle de un job ─────────────────────────────────────────────────────

    async def job_detail(self, job_id: uuid.UUID) -> JobDetail:
        """El job con sus servicios, pagos y saldo (X9), y desde cuándo se puede cancelar por
        demora. Un job ajeno o inexistente es `NotFoundError`."""
        await self._enter()
        job = await self._require(JobRepository(self._session), job_id)
        tolerance = (await self._settings()).tolerance_min
        inbox = InboxBookingRepository(self._session)
        customers = await inbox.customers(
            {job.customer_id} if job.customer_id else set(), self._tenant_id
        )
        vehicles = await inbox.vehicles({job.vehicle_id}, self._tenant_id)
        payments = await PaymentRepository(self._session).list_for_job(job.id, self._tenant_id)
        total = total_agreed(job.base_price_cents, job.surcharge_cents, job.discount_cents)
        lines = [(p.kind, p.amount_cents, p.voided_at is not None) for p in payments]
        booking = (
            (await inbox.by_ids([job.booking_id], self._tenant_id, include_voided=True)).get(
                job.booking_id
            )
            if job.booking_id
            else None
        )
        return JobDetail(
            job=job,
            items=await JobItemRepository(self._session).list_for_job(job.id, self._tenant_id),
            customer=customers.get(job.customer_id) if job.customer_id else None,
            vehicle=vehicles.get(job.vehicle_id),
            payments=payments,
            booking_code=booking.code if booking is not None else None,
            total_cents=total,
            paid_cents=net_paid(lines),
            balance_due_cents=balance_due(total, lines),
            tolerance_min=tolerance,
            cancel_delay_from=cancel_delay_from(job, tolerance),
            inspection=await JobInspectionRepository(self._session).find_by_job(
                job.id, self._tenant_id
            ),
        )

    # ── Turno "solo a mano" ───────────────────────────────────────────────────

    async def create_manual_booking(
        self,
        *,
        idempotency_key: str,
        now: datetime,
        start_at: datetime,
        resource_id: uuid.UUID,
        vehicle_size_id: uuid.UUID,
        service_ids: Sequence[uuid.UUID],
        customer_name: str | None = None,
        customer_phone: str | None = None,
        plate: str | None = None,
        channel: Channel = Channel.CARGA_MANUAL,
        notes: str | None = None,
        deposit: ManualDeposit | None = None,
    ) -> ManualBooking:
        """Un turno que el dueño carga a mano (fuente `AGENDA_INTERNA`, código `INT-…`).

        Cliente por teléfono (si lo hay; si no, uno nuevo) y "sin nombre" si no sabe cómo se llama;
        patente opcional. Reusa `BookingService.create`: el `EXCLUDE` decide el choque
        (`SlotTakenError`) y el turno nace `PENDIENTE_SEÑA`, con su hold, si el negocio pide seña.
        La anticipación mínima del turnero **no** rige acá (`enforce_advance=False`), pero el turno
        tiene que ser a futuro: lo que ya pasó es una carga retrospectiva.

        `deposit`: la seña que el cliente ya pagó. Registra el pago con `confirm_deposit` y deja el
        turno `CONFIRMADO`; sin `deposit` queda esperando la seña. Pasarla con un turno que no pide
        seña es `GuardFailedError`.

        Idempotencia: la clave del pago (`:deposit`) es la del llamador; el alta en sí la protege
        la ruta con `claim_idempotency_key`, como el resto de la bandeja.
        """
        await self._enter()
        key = require_key(idempotency_key)
        require_instant(start_at)
        require_instant(now)
        if start_at < now:
            raise GuardFailedError(
                "a manual booking must start in the future: use a retrospective job for the past"
            )
        if deposit is not None:
            # Antes de crear nada: un medio ajeno o inexistente no puede dejar un turno a medias.
            await self._require(PaymentMethodRepository(self._session), deposit.payment_method_id)
        customer_id = await self._customer(customer_name, customer_phone, channel, required=True)
        assert customer_id is not None
        vehicle_id = await self._vehicle(plate, customer_id, vehicle_size_id, anonymous=False)
        bookings = BookingService(self._session, self._tenant_id, self._actor_user_id)
        for _ in range(booking_code.MAX_CODE_ATTEMPTS):
            try:
                booking = await bookings.create(
                    code=booking_code.new_internal_booking_code(),
                    source=BookingSource.AGENDA_INTERNA,
                    channel=channel,
                    resource_id=resource_id,
                    start_at=start_at,
                    customer_id=customer_id,
                    service_ids=service_ids,
                    vehicle_size_id=vehicle_size_id,
                    now=now,
                    vehicle_id=vehicle_id,
                    notes=notes,
                    enforce_advance=False,
                )
                break
            except AlreadyExistsError:
                continue
        else:
            raise AlreadyExistsError("could not allocate a free booking code")
        paid = 0
        if deposit is not None:
            if booking.status != BookingStatus.PENDIENTE_SENA:
                raise GuardFailedError("the booking does not ask for a deposit")
            amount = deposit.amount_cents or booking.deposit_required_cents
            if booking.price_cents is not None and amount > booking.price_cents:
                raise GuardFailedError("the deposit cannot exceed the booking total")
            confirmation = await bookings.confirm_deposit(
                booking.id,
                PaymentInput(
                    amount_cents=amount,
                    payment_method_id=deposit.payment_method_id,
                    idempotency_key=f"{key}:deposit",
                    occurred_at=now,
                    reference=deposit.reference,
                ),
                now=now,
            )
            booking = confirmation.booking
            paid = deposits_paid([confirmation.payment])
        return ManualBooking(booking=booking, deposit_paid_cents=paid)

    # ── Cliente y auto de un ingreso ──────────────────────────────────────────

    async def _customer(
        self, name: str | None, phone: str | None, channel: Channel, *, required: bool
    ) -> uuid.UUID | None:
        """El cliente por teléfono o uno nuevo; "sin nombre" si no sabe cómo se llama. Sin nombre
        ni teléfono, `None` (el job puede quedar sin cliente, D6-3) salvo que `required`."""
        cleaned = (name or "").strip()
        if not cleaned and not (phone or "").strip() and not required:
            return None
        resolution = await CustomerService(
            self._session, self._tenant_id, self._actor_user_id
        ).resolve_by_phone(phone, name=cleaned or NO_NAME, channel=channel)
        return resolution.customer.id

    async def _vehicle(
        self,
        plate: str | None,
        customer_id: uuid.UUID | None,
        vehicle_size_id: uuid.UUID,
        *,
        anonymous: bool,
    ) -> uuid.UUID | None:
        """El auto por patente (opcional, D6-3). Con patente nueva queda vinculado al cliente.
        Sin patente: `None`, o un auto anónimo nuevo si `anonymous` (un job siempre tiene auto)."""
        if normalize_plate(plate) is None and not anonymous:
            return None
        vehicles = VehicleService(self._session, self._tenant_id, self._actor_user_id)
        resolution = await vehicles.resolve_by_plate(plate, vehicle_size_id=vehicle_size_id)
        if resolution.created and customer_id is not None:
            await vehicles.link_customer(customer_id, resolution.vehicle.id, is_primary=True)
        return resolution.vehicle.id

    # ── Recibir ───────────────────────────────────────────────────────────────

    async def receive_job(
        self,
        *,
        idempotency_key: str,
        arrived_at: datetime,
        booking_id: uuid.UUID | None = None,
        vehicle_id: uuid.UUID | None = None,
        plate: str | None = None,
        vehicle_size_id: uuid.UUID | None = None,
        service_ids: Sequence[uuid.UUID] = (),
        customer_id: uuid.UUID | None = None,
        customer_name: str | None = None,
        customer_phone: str | None = None,
        channel: Channel | None = None,
        resource_id: uuid.UUID | None = None,
        quote_id: uuid.UUID | None = None,
        notes: str | None = None,
    ) -> Job:
        """`JobService.receive` para la pantalla: lo mismo, pero el auto y el cliente se dan por
        patente, nombre y teléfono (no hay otro lugar donde cargarlos), así que se resuelven acá.

        Con `booking_id` el turno trae su cliente, y su auto si lo tiene; si no, el de `vehicle_id`
        o `plate`, o uno anónimo (la web no pide patente y el job sí). Sin turno, el canal por
        defecto es `CALLE`. Un reenvío de la misma clave **no** crea autos ni clientes de más: la
        resolución se saltea cuando el job ya existe.
        """
        await self._enter()
        key = require_key(idempotency_key)
        jobs = JobService(self._session, self._tenant_id, self._actor_user_id)
        replayed = await JobEventRepository(self._session).get_by_key(key, self._tenant_id)
        if replayed is None and booking_id is not None:
            booking = await self._require(BookingRepository(self._session), booking_id)
            already = await JobRepository(self._session).find_by_booking(
                booking.id, self._tenant_id
            )
            if already is None and vehicle_id is None and booking.vehicle_id is None:
                vehicle_id = await self._vehicle(
                    plate, booking.customer_id, booking.vehicle_size_id, anonymous=True
                )
            elif already is None and vehicle_id is None and normalize_plate(plate) is not None:
                vehicle_id = await self._vehicle(
                    plate, booking.customer_id, booking.vehicle_size_id, anonymous=False
                )
        elif replayed is None:
            channel = channel or Channel.CALLE
            if customer_id is None:
                customer_id = await self._customer(
                    customer_name, customer_phone, channel, required=False
                )
            if vehicle_id is None and vehicle_size_id is not None:
                vehicle_id = await self._vehicle(
                    plate, customer_id, vehicle_size_id, anonymous=True
                )
        return await jobs.receive(
            idempotency_key=key,
            arrived_at=arrived_at,
            booking_id=booking_id,
            vehicle_id=vehicle_id,
            vehicle_size_id=vehicle_size_id,
            service_ids=service_ids,
            customer_id=customer_id,
            channel=channel,
            resource_id=resource_id,
            quote_id=quote_id,
            notes=notes,
        )

    # ── Carga retrospectiva ───────────────────────────────────────────────────

    async def record_retrospective_job(
        self,
        *,
        idempotency_key: str,
        started_at: datetime,
        finished_at: datetime,
        now: datetime,
        vehicle_size_id: uuid.UUID,
        service_ids: Sequence[uuid.UUID],
        customer_name: str | None = None,
        customer_phone: str | None = None,
        plate: str | None = None,
        channel: Channel = Channel.CARGA_MANUAL,
        resource_id: uuid.UUID | None = None,
        notes: str | None = None,
        payment: RetroPayment | None = None,
    ) -> Job:
        """`JobService.record_retrospective` con el cliente ("sin nombre" posible) y el auto
        (patente opcional) resueltos por nombre, teléfono y patente. Un reenvío de la misma
        clave devuelve el job ya cargado, sin crear autos ni clientes de más."""
        await self._enter()
        key = require_key(idempotency_key)
        jobs = JobService(self._session, self._tenant_id, self._actor_user_id)
        replayed = await JobEventRepository(self._session).get_by_key(
            f"{key}:received", self._tenant_id
        )
        customer_id: uuid.UUID | None = None
        vehicle_id: uuid.UUID | None = None
        if replayed is None:
            customer_id = await self._customer(
                customer_name, customer_phone, channel, required=False
            )
            vehicle_id = await self._vehicle(plate, customer_id, vehicle_size_id, anonymous=True)
        return await jobs.record_retrospective(
            idempotency_key=key,
            started_at=started_at,
            finished_at=finished_at,
            now=now,
            vehicle_id=vehicle_id,
            vehicle_size_id=vehicle_size_id,
            service_ids=service_ids,
            customer_id=customer_id,
            channel=channel,
            resource_id=resource_id,
            notes=notes,
            payment=payment,
        )
