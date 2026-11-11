"""Servicios y categorías del negocio — FASE-4-CONTRATO §3.3 y §4 (PR 4.3).

Amplía `CatalogService` (que ya sabe de `set_price`, `check_price` y `_insert_unique`) con el ABM
completo de **categorías** y **servicios**, la búsqueda y el vínculo con el glosario.

Reglas que viven acá:

- **Todo servicio nace con precio para cada tamaño vivo** (D4-6, A8). `PUT /services/{id}/prices`
  y la creación exigen un precio por **cada** tamaño vivo del negocio: si falta alguno, 422 con la
  lista de los que faltan. Ningún precio queda NULL ni queda un tamaño sin cubrir.
- **El `PATCH` de un servicio no toca precios** ni la modalidad: eso es `replace_prices`.
- **`search_key`** = `normalize(name)`, se recalcula al crear y al renombrar.
- Anular una categoría con servicios vivos es `IN_USE`. Anular un servicio anula también sus
  precios vivos (si no, el tamaño no se podría anular nunca: `void_vehicle_size` los cuenta).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select

from app.application.services._base import alive_unique, first_match
from app.application.services.catalog_service import CatalogService, check_price
from app.application.services.errors import (
    AlreadyExistsError,
    GlossaryCodeTakenError,
    InUseError,
)
from app.domain.enums import BookingStatus, JobStatus, PricingMode, QuoteStatus
from app.domain.exceptions import InvalidAmountError
from app.domain.glossary import load_glossary
from app.domain.search import match_tier, normalize
from app.domain.void import VoidReason
from app.persistence.db._savepoint import SavepointConflictError, guarded_savepoint
from app.persistence.models.agenda import Booking, BookingItem
from app.persistence.models.catalog import Service, ServiceCategory, ServicePrice, VehicleSize
from app.persistence.models.job import Job, JobItem
from app.persistence.models.quote import Quote
from app.persistence.repositories.base import Page
from app.persistence.repositories.catalog import (
    ServiceCategoryRepository,
    ServicePriceRepository,
    ServiceRepository,
)
from app.utils.datetime_utils import utcnow

_NAME_UNIQUE = "ux_services_tenant_id_name"
_GLOSSARY_UNIQUE = "ux_services_tenant_id_glossary_code"
_SERVICE_UNIQUES = first_match(
    alive_unique("services", "name"), alive_unique("services", "glossary_code")
)
_ALL_ROWS = 10_000  # un negocio tiene decenas de servicios: se filtra y ordena en Python (Y8)


@dataclass(frozen=True)
class PriceInput:
    """Un precio base servicio × tamaño, ya validado por forma (> 0) en el schema."""

    vehicle_size_id: uuid.UUID
    price_cents: int
    duration_min: int


#: Quién "usa" un servicio y por eso no se puede anular (como `void_resource` con los puestos):
#: turnos que todavía no cerraron, cotizaciones sin resolver y jobs abiertos (sin retirar).
OPEN_BOOKING_STATUSES = (
    BookingStatus.PENDIENTE_SENA,
    BookingStatus.PENDIENTE_COTIZACION,
    BookingStatus.CONFIRMADO,
    BookingStatus.RECIBIDO,
)
OPEN_QUOTE_STATUSES = (QuoteStatus.PENDIENTE, QuoteStatus.COTIZADO)
OPEN_JOB_STATUSES = (
    JobStatus.PRESENTE,
    JobStatus.EN_PROCESO,
    JobStatus.FINALIZADO,
    JobStatus.COBRADO,
)


@dataclass(frozen=True)
class ServiceView:
    service: Service
    prices: list[ServicePrice]
    #: Tamaños vivos sin precio vivo (se sumó un tamaño después de cargar el servicio): la UI
    #: los marca. Reservar con ese tamaño falla hasta cargar el precio (§6).
    missing_price_size_ids: list[uuid.UUID]


@dataclass(frozen=True)
class PriceCoverage:
    """Cobertura de precios de todo el catálogo vivo (la usa el alta, FASE-4-CONTRATO §6)."""

    size_ids: tuple[uuid.UUID, ...]
    service_ids: tuple[uuid.UUID, ...]
    #: Servicios con algún `missing_price_size_ids`: la misma regla que `ServiceView`.
    unpriced_service_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True)
class ServiceIndex:
    """Los servicios vivos del negocio, por `glossary_code` y por `search_key` (nombre)."""

    by_code: dict[str, Service]
    by_key: dict[str, Service]


def _aliases(service: Service) -> tuple[str, ...]:
    """Sinónimos del glosario del servicio, **ya normalizados** (vacío si es propio)."""
    if service.glossary_code is None:
        return ()
    return load_glossary().aliases_for(service.glossary_code)


class ServiceCatalogService(CatalogService):
    # ── helpers ───────────────────────────────────────────────────────────────────────────

    async def _live_sizes(self) -> dict[uuid.UUID, str]:
        rows = await self._session.execute(
            select(VehicleSize.id, VehicleSize.code)
            .where(VehicleSize.tenant_id == self._tenant_id, VehicleSize.voided_at.is_(None))
            .order_by(VehicleSize.sort_order, VehicleSize.code)
        )
        return {row.id: row.code for row in rows}

    @staticmethod
    def sorted_by_size(
        prices: Sequence[PriceInput], live: dict[uuid.UUID, str]
    ) -> list[PriceInput]:
        """Los precios en el orden de los tamaños vivos (`sort_order`, código)."""
        position = {size_id: i for i, size_id in enumerate(live)}
        return sorted(prices, key=lambda p: position.get(p.vehicle_size_id, len(position)))

    @staticmethod
    def _missing_sizes(
        live: dict[uuid.UUID, str], prices: Sequence[ServicePrice]
    ) -> list[uuid.UUID]:
        have = {p.vehicle_size_id for p in prices}
        return [size_id for size_id in live if size_id not in have]

    @staticmethod
    def _check_coverage(prices: Sequence[PriceInput], live: dict[uuid.UUID, str]) -> None:
        """Un precio válido por **cada** tamaño vivo, ni uno más ni uno menos (A8, D4-6).

        Pura: recibe los tamaños vivos ya leídos, así un lote (la plantilla) los trae una vez.
        """
        given = [p.vehicle_size_id for p in prices]
        if len(set(given)) != len(given):
            raise InvalidAmountError("a vehicle size has more than one price")
        unknown = [str(i) for i in given if i not in live]
        if unknown:
            raise InvalidAmountError(f"unknown vehicle size(s): {', '.join(unknown)}")
        missing = sorted(code for size_id, code in live.items() if size_id not in set(given))
        if missing:
            raise InvalidAmountError(
                f"a price is required for every vehicle size; missing: {', '.join(missing)}"
            )
        for price in prices:
            check_price(price.price_cents)
            if price.duration_min <= 0:
                raise InvalidAmountError(f"duration must be > 0: {price.duration_min}")

    async def _prices_by_service(
        self, service_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[ServicePrice]]:
        grouped: dict[uuid.UUID, list[ServicePrice]] = {i: [] for i in service_ids}
        if not service_ids:
            return grouped
        rows = await self._session.scalars(
            select(ServicePrice)
            .join(
                VehicleSize,
                (VehicleSize.tenant_id == ServicePrice.tenant_id)
                & (VehicleSize.id == ServicePrice.vehicle_size_id),
            )
            .where(
                ServicePrice.tenant_id == self._tenant_id,
                ServicePrice.voided_at.is_(None),
                ServicePrice.service_id.in_(service_ids),
            )
            # Mismo orden que los tamaños del negocio: el alta y la lectura devuelven lo mismo.
            .order_by(VehicleSize.sort_order, VehicleSize.code)
        )
        for price in rows:
            grouped[price.service_id].append(price)
        return grouped

    async def price_coverage(self) -> PriceCoverage:
        """Tamaños y servicios vivos, y qué servicios no tienen precio para algún tamaño vivo.

        Tres consultas: tamaños, servicios y precios, con los mismos `_live_sizes`,
        `_prices_by_service` y `_missing_sizes` que arman `missing_price_size_ids`.
        """
        await self._enter()
        live = await self._live_sizes()
        ids = list(
            await self._session.scalars(
                select(Service.id)
                .where(Service.tenant_id == self._tenant_id, Service.voided_at.is_(None))
                .order_by(Service.name, Service.id)
            )
        )
        prices = await self._prices_by_service(ids)
        return PriceCoverage(
            size_ids=tuple(live),
            service_ids=tuple(ids),
            unpriced_service_ids=tuple(i for i in ids if self._missing_sizes(live, prices[i])),
        )

    async def _view(self, service: Service) -> ServiceView:
        prices = (await self._prices_by_service([service.id]))[service.id]
        return ServiceView(service, prices, self._missing_sizes(await self._live_sizes(), prices))

    async def _insert_service(self, service: Service) -> Service:
        try:
            async with guarded_savepoint(self._session, _SERVICE_UNIQUES):
                self._session.add(service)
        except SavepointConflictError as exc:
            if exc.constraint == _GLOSSARY_UNIQUE:
                raise GlossaryCodeTakenError(
                    f"a live service already uses glossary code {service.glossary_code}"
                ) from exc
            raise AlreadyExistsError("services: ('name',) already taken") from exc
        return service

    async def insert_priced(self, service: Service, prices: Sequence[PriceInput]) -> ServiceView:
        """Inserta el servicio y todos sus precios de una vez. **No valida cobertura**: el
        llamador ya lo hizo (`_check_coverage`). Un servicio nuevo no tiene precios previos, así
        que no hace falta leer ni bloquear nada por precio."""
        inserted = await self._insert_service(service)
        rows = [
            ServicePrice(
                tenant_id=self._tenant_id,
                service_id=inserted.id,
                vehicle_size_id=p.vehicle_size_id,
                price_cents=p.price_cents,
                duration_min=p.duration_min,
            )
            for p in prices
        ]
        self._session.add_all(rows)
        await self._session.flush()
        return ServiceView(inserted, rows, [])

    # ── categorías ────────────────────────────────────────────────────────────────────────

    async def list_categories(self, *, limit: int, offset: int) -> Page[ServiceCategory]:
        return await self._list(
            ServiceCategoryRepository(self._session),
            limit=limit,
            offset=offset,
            order_by=(ServiceCategory.sort_order.asc(), ServiceCategory.name.asc()),
        )

    async def get_category(self, id: uuid.UUID) -> ServiceCategory:
        return await self._get(ServiceCategoryRepository(self._session), id)

    async def create_category(self, name: str, sort_order: int = 0) -> ServiceCategory:
        await self._enter()
        return await self._insert_unique(
            ServiceCategory(tenant_id=self._tenant_id, name=name, sort_order=sort_order), "name"
        )

    async def update_category(
        self, id: uuid.UUID, *, name: str | None = None, sort_order: int | None = None
    ) -> ServiceCategory:
        await self._enter()
        category = await self._lock(ServiceCategoryRepository(self._session), id)
        try:
            async with guarded_savepoint(self._session, alive_unique("service_categories", "name")):
                if name is not None:
                    category.name = name
                if sort_order is not None:
                    category.sort_order = sort_order
        except SavepointConflictError as exc:
            raise AlreadyExistsError("service_categories: ('name',) already taken") from exc
        return category

    async def void_category(self, id: uuid.UUID) -> None:
        """No se anula una categoría con servicios vivos: quedarían apuntando a una anulada."""
        await self._enter()
        category = await self._require(ServiceCategoryRepository(self._session), id)
        in_use = await self._session.scalar(
            select(func.count())
            .select_from(Service)
            .where(
                Service.tenant_id == self._tenant_id,
                Service.category_id == category.id,
                Service.voided_at.is_(None),
            )
        )
        if in_use:
            raise InUseError("the category still has services: move or remove them first")
        await self._void(ServiceCategoryRepository(self._session), id)

    async def find_or_create_category(
        self, name: str, sort_order: int
    ) -> tuple[ServiceCategory, bool]:
        """La categoría viva con ese nombre, o la crea. El `bool` dice si la creó.

        Idempotente por nombre (la usa la plantilla): ante la carrera de dos altas simultáneas,
        el único la rechaza y se devuelve la que ganó.
        """
        await self._enter()
        existing = await self._session.scalar(
            select(ServiceCategory).where(
                ServiceCategory.tenant_id == self._tenant_id,
                ServiceCategory.voided_at.is_(None),
                ServiceCategory.name == name,
            )
        )
        if existing is not None:
            return existing, False
        try:
            return await self.create_category(name, sort_order), True
        except AlreadyExistsError:
            winner = await self._session.scalar(
                select(ServiceCategory).where(
                    ServiceCategory.tenant_id == self._tenant_id,
                    ServiceCategory.voided_at.is_(None),
                    ServiceCategory.name == name,
                )
            )
            if winner is None:  # pragma: no cover  # la fila ganadora se anuló en el medio
                raise
            return winner, False

    # ── servicios ─────────────────────────────────────────────────────────────────────────

    async def list_services(
        self,
        *,
        limit: int,
        offset: int,
        category_id: uuid.UUID | None = None,
        q: str | None = None,
    ) -> tuple[list[ServiceView], int]:
        """Servicios vivos, con sus precios. Con `q`, solo los que la búsqueda encuentra, mejor
        coincidencia primero (`match_tier`); sin `q`, por nombre. Devuelve `(página, total)`."""
        await self._enter()
        wanted = normalize(q) if q is not None else ""
        scope = [Service.tenant_id == self._tenant_id, Service.voided_at.is_(None)]
        if category_id is not None:
            scope.append(Service.category_id == category_id)
        if not wanted:  # sin `q`, o `q` que no deja nada que buscar: se lista todo
            total = int(
                await self._session.scalar(select(func.count()).select_from(Service).where(*scope))
                or 0
            )
            page = list(
                await self._session.scalars(
                    select(Service)
                    .where(*scope)
                    .order_by(Service.name.asc(), Service.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
            )
        else:
            candidates = await self._session.scalars(
                select(Service)
                .where(*scope)
                .order_by(Service.name.asc(), Service.id.desc())
                .limit(_ALL_ROWS)
            )
            ranked: list[tuple[int, Service]] = []
            for service in candidates:
                tier = match_tier(wanted, service.search_key, aliases=_aliases(service))
                if tier is not None:
                    ranked.append((tier, service))
            ranked.sort(key=lambda pair: pair[0])  # estable: a igual tier, queda por nombre
            total = len(ranked)
            page = [service for _, service in ranked[offset : offset + limit]]
        prices = await self._prices_by_service([s.id for s in page])
        live = await self._live_sizes()
        return [
            ServiceView(s, prices[s.id], self._missing_sizes(live, prices[s.id])) for s in page
        ], total

    async def get_service(self, id: uuid.UUID) -> ServiceView:
        await self._enter()
        return await self._view(await self._require(ServiceRepository(self._session), id))

    async def create_priced_service(
        self,
        name: str,
        pricing_mode: PricingMode,
        prices: Sequence[PriceInput],
        *,
        notes: str | None = None,
        category_id: uuid.UUID | None = None,
        deposit_bps: int | None = None,
    ) -> ServiceView:
        """Alta con precio para cada tamaño vivo (D4-6): todo o nada, en la transacción.

        `deposit_bps`: % de seña propio (D4-17); `None` usa el del negocio.

        El `glossary_code` no entra por acá: solo lo fija la plantilla (`TemplateService`).
        """
        await self._enter()
        live = await self._live_sizes()
        self._check_coverage(prices, live)
        if category_id is not None:
            await self._require(ServiceCategoryRepository(self._session), category_id)
        return await self.insert_priced(
            Service(
                tenant_id=self._tenant_id,
                name=name,
                search_key=normalize(name),
                pricing_mode=pricing_mode,
                notes=notes,
                category_id=category_id,
                deposit_bps=deposit_bps,
            ),
            self.sorted_by_size(prices, live),
        )

    async def update_service(
        self,
        id: uuid.UUID,
        *,
        name: str | None = None,
        notes: str | None = None,
        clear_notes: bool = False,
        category_id: uuid.UUID | None = None,
        clear_category: bool = False,
    ) -> ServiceView:
        """Nombre, notas y categoría. **No** precios ni modalidad (§2.3): ver `replace_prices`."""
        await self._enter()
        service = await self._lock(ServiceRepository(self._session), id)
        if category_id is not None:
            await self._require(ServiceCategoryRepository(self._session), category_id)
        try:
            async with guarded_savepoint(self._session, alive_unique("services", "name")):
                if name is not None:
                    service.name = name
                    service.search_key = normalize(name)
                if clear_notes:
                    service.notes = None
                elif notes is not None:
                    service.notes = notes
                if clear_category:
                    service.category_id = None
                elif category_id is not None:
                    service.category_id = category_id
        except SavepointConflictError as exc:
            raise AlreadyExistsError("services: ('name',) already taken") from exc
        return await self._view(service)

    async def replace_prices(
        self,
        id: uuid.UUID,
        prices: Sequence[PriceInput],
        *,
        pricing_mode: PricingMode | None = None,
        deposit_bps: int | None = None,
        set_deposit: bool = False,
    ) -> ServiceView:
        """Fija el precio de **todos** los tamaños vivos de una vez (y, si viene, la modalidad).

        Con `set_deposit`, también el % de seña propio (D4-17): `deposit_bps=None` lo borra y el
        servicio vuelve a usar el del negocio. Afecta solo a los turnos nuevos: los existentes
        conservan el % que guardaron al reservar.

        Lee los precios existentes en una sola consulta y actualiza o agrega según corresponda; el
        servicio queda bloqueado (`FOR UPDATE`), así que no compite con otro `PUT`.
        """
        await self._enter()
        service = await self._lock(ServiceRepository(self._session), id)
        self._check_coverage(prices, await self._live_sizes())
        existing = {
            p.vehicle_size_id: p
            for p in await ServicePriceRepository(self._session).list_for_service(
                id, self._tenant_id
            )
        }
        for price in prices:
            check_price(price.price_cents)
            row = existing.get(price.vehicle_size_id)
            if row is None:
                self._session.add(
                    ServicePrice(
                        tenant_id=self._tenant_id,
                        service_id=id,
                        vehicle_size_id=price.vehicle_size_id,
                        price_cents=price.price_cents,
                        duration_min=price.duration_min,
                    )
                )
            else:
                row.price_cents = price.price_cents
                row.duration_min = price.duration_min
        if set_deposit:
            service.deposit_bps = deposit_bps
        await self._session.flush()
        if pricing_mode is not None and pricing_mode != service.pricing_mode:
            await self.change_pricing_mode(id, pricing_mode)
        return await self._view(service)

    async def _in_use_by(self, service_id: uuid.UUID) -> str | None:
        """Qué usa hoy el servicio (turnos abiertos, cotizaciones sin resolver, jobs abiertos)."""
        tenant = self._tenant_id
        checks = (
            (
                "open bookings",
                select(func.count())
                .select_from(BookingItem)
                .join(
                    Booking,
                    (Booking.tenant_id == BookingItem.tenant_id)
                    & (Booking.id == BookingItem.booking_id),
                )
                .where(
                    BookingItem.tenant_id == tenant,
                    BookingItem.service_id == service_id,
                    BookingItem.voided_at.is_(None),
                    Booking.voided_at.is_(None),
                    Booking.status.in_(OPEN_BOOKING_STATUSES),
                ),
            ),
            (
                "pending quotes",
                select(func.count())
                .select_from(Quote)
                .where(
                    Quote.tenant_id == tenant,
                    Quote.service_id == service_id,
                    Quote.voided_at.is_(None),
                    Quote.status.in_(OPEN_QUOTE_STATUSES),
                ),
            ),
            (
                "open jobs",
                select(func.count())
                .select_from(JobItem)
                .join(Job, (Job.tenant_id == JobItem.tenant_id) & (Job.id == JobItem.job_id))
                .where(
                    JobItem.tenant_id == tenant,
                    JobItem.service_id == service_id,
                    JobItem.voided_at.is_(None),
                    Job.voided_at.is_(None),
                    Job.status.in_(OPEN_JOB_STATUSES),
                ),
            ),
        )
        for label, query in checks:
            if await self._session.scalar(query):
                return label
        return None

    async def void_service(self, id: uuid.UUID) -> None:
        """Anula el servicio y sus precios vivos (ver docstring del módulo).

        409 `IN_USE` si lo usan turnos que no cerraron, cotizaciones sin resolver o jobs
        abiertos: igual que un puesto con turnos futuros, hay que resolverlos antes.
        """
        await self._enter()
        await self._require(ServiceRepository(self._session), id)
        used = await self._in_use_by(id)
        if used is not None:
            raise InUseError(f"the service is still used by {used}: resolve them first")
        for price in await ServicePriceRepository(self._session).list_for_service(
            id, self._tenant_id
        ):
            price.voided_at = utcnow()
            price.void_reason = VoidReason.PEDIDO_DEL_USUARIO
        await self._void(ServiceRepository(self._session), id)

    async def live_service_index(self) -> ServiceIndex:
        """Una consulta: los servicios vivos por `glossary_code` y por `search_key`."""
        await self._enter()
        rows = await self._session.scalars(
            select(Service)
            .where(Service.tenant_id == self._tenant_id, Service.voided_at.is_(None))
            .order_by(Service.created_at, Service.id)
        )
        by_code: dict[str, Service] = {}
        by_key: dict[str, Service] = {}
        for service in rows:
            if service.glossary_code is not None:
                by_code[service.glossary_code] = service
            by_key.setdefault(service.search_key, service)
        return ServiceIndex(by_code, by_key)
