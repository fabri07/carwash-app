"""Turnero público — FASE-5-CONTRATO §4.2, §6 y D5-1..D5-9 (PR 5.2).

Lo que ve y hace un cliente **sin cuenta** en `/<slug>`: el negocio (nombre, marca, textos), el
catálogo reservable, los horarios libres de un día, reservar, ver su turno y cancelarlo.

- **Sin actor** (`actor_user_id=None`): nadie del negocio está mirando. Solo se usan operaciones
  que no lo exigen (`BookingService.create` y `cancel_by_client`, `CustomerService`,
  `VehicleService`, la disponibilidad y las incidencias de `AgendaService`).
- **El tenant** lo resuelve la API con `find_public_tenant_id` (función `SECURITY DEFINER`, Z7);
  acá entra como siempre y `_enter()` abre el contexto RLS.
- **El motor ofrece, el `EXCLUDE` decide** (Z5/Z6): el `POST` valida el inicio contra
  `free_starts` con las reglas del turnero (horizonte y anticipación) y después prueba los puestos
  libres en `sort_order` (D5-6); si todos chocan, `SlotTakenError`.
- **No se pisa al cliente** (§4.2 paso 4): si el WhatsApp ya es de un cliente, su nombre queda; el
  tipeado va a las notas del turno.
- **Ver o cancelar exige código + WhatsApp** (D5-8): cualquier diferencia es el mismo
  `NotFoundError` que un código inexistente.

Nunca devuelve datos de otro cliente: la disponibilidad son horas, el turno es el propio.
"""

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.idempotency import claim_idempotency_key
from app.application.services._base import ServiceBase, as_aware, require_instant, require_key
from app.application.services._items import (
    deposit_bps_snapshot,
    item_deposit_bps,
    require_service_ids,
)
from app.application.services.agenda_service import AgendaService, Availability
from app.application.services.booking_service import BookingService
from app.application.services.customer_service import CustomerService, VehicleService
from app.application.services.errors import (
    AlreadyExistsError,
    IdempotencyKeyReusedError,
    NotFoundError,
    SlotTakenError,
)
from app.domain import booking_code
from app.domain.booking_state import can_client_cancel
from app.domain.deposit import deposit_required
from app.domain.enums import (
    BookingSource,
    BookingStatus,
    Channel,
    PricingMode,
    ServiceBookingMode,
)
from app.domain.exceptions import DomainError, GuardFailedError
from app.domain.phone import normalize_phone_ar
from app.domain.plate import normalize_plate
from app.persistence.models.agenda import Booking, BookingItem
from app.persistence.models.cancellation import Cancellation
from app.persistence.models.catalog import Service, ServiceCategory, ServicePrice, VehicleSize
from app.persistence.models.idempotency_key import IdempotencyKey
from app.persistence.models.tenant import Tenant, TenantSettings
from app.persistence.repositories.agenda import BookingItemRepository, BookingRepository
from app.persistence.repositories.catalog import (
    ServiceCategoryRepository,
    ServicePriceRepository,
    ServiceRepository,
    VehicleSizeRepository,
)
from app.persistence.repositories.customers import CustomerRepository

#: Acción de `idempotency_keys` del alta pública. Al crear el turno pasa a
#: `public_booking:<id>`: así un reenvío con la misma clave devuelve el mismo turno.
PUBLIC_BOOKING_ACTION = "public_booking"
#: D5-4: turnos activos a futuro por cliente (mismo WhatsApp). Un tercero lo carga el negocio.
MAX_ACTIVE_BOOKINGS = 2
#: D-014 / D5-9: con estas incidencias en 90 días el pedido va a la lista de espera.
REVIEW_INCIDENTS = 2
#: Largo de `terms_version`: los primeros hex del SHA-256 de la política vigente (§4.2 paso 8).
TERMS_VERSION_LENGTH = 12
#: Un turno cancelado no se "repite" con la misma clave: el cliente lo canceló, otro pedido es otro.
CANCELLED_BOOKING_STATUSES = frozenset(
    {
        BookingStatus.CANCELADO_CLIENTE,
        BookingStatus.AUSENTE_CON_AVISO_POSTERIOR,
        BookingStatus.CANCELADO_OPERATIVO,
    }
)
_ALL_ROWS = 10_000  # tamaños, categorías y servicios de un negocio: decenas


class ServiceNotBookableError(GuardFailedError):
    """El servicio no se reserva desde la web: no existe en el negocio, está anulado, es
    `SOLO_A_MANO`, es `A_COTIZAR` (D5-5) o no tiene precio para el tamaño. 422
    `SERVICE_NOT_BOOKABLE`."""


