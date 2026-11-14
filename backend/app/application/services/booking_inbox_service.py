"""Bandeja del dueño: lecturas e incidencias — FASE-5-CONTRATO §4.1 (PR 5.3).

Las **transiciones** del turno (aprobar, rechazar, reprogramar, confirmar la seña, cancelar, no
asistió) viven en `BookingService`; la resolución de la seña, en `DepositResolutionService`. Acá va
lo que la bandeja **lee** (listas por estado y día, detalle, contador del menú) y la marca manual de
incidencia del cliente (D-014, §3.6).

Los días de los filtros son **hora local del negocio** (Z9, como bloqueos y aperturas en la adenda
5.1-1): el front no calcula zonas. Todo lo que recibe un id lo busca en el tenant del servicio: uno
ajeno es `NotFoundError`, igual que uno inexistente.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.application.services._base import ServiceBase, require_instant
from app.application.services.agenda_service import AgendaService, check_day
from app.application.services.errors import NotFoundError
from app.domain.deposit import DEPOSIT_OPEN_CASE_STATUSES
from app.domain.enums import BookingStatus
from app.domain.exceptions import GuardFailedError
from app.domain.void import VoidReason
from app.persistence.models.agenda import (
    AGENDA_REASON_MAX,
    Booking,
    BookingItem,
    CustomerIncidentMark,
)
from app.persistence.models.cancellation import Cancellation
from app.persistence.models.customer import Customer, Vehicle
from app.persistence.models.money import Payment
from app.persistence.models.tenant import Tenant
from app.persistence.repositories.agenda import BookingRepository
from app.persistence.repositories.base import Page
from app.persistence.repositories.cancellations import CancellationRepository
from app.persistence.repositories.inbox import (
    InboxBookingRepository,
    InboxCancellationRepository,
    InboxIncidentMarkRepository,
)
from app.persistence.repositories.money import PaymentRepository


@dataclass(frozen=True)
class BookingRow:
    """Un turno con lo que la bandeja muestra en la fila (sin N+1)."""

    booking: Booking
    customer: Customer | None
    vehicle: Vehicle | None
    items: list[BookingItem]
    #: Señas vivas cobradas.
    deposit_paid_cents: int


@dataclass(frozen=True)
class BookingDetail:
    row: BookingRow
    #: Pagos del turno (señas y devoluciones), anulados incluidos, por fecha.
    payments: list[Payment]
    cancellation: Cancellation | None
    #: Incidencias del cliente en los últimos 90 días (D-014): con 2 o más, el turnero lo manda a
    #: la lista de espera.
    incidents: int
    #: Marcas manuales vivas del cliente, la más nueva primero.
    marks: list[CustomerIncidentMark]


@dataclass(frozen=True)
class DepositCase:
    cancellation: Cancellation
    row: BookingRow


@dataclass(frozen=True)
class InboxCounts:
    #: `PENDIENTE_SEÑA` que todavía no empezaron.
    pending_deposit: int
    #: `PENDIENTE_REVISION` (lista de espera) que todavía no empezaron.
    in_review: int
    #: Casos de seña abiertos de cancelaciones (devolver, retener o reprogramar).
    deposit_cases: int

    @property
    def total(self) -> int:
        return self.pending_deposit + self.in_review + self.deposit_cases


def _reason(reason: str) -> str:
    cleaned = (reason or "").strip()
    if not cleaned:
        raise GuardFailedError("an incident mark needs a reason")
    if len(cleaned) > AGENDA_REASON_MAX:
        raise GuardFailedError(f"reason must have at most {AGENDA_REASON_MAX} chars")
    return cleaned


class BookingInboxService(ServiceBase):
    async def _timezone(self) -> ZoneInfo:
        tenant = await self._session.get(Tenant, self._tenant_id)
        if tenant is None:  # pragma: no cover  # el del token
            raise NotFoundError("tenants", self._tenant_id)
        return ZoneInfo(tenant.timezone)

    async def rows(self, bookings: Sequence[Booking]) -> list[BookingRow]:
        """Las filas de la bandeja de esos turnos (cliente, auto, servicios y seña cobrada)."""
        await self._enter()
        repo = InboxBookingRepository(self._session)
        ids = [b.id for b in bookings]
        customers = await repo.customers({b.customer_id for b in bookings}, self._tenant_id)
        vehicles = await repo.vehicles(
            {b.vehicle_id for b in bookings if b.vehicle_id is not None}, self._tenant_id
        )
        items = await repo.items(ids, self._tenant_id)
        paid = await repo.deposits_paid(ids, self._tenant_id)
        return [
            BookingRow(
                booking=b,
                customer=customers.get(b.customer_id),
                vehicle=vehicles.get(b.vehicle_id) if b.vehicle_id is not None else None,
                items=items.get(b.id, []),
                deposit_paid_cents=paid.get(b.id, 0),
            )
            for b in bookings
        ]

    # ── Listas y contador ─────────────────────────────────────────────────────

    async def list_bookings(
        self,
        *,
        statuses: Sequence[BookingStatus] | None = None,
        from_day: date | None = None,
        to_day: date | None = None,
        limit: int,
        offset: int,
    ) -> Page[BookingRow]:
        """Turnos vivos por estado y por día local de inicio (`to_day` inclusive), por inicio."""
        await self._enter()
        if from_day is not None and to_day is not None and to_day < from_day:
            raise GuardFailedError("to must be on or after from")
        tz = await self._timezone()
        starts_from = (
            datetime.combine(check_day(from_day), time(0), tzinfo=tz).astimezone(UTC)
            if from_day is not None
            else None
        )
        starts_before = (
            datetime.combine(check_day(to_day) + timedelta(days=1), time(0), tzinfo=tz).astimezone(
                UTC
            )
            if to_day is not None
            else None
        )
        page = await InboxBookingRepository(self._session).list_filtered(
            self._tenant_id,
            statuses=statuses,
            starts_from=starts_from,
            starts_before=starts_before,
            limit=limit,
            offset=offset,
        )
        return Page(items=await self.rows(page.items), total=page.total)

    async def counts(self, *, now: datetime) -> InboxCounts:
        """Lo que espera una decisión del negocio: el número del menú (§4.1)."""
        await self._enter()
        require_instant(now)
        bookings = InboxBookingRepository(self._session)
        return InboxCounts(
            pending_deposit=await bookings.count_upcoming(
                BookingStatus.PENDIENTE_SENA, now, self._tenant_id
            ),
            in_review=await bookings.count_upcoming(
                BookingStatus.PENDIENTE_REVISION, now, self._tenant_id
            ),
            deposit_cases=await InboxCancellationRepository(self._session).count_open(
                DEPOSIT_OPEN_CASE_STATUSES, self._tenant_id
            ),
        )

    async def list_deposit_cases(self, *, limit: int, offset: int) -> Page[DepositCase]:
        """Cancelaciones con la seña sin resolver, la más vieja primero ("Señas a resolver")."""
        await self._enter()
        page = await InboxCancellationRepository(self._session).list_open(
            DEPOSIT_OPEN_CASE_STATUSES, self._tenant_id, limit=limit, offset=offset
        )
        by_id = await InboxBookingRepository(self._session).by_ids(
            {c.booking_id for c in page.items}, self._tenant_id, include_voided=True
        )
        found: list[Booking] = []
        for cancellation in page.items:
            booking = by_id.get(cancellation.booking_id)
            if booking is None:  # pragma: no cover  # FK compuesta: el turno existe
                raise NotFoundError("bookings", cancellation.booking_id)
            found.append(booking)
        rows = await self.rows(found)
        return Page(
            items=[DepositCase(c, r) for c, r in zip(page.items, rows, strict=True)],
            total=page.total,
        )

    # ── Detalle ───────────────────────────────────────────────────────────────

    async def detail(self, booking_id: uuid.UUID, *, now: datetime) -> BookingDetail:
        await self._enter()
        require_instant(now)
        booking = await self._require(BookingRepository(self._session), booking_id)
        (row,) = await self.rows([booking])
        payments = await PaymentRepository(self._session).list_for_booking(
            booking.id, self._tenant_id
        )
        cancellation = await CancellationRepository(self._session).find_by_booking(
            booking.id, self._tenant_id
        )
        customer_alive = row.customer is not None and row.customer.voided_at is None
        incidents = (
            await AgendaService(
                self._session, self._tenant_id, self._actor_user_id
            ).count_incidents(booking.customer_id, now=now)
            if customer_alive
            else 0
        )
        marks = await InboxIncidentMarkRepository(self._session).list_for_customer(
            booking.customer_id, self._tenant_id
        )
        return BookingDetail(row, payments, cancellation, incidents, marks)

    # ── Marca manual de incidencia (D-014, §3.6) ──────────────────────────────

    async def mark_incident(self, booking_id: uuid.UUID, *, reason: str) -> CustomerIncidentMark:
        """Marca una incidencia del cliente del turno ("llegó tarde y se fue", "no avisó").

        Cuenta para la lista de espera como una cancelación tardía (`count_incidents`).
        """
        await self._enter()
        actor = self._actor()
        cleaned = _reason(reason)
        booking = await self._require(BookingRepository(self._session), booking_id)
        mark = CustomerIncidentMark(
            tenant_id=self._tenant_id,
            customer_id=booking.customer_id,
            booking_id=booking.id,
            reason=cleaned,
            created_by_user_id=actor,
        )
        self._session.add(mark)
        await self._session.flush()
        return mark

    async def void_incident_mark(self, mark_id: uuid.UUID) -> None:
        """ "Desmarcar" = anular (`PEDIDO_DEL_USUARIO`), nunca borrar (§3.6)."""
        await self._enter()
        self._actor()
        repo = InboxIncidentMarkRepository(self._session)
        if await repo.void(mark_id, self._tenant_id, VoidReason.PEDIDO_DEL_USUARIO) is None:
            raise NotFoundError("customer_incident_marks", mark_id)
