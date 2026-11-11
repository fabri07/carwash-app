"""Agenda del negocio — FASE-5-CONTRATO §3, §4.1 y §5 (PR 5.1).

- **Bloqueos y aperturas especiales:** alta, listado de los vigentes y anulación. Un bloqueo **no
  cancela** los turnos que pisa (R-T-052): el alta los devuelve para que la pantalla avise.
- **Disponibilidad:** arma los datos del motor (`domain/availability.py`) desde la base y lo
  corre. Lo que ocupa: turnos vivos en `CONFIRMADO`, `RECIBIDO` y `PENDIENTE_SEÑA` con hold
  vigente (el repositorio aplica el mismo comparador que `hold_expired`).
- **Incidencias (D-014):** cuántas tiene un cliente en los últimos 90 días; con 2 o más, el pedido
  del turnero queda en lista de espera (5.2).

Todo lo que recibe un id lo busca en el tenant del servicio: un id ajeno es `NotFoundError`,
igual que uno inexistente.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.application.services._base import ServiceBase, as_aware, require_instant
from app.application.services._items import catalog_items
from app.application.services.booking_service import BookingService
from app.application.services.errors import NotFoundError
from app.domain.availability import BusinessHour, FreeStart, RuleRow, Span, free_starts
from app.domain.enums import BookingStatus, CancellationClassification
from app.domain.exceptions import GuardFailedError
from app.domain.void import VoidReason
from app.persistence.models.agenda import (
    AGENDA_REASON_MAX,
    Booking,
    ScheduleBlock,
    ScheduleOpening,
)
from app.persistence.models.catalog import BusinessHours, Resource
from app.persistence.models.tenant import Tenant
from app.persistence.repositories.agenda import (
    BookingRepository,
    CustomerIncidentMarkRepository,
    ScheduleBlockRepository,
    ScheduleOpeningRepository,
)
from app.persistence.repositories.base import Page
from app.persistence.repositories.cancellations import CancellationRepository
from app.persistence.repositories.catalog import (
    BusinessHoursRepository,
    ResourceRepository,
    ServiceScheduleRuleRepository,
    VehicleSizeRepository,
)
from app.persistence.repositories.customers import CustomerRepository

#: D-014: las incidencias que cuentan son las de los últimos 90 días.
INCIDENT_WINDOW = timedelta(days=90)
#: Cancelaciones del cliente que cuentan como incidencia. `NORMAL` no; `OPERATIVA` nunca (es del
#: negocio).
INCIDENT_CANCELLATIONS = (
    CancellationClassification.TARDIA,
    CancellationClassification.POSTERIOR_AL_TURNO,
)
#: Estados finales del turno que cuentan como incidencia.
INCIDENT_BOOKING_STATUSES = (BookingStatus.CANCELADO_DEMORA, BookingStatus.NO_ASISTIO)
#: Turnos que siguen en pie: los que un bloqueo nuevo deja adentro (hay que avisarle al cliente).
ACTIVE_BOOKING_STATUSES = (
    BookingStatus.PENDIENTE_SENA,
    BookingStatus.PENDIENTE_COTIZACION,
    BookingStatus.PENDIENTE_REVISION,
    BookingStatus.CONFIRMADO,
    BookingStatus.RECIBIDO,
)
_ALL_ROWS = 10_000  # puestos y franjas de un negocio: decenas, se leen enteros


#: Tope de un bloqueo o una apertura: un año (vacaciones largas entran; un typo de año, no).
MAX_SPAN = timedelta(days=366)


@dataclass(frozen=True)
class LocalRange:
    """Un rango en hora local del negocio (Z9), como lo carga el dueño.

    Sin horas = día completo (`[00:00, 00:00 del día siguiente)`, modelo de `schedule_blocks`).
    `end_day` (inclusive) permite varios días; sin `end_time`, termina al final de `end_day`.
    """

    day: date
    end_day: date | None = None
    start_time: time | None = None
    end_time: time | None = None

    def instants(self, tz: ZoneInfo) -> tuple[datetime, datetime]:
        last = self.end_day or self.day
        starts = datetime.combine(self.day, self.start_time or time(0), tzinfo=tz)
        if self.end_time is None:
            ends = datetime.combine(last + timedelta(days=1), time(0), tzinfo=tz)
        else:
            ends = datetime.combine(last, self.end_time, tzinfo=tz)
        if ends <= starts:
            raise GuardFailedError("the range must end after it starts")
        if ends - starts > MAX_SPAN:
            raise GuardFailedError("the range cannot be longer than a year")
        # En UTC: los instantes viajan y se guardan en UTC (Z9; SQLite pierde la zona).
        return starts.astimezone(UTC), ends.astimezone(UTC)


@dataclass(frozen=True)
class BlockCreated:
    block: ScheduleBlock
    #: Turnos activos que quedan dentro del bloqueo, por inicio. No se tocan (R-T-052).
    overlapping: list[Booking]
    #: Nombre del cliente de cada turno de `overlapping` (para avisarle).
    customer_names: dict[uuid.UUID, str]


@dataclass(frozen=True)
class Availability:
    timezone: str
    #: Duración total de los servicios para el tamaño (snapshot del catálogo de hoy).
    duration_min: int
    starts: list[FreeStart]


def _reason(reason: str | None) -> str | None:
    """Motivo limpio: `None` si viene vacío; más de `AGENDA_REASON_MAX` es `GuardFailedError`."""
    cleaned = (reason or "").strip()
    if not cleaned:
        return None
    if len(cleaned) > AGENDA_REASON_MAX:
        raise GuardFailedError(f"reason must have at most {AGENDA_REASON_MAX} chars")
    return cleaned


def _span(row: ScheduleBlock | ScheduleOpening) -> Span:
    return Span(row.resource_id, as_aware(row.starts_at), as_aware(row.ends_at))


def _page[T](rows: list[T], *, limit: int, offset: int) -> Page[T]:
    return Page(items=rows[offset : offset + limit], total=len(rows))


class AgendaService(ServiceBase):
    # ── Bloqueos ──────────────────────────────────────────────────────────────

    async def list_blocks(self, *, now: datetime, limit: int, offset: int) -> Page[ScheduleBlock]:
        """Bloqueos vivos que todavía no terminaron (`ends_at > now`), por inicio."""
        await self._enter()
        require_instant(now)
        rows = await ScheduleBlockRepository(self._session).list_overlapping(
            now, None, self._tenant_id
        )
        return _page(rows, limit=limit, offset=offset)

    async def create_block(
        self,
        when: LocalRange,
        *,
        resource_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> BlockCreated:
        """Alta de un bloqueo (del puesto o de todo el lavadero) y los turnos activos que pisa.

        Reusa `BookingService.add_block` (R-T-013). Los turnos no se cancelan: el negocio decide
        (avisarles, reprogramar en la bandeja de 5.3).
        """
        await self._enter()
        actor = self._actor()
        starts_at, ends_at = when.instants(await self._timezone())
        block = await BookingService(self._session, self._tenant_id, actor).add_block(
            starts_at=starts_at, ends_at=ends_at, resource_id=resource_id, reason=_reason(reason)
        )
        overlapping = await BookingRepository(self._session).list_with_status_overlapping(
            starts_at,
            ends_at,
            ACTIVE_BOOKING_STATUSES,
            self._tenant_id,
            resource_id=resource_id,
        )
        customers = CustomerRepository(self._session)
        names: dict[uuid.UUID, str] = {}
        for booking in overlapping:
            if booking.customer_id not in names:
                customer = await customers.get_by_id(
                    booking.customer_id, self._tenant_id, include_voided=True
                )
                names[booking.customer_id] = customer.name if customer is not None else ""
        return BlockCreated(block, overlapping, names)

    async def void_block(self, id: uuid.UUID) -> None:
        """Desactivar = anular con `DESACTIVADO` (modelo de `schedule_blocks`)."""
        await self._enter()
        self._actor()
        repo = ScheduleBlockRepository(self._session)
        if await repo.void(id, self._tenant_id, VoidReason.DESACTIVADO) is None:
            raise NotFoundError(repo.model.__tablename__, id)

    # ── Aperturas especiales ──────────────────────────────────────────────────

    async def list_openings(
        self, *, now: datetime, limit: int, offset: int
    ) -> Page[ScheduleOpening]:
        """Aperturas vivas que todavía no terminaron (`ends_at > now`), por inicio."""
        await self._enter()
        require_instant(now)
        rows = await ScheduleOpeningRepository(self._session).list_overlapping(
            now, None, self._tenant_id
        )
        return _page(rows, limit=limit, offset=offset)

    async def create_opening(
        self,
        when: LocalRange,
        *,
        resource_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> ScheduleOpening:
        """Alta de una apertura especial (§3.3): suma una ventana fuera del horario habitual."""
        await self._enter()
        actor = self._actor()
        starts_at, ends_at = when.instants(await self._timezone())
        if resource_id is not None:
            await self._require(ResourceRepository(self._session), resource_id)
        opening = ScheduleOpening(
            tenant_id=self._tenant_id,
            resource_id=resource_id,
            starts_at=starts_at,
            ends_at=ends_at,
            created_by_user_id=actor,
        )
        cleaned = _reason(reason)
        if cleaned is not None:
            opening.reason = cleaned
        self._session.add(opening)
        await self._session.flush()
        return opening

    async def void_opening(self, id: uuid.UUID) -> None:
        await self._enter()
        self._actor()
        repo = ScheduleOpeningRepository(self._session)
        if await repo.void(id, self._tenant_id, VoidReason.DESACTIVADO) is None:
            raise NotFoundError(repo.model.__tablename__, id)

    # ── Disponibilidad ────────────────────────────────────────────────────────

    async def _timezone(self) -> ZoneInfo:
        tenant = await self._session.get(Tenant, self._tenant_id)
        if tenant is None or tenant.voided_at is not None:  # pragma: no cover  # el del token
            raise NotFoundError("tenants", self._tenant_id)
        return ZoneInfo(tenant.timezone)

    async def availability(
        self,
        *,
        day: date,
        vehicle_size_id: uuid.UUID,
        service_ids: Sequence[uuid.UUID],
        now: datetime,
    ) -> Availability:
        """Los inicios libres de `day` para esos servicios y ese tamaño (§5).

        La duración es la suma de las del catálogo para el tamaño (como `BookingService.create`).
        Un servicio sin precio para el tamaño es `CatalogIncoherentError`; uno ajeno o anulado,
        `NotFoundError`.
        """
        await self._enter()
        require_instant(now)
        await self._require(VehicleSizeRepository(self._session), vehicle_size_id)
        settings = await self._settings()
        items = await catalog_items(
            self._session, self._tenant_id, service_ids, vehicle_size_id, deposit_bps=0
        )
        duration = sum(item.duration_min for item in items)
        tz = await self._timezone()
        day_start = datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC)
        day_end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz).astimezone(UTC)

        hours = await BusinessHoursRepository(self._session).list_by_tenant(
            self._tenant_id, limit=_ALL_ROWS, order_by=(BusinessHours.weekday.asc(),)
        )
        rules_rows = await ServiceScheduleRuleRepository(self._session).list_for_services(
            [item.service_id for item in items], self._tenant_id
        )
        rules = [
            [
                RuleRow(r.weekday, r.window_start, r.last_start)
                for r in rules_rows
                if r.service_id == item.service_id
            ]
            for item in items
        ]
        openings = await ScheduleOpeningRepository(self._session).list_overlapping(
            day_start, day_end, self._tenant_id
        )
        blocks = await ScheduleBlockRepository(self._session).list_overlapping(
            day_start, day_end, self._tenant_id
        )
        occupied: dict[uuid.UUID, list[tuple[datetime, datetime]]] = {}
        for booking in await BookingRepository(self._session).list_occupying(
            day_start, day_end, now, self._tenant_id
        ):
            occupied.setdefault(booking.resource_id, []).append(
                (as_aware(booking.start_at), as_aware(booking.end_at))
            )
        resources = await ResourceRepository(self._session).list_by_tenant(
            self._tenant_id,
            limit=_ALL_ROWS,
            order_by=(Resource.sort_order.asc(), Resource.name.asc()),
        )
        starts = free_starts(
            day=day,
            tz=tz,
            now=now,
            business_hours=[BusinessHour(h.weekday, h.opens_at, h.closes_at) for h in hours.items],
            rules=rules,
            openings=[_span(o) for o in openings],
            blocks=[_span(b) for b in blocks],
            occupied=occupied,
            resources=[r.id for r in resources.items],
            duration_min=duration,
            min_advance_min=settings.min_advance_min,
            horizon_days=settings.booking_horizon_days,
            step_min=settings.slot_step_min,
        )
        return Availability(timezone=tz.key, duration_min=duration, starts=starts)

    # ── Incidencias (D-014) ───────────────────────────────────────────────────

    async def count_incidents(self, customer_id: uuid.UUID, *, now: datetime) -> int:
        """Incidencias del cliente en los últimos 90 días (`now` es la hora del server).

        Cuentan: cancelaciones `TARDIA` y `POSTERIOR_AL_TURNO` (por `requested_at`), turnos
        `CANCELADO_DEMORA` y `NO_ASISTIO` (por `start_at`) y marcas manuales vivas (por
        `created_at`). `OPERATIVA` nunca; lo anulado tampoco.
        """
        await self._enter()
        require_instant(now)
        await self._require(CustomerRepository(self._session), customer_id)
        since = now - INCIDENT_WINDOW
        cancellations = await CancellationRepository(self._session).list_for_customer_since(
            customer_id, INCIDENT_CANCELLATIONS, since, self._tenant_id
        )
        bookings = await BookingRepository(self._session).list_for_customer_with_status_since(
            customer_id, INCIDENT_BOOKING_STATUSES, since, self._tenant_id
        )
        marks = await CustomerIncidentMarkRepository(self._session).list_for_customer_since(
            customer_id, since, self._tenant_id
        )
        return len(cancellations) + len(bookings) + len(marks)