class InvalidPhoneError(GuardFailedError):
    """El WhatsApp no se puede normalizar a E.164 sin adivinar (inválido o ambiguo). 422
    `INVALID_PHONE`: el turnero no crea clientes sin teléfono."""


class TooManyActiveBookingsError(DomainError):
    """El cliente ya tiene `MAX_ACTIVE_BOOKINGS` turnos activos a futuro (D5-4). 409."""


@dataclass(frozen=True, slots=True)
class PublicProfile:
    tenant: Tenant
    settings: TenantSettings


@dataclass(frozen=True, slots=True)
class PublicPrice:
    vehicle_size_id: uuid.UUID
    price_cents: int
    duration_min: int
    #: Seña de este servicio solo, para ese tamaño. 0 en `A_COTIZAR` (D4-7) o con la seña apagada.
    deposit_cents: int


@dataclass(frozen=True, slots=True)
class PublicService:
    id: uuid.UUID
    name: str
    category_name: str | None
    pricing_mode: PricingMode
    #: `False` en `A_COTIZAR`: se muestra con "desde $X" y "Consultar por WhatsApp" (D5-5).
    bookable: bool
    #: % de seña efectivo (D4-17): el del servicio o el del negocio; 0 con la seña apagada.
    deposit_bps: int
    prices: list[PublicPrice]


@dataclass(frozen=True, slots=True)
class PublicCatalog:
    vehicle_sizes: list[VehicleSize]
    services: list[PublicService]


@dataclass(frozen=True, slots=True)
class PublicBookingRequest:
    vehicle_size_id: uuid.UUID
    service_ids: Sequence[uuid.UUID]
    start_at: datetime
    name: str
    phone: str
    plate: str | None = None
    accept_policy: bool = False


@dataclass(frozen=True, slots=True)
class PublicBookingView:
    booking: Booking
    items: list[BookingItem]
    vehicle_size_label: str
    timezone: str

    @property
    def can_cancel(self) -> bool:
        return can_client_cancel(self.booking.status)

    @property
    def deposit_cents(self) -> int:
        """Seña a pagar. En lista de espera no se pide (D5-9): se recalcula al aprobar."""
        if self.booking.status == BookingStatus.PENDIENTE_REVISION:
            return 0
        return self.booking.deposit_required_cents


@dataclass(frozen=True, slots=True)
class PublicCancellation:
    booking: Booking
    cancellation: Cancellation


def terms_version_of(policy: str | None) -> str | None:
    """Versión de la política aceptada: los primeros 12 hex del SHA-256 del texto vigente. Sin
    política cargada no hay versión (los TyC versionados son v2, D-017)."""
    if not policy:
        return None
    return hashlib.sha256(policy.encode("utf-8")).hexdigest()[:TERMS_VERSION_LENGTH]


class PublicBookingService(ServiceBase):
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        super().__init__(session, tenant_id, None)

    # ── Lecturas ──────────────────────────────────────────────────────────────

    async def profile(self) -> PublicProfile:
        """El negocio y su configuración (lo que muestra la cabecera del turnero)."""
        await self._enter()
        tenant = await self._session.get(Tenant, self._tenant_id)
        if tenant is None or tenant.voided_at is not None:  # pragma: no cover  # lo filtró el slug
            raise NotFoundError("tenants", self._tenant_id)
        return PublicProfile(tenant, await self._settings())

    async def catalog(self) -> PublicCatalog:
        """Tamaños y servicios vivos que no son `SOLO_A_MANO`, con precio, duración y seña por
        tamaño. Un `A_COTIZAR` aparece con su precio base ("desde") pero no se reserva."""
        await self._enter()
        settings = await self._settings()
        business_bps = deposit_bps_snapshot(settings)
        sizes = await VehicleSizeRepository(self._session).list_by_tenant(
            self._tenant_id,
            limit=_ALL_ROWS,
            order_by=(VehicleSize.sort_order.asc(), VehicleSize.label.asc()),
        )
        categories = await ServiceCategoryRepository(self._session).list_by_tenant(
            self._tenant_id,
            limit=_ALL_ROWS,
            order_by=(ServiceCategory.sort_order.asc(), ServiceCategory.name.asc()),
        )
        category_order = {c.id: (i, c.name) for i, c in enumerate(categories.items)}
        services = await ServiceRepository(self._session).list_by_tenant(
            self._tenant_id, limit=_ALL_ROWS, order_by=(Service.name.asc(),)
        )
        size_ids = {s.id for s in sizes.items}
        # Una consulta para todos los precios (antes, una por servicio).
        by_service: dict[uuid.UUID, list[ServicePrice]] = {}
        for price in await ServicePriceRepository(self._session).list_for_services(
            [service.id for service in services.items], self._tenant_id
        ):
            by_service.setdefault(price.service_id, []).append(price)
        out: list[tuple[tuple[int, str], PublicService]] = []
        for service in services.items:
            if service.booking_mode == ServiceBookingMode.SOLO_A_MANO:
                continue
            bps = item_deposit_bps(business_bps, service.deposit_bps)
            quoted = service.pricing_mode == PricingMode.A_COTIZAR
            rows = [
                row for row in by_service.get(service.id, []) if row.vehicle_size_id in size_ids
            ]
            if not rows:
                continue  # sin precio para ningún tamaño no se puede ofrecer
            order, category = category_order.get(
                service.category_id or uuid.UUID(int=0), (len(category_order), None)
            )
            out.append(
                (
                    (order, service.name.casefold()),
                    PublicService(
                        id=service.id,
                        name=service.name,
                        category_name=category,
                        pricing_mode=service.pricing_mode,
                        bookable=not quoted,
                        deposit_bps=bps,
                        prices=[
                            PublicPrice(
                                vehicle_size_id=row.vehicle_size_id,
                                price_cents=row.price_cents,
                                duration_min=row.duration_min,
                                deposit_cents=0
                                if quoted
                                else deposit_required(row.price_cents, bps),
                            )
                            for row in rows
                        ],
                    ),
                )
            )
        out.sort(key=lambda pair: pair[0])
        return PublicCatalog(list(sizes.items), [service for _, service in out])

    async def availability(
        self,
        *,
        day: date,
        vehicle_size_id: uuid.UUID,
        service_ids: Sequence[uuid.UUID],
        now: datetime,
    ) -> Availability:
        """Los inicios libres de `day` con las reglas del turnero (horizonte y anticipación,
        adenda 5.1-13). Un servicio que no se reserva desde la web es `ServiceNotBookableError`."""
        await self._enter()
        await self._require_bookable(service_ids, vehicle_size_id)
        return await self._agenda().availability(
            day=day,
            vehicle_size_id=vehicle_size_id,
            service_ids=service_ids,
            now=now,
            public=True,
        )

    # ── Reservar ──────────────────────────────────────────────────────────────

    async def book(
        self, request: PublicBookingRequest, *, idempotency_key: str, now: datetime
    ) -> PublicBookingView:
        """El `POST` del turnero (§4.2): todo en la transacción del llamador.

        Devuelve el turno nuevo, o el mismo de antes si la clave ya se usó con el mismo
        WhatsApp (doble envío del formulario). La clave usada con otro WhatsApp u otra operación
        es `IdempotencyKeyReusedError`.
        """
        await self._enter()
        key = require_key(idempotency_key)
        require_instant(now)
        start_at = require_instant(request.start_at)
        if not request.accept_policy:
            raise GuardFailedError("the cancellation policy must be accepted")
        phone = normalize_phone_ar(request.phone)
        if phone.e164 is None:
            raise InvalidPhoneError("the WhatsApp number is not a valid Argentine mobile")
        if not await claim_idempotency_key(
            self._session, self._tenant_id, key, PUBLIC_BOOKING_ACTION
        ):
            return await self._replayed(key, phone.e164, request)

        await self._require_bookable(request.service_ids, request.vehicle_size_id)
        settings = await self._settings()
        tz = await self._timezone()
        # Z6: el `EXCLUDE` no ve horario, reglas ni bloqueos; el motor sí.
        offered = await self._agenda().availability(
            day=start_at.astimezone(tz).date(),
            vehicle_size_id=request.vehicle_size_id,
            service_ids=request.service_ids,
            now=now,
            public=True,
        )
        slot = next((s for s in offered.starts if s.start_at == start_at), None)
        if slot is None:
            raise SlotTakenError("the start is not among the free starts")

        resolution = await CustomerService(self._session, self._tenant_id, None).resolve_by_phone(
            request.phone, name=request.name, channel=Channel.TURNERO_WEB
        )
        customer = resolution.customer
        typed_name = request.name.strip()
        notes = None
        if not resolution.created and typed_name.casefold() != customer.name.casefold():
            notes = f"Nombre en la web: {typed_name}"
        # D5-4 atómico: el lock de la fila del cliente serializa los pedidos simultáneos del mismo
        # WhatsApp hasta que cada transacción confirma; el segundo cuenta después del primero.
        await self._lock(CustomerRepository(self._session), customer.id)
        bookings = BookingRepository(self._session)
        active = await bookings.count_active_for_customer(customer.id, now, self._tenant_id)
        if active >= MAX_ACTIVE_BOOKINGS:
            raise TooManyActiveBookingsError(
                f"the customer already has {MAX_ACTIVE_BOOKINGS} active bookings"
            )
        vehicle_id = await self._vehicle(request, customer.id)
        review = await self._agenda().count_incidents(customer.id, now=now) >= REVIEW_INCIDENTS
        # En lista de espera no ocupa: se guarda el primer puesto libre y se reelige al aprobar.
        resources = slot.resource_ids[:1] if review else slot.resource_ids
        booking = await self._create(
            resources,
            start_at=start_at,
            customer_id=customer.id,
            request=request,
            vehicle_id=vehicle_id,
            notes=notes,
            terms_version=terms_version_of(settings.cancellation_policy),
            now=now,
            for_review=review,
        )
        await self._session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.tenant_id == self._tenant_id, IdempotencyKey.key == key)
            .values(action=f"{PUBLIC_BOOKING_ACTION}:{booking.id}")
        )
        return await self._view(booking)

    async def _create(
        self,
        resources: Sequence[uuid.UUID],
        *,
        start_at: datetime,
        customer_id: uuid.UUID,
        request: PublicBookingRequest,
        vehicle_id: uuid.UUID | None,
        notes: str | None,
        terms_version: str | None,
        now: datetime,
        for_review: bool,
    ) -> Booking:
        """D5-6: el primer puesto libre por `sort_order`; si el `EXCLUDE` choca (otro cliente
        ganó la carrera), el siguiente. Un código repetido se reintenta con otro (§4.2 paso 7)."""
        service = BookingService(self._session, self._tenant_id, None)
        for resource_id in resources:
            for _ in range(booking_code.MAX_CODE_ATTEMPTS):
                try:
                    return await service.create(
                        code=booking_code.new_booking_code(),
                        source=BookingSource.TURNERO_WEB,
                        channel=Channel.TURNERO_WEB,
                        resource_id=resource_id,
                        start_at=start_at,
                        customer_id=customer_id,
                        service_ids=request.service_ids,
                        vehicle_size_id=request.vehicle_size_id,
                        vehicle_id=vehicle_id,
                        now=now,
                        notes=notes,
                        terms_version=terms_version,
                        terms_accepted_at=now,
                        for_review=for_review,
                    )
                except SlotTakenError:
                    break
                except AlreadyExistsError:
                    continue
            else:
                raise AlreadyExistsError("could not allocate a free booking code")
        raise SlotTakenError("every free resource was taken meanwhile")

    async def _vehicle(
        self, request: PublicBookingRequest, customer_id: uuid.UUID
    ) -> uuid.UUID | None:
        """La patente es opcional (D5-3). Si es nueva, el auto queda vinculado al cliente; si ya
        existe, el turno la referencia pero **no** se vincula: cualquiera puede tipear una patente
        ajena (§6, como el nombre)."""
        if normalize_plate(request.plate) is None:  # vacía, `SIN000` o sin letras ni números
            return None
        vehicles = VehicleService(self._session, self._tenant_id, None)
        resolution = await vehicles.resolve_by_plate(
            request.plate, vehicle_size_id=request.vehicle_size_id
        )
        if resolution.created:
            await vehicles.link_customer(customer_id, resolution.vehicle.id, is_primary=True)
        return resolution.vehicle.id

    async def _replayed(
        self, key: str, phone_e164: str, request: PublicBookingRequest
    ) -> PublicBookingView:
        """Reenvío de una clave ya usada: devuelve el turno **solo si es el mismo pedido** (mismo
        WhatsApp, horario, tamaño y servicios) y sigue en pie. Con otro cuerpo, o si ya se
        canceló, es `IdempotencyKeyReusedError` (409 `DUPLICATE_IDEMPOTENT`): devolver el turno
        original como si fuera el pedido nuevo sería mentirle al cliente."""
        row = await self._session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.tenant_id == self._tenant_id, IdempotencyKey.key == key
            )
        )
        prefix = f"{PUBLIC_BOOKING_ACTION}:"
        if row is None or not row.action.startswith(prefix):
            raise IdempotencyKeyReusedError(key)
        booking = await BookingRepository(self._session).get_by_id(
            uuid.UUID(row.action.removeprefix(prefix)), self._tenant_id, include_voided=True
        )
        if booking is None or not await self._belongs_to(booking, phone_e164):
            raise IdempotencyKeyReusedError(key)
        items = await BookingItemRepository(self._session).list_for_booking(
            booking.id, self._tenant_id
        )
        same_request = (
            as_aware(booking.start_at) == request.start_at
            and booking.vehicle_size_id == request.vehicle_size_id
            and {item.service_id for item in items} == set(request.service_ids)
        )
        if not same_request or booking.status in CANCELLED_BOOKING_STATUSES:
            raise IdempotencyKeyReusedError(key)
        return await self._view(booking)

    # ── Ver y cancelar (D5-8) ─────────────────────────────────────────────────

    async def lookup(self, code: str, phone: str) -> PublicBookingView:
        await self._enter()
        return await self._view(await self._find(code, phone))

    async def cancel(
        self, code: str, phone: str, *, reason: str | None, now: datetime
    ) -> PublicCancellation:
        """`BookingService.cancel_by_client` sin cambios (clasificación D-005, caso de la seña,
        idempotente). `now` es la hora del server: decide la clasificación."""
        await self._enter()
        booking = await self._find(code, phone)
        cancellation = await BookingService(self._session, self._tenant_id, None).cancel_by_client(
            booking.id, now=now, reason=reason
        )
        return PublicCancellation(booking, cancellation)

    async def _find(self, code: str, phone: str) -> Booking:
        """El turno del código **y** del WhatsApp. Cualquier diferencia (código mal escrito,
        inexistente, de otro negocio o de otro cliente) es el mismo `NotFoundError`."""
        normalized = booking_code.normalize_booking_code(code)
        e164 = normalize_phone_ar(phone).e164
        booking = (
            await BookingRepository(self._session).find_by_code(normalized, self._tenant_id)
            if normalized is not None and e164 is not None
            else None
        )
        if booking is None or e164 is None or not await self._belongs_to(booking, e164):
            raise NotFoundError("bookings", "code")
        return booking

    async def _belongs_to(self, booking: Booking, phone_e164: str) -> bool:
        customer = await CustomerRepository(self._session).get_by_id(
            booking.customer_id, self._tenant_id, include_voided=True
        )
        return customer is not None and customer.phone_e164 == phone_e164

    # ── Piezas ────────────────────────────────────────────────────────────────

    def _agenda(self) -> AgendaService:
        return AgendaService(self._session, self._tenant_id, None)

    async def _timezone(self) -> ZoneInfo:
        tenant = await self._session.get(Tenant, self._tenant_id)
        assert tenant is not None  # lo resolvió el slug en esta transacción
        return ZoneInfo(tenant.timezone)

    async def _require_bookable(
        self, service_ids: Sequence[uuid.UUID], vehicle_size_id: uuid.UUID
    ) -> None:
        """§4.2 paso 2: vivos, del negocio, no `SOLO_A_MANO`, no `A_COTIZAR` y con precio para el
        tamaño. Un tamaño ajeno o anulado es `GuardFailedError` (422, no 404: el 404 del turnero
        es solo el del negocio y el del turno)."""
        ids = require_service_ids(service_ids)
        if (
            await VehicleSizeRepository(self._session).get_by_id(vehicle_size_id, self._tenant_id)
            is None
        ):
            raise GuardFailedError("unknown vehicle size")
        services = ServiceRepository(self._session)
        prices = ServicePriceRepository(self._session)
        for service_id in ids:
            service = await services.get_by_id(service_id, self._tenant_id)
            if (
                service is None
                or service.booking_mode == ServiceBookingMode.SOLO_A_MANO
                or service.pricing_mode == PricingMode.A_COTIZAR
                or await prices.find_for(service_id, vehicle_size_id, self._tenant_id) is None
            ):
                raise ServiceNotBookableError("the service cannot be booked from the web")

    async def _view(self, booking: Booking) -> PublicBookingView:
        items = await BookingItemRepository(self._session).list_for_booking(
            booking.id, self._tenant_id
        )
        size = await VehicleSizeRepository(self._session).get_by_id(
            booking.vehicle_size_id, self._tenant_id, include_voided=True
        )
        tz = await self._timezone()
        return PublicBookingView(
            booking=booking,
            items=items,
            vehicle_size_label=size.label if size is not None else "",
            timezone=tz.key,
        )


__all__ = [
    "InvalidPhoneError",
    "PublicBookingRequest",
    "PublicBookingService",
    "PublicBookingView",
    "PublicCancellation",
    "PublicCatalog",
    "PublicProfile",
    "ServiceNotBookableError",
    "TooManyActiveBookingsError",
    "terms_version_of",
]
