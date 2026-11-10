"""Servicios de un turno o un walk-in, leídos del catálogo — adenda C1 del contrato de F3.

Los servicios **se suman**: un turno o un job lleva N. Lo comparten `BookingService.create` y el
walk-in de `JobService.receive`, para que las reglas se escriban una vez:

- al menos un servicio y ninguno repetido;
- cada uno con su fila de precio para el tamaño (§1.1);
- **a lo sumo uno `A_COTIZAR`**: la cotización (`quotes.service_id`) es de un servicio.

Todo servicio tiene precio base por tamaño (D4-6, FASE-4-CONTRATO): en `A_COTIZAR` es la
referencia "desde $X" y la cotización fija el final.

La seña la activa el negocio (D4-4). Si está activa, cada ítem guarda al reservar el % propio del
servicio o, si no tiene, el del negocio (D4-17; `deposit_bps`, único snapshot). Se calcula **por
ítem** (`round_half_up(precio × bps)`) y se suma: así el total no depende de en qué orden se
redondea. El ítem a cotizar guarda el % pero **su seña es 0 hasta que se acepta la cotización**
(D4-7: se calcula sobre el precio acordado).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.catalog_service import check_price
from app.application.services.errors import CatalogIncoherentError, NotFoundError
from app.domain.deposit import deposit_required
from app.domain.enums import PricingMode
from app.domain.exceptions import GuardFailedError
from app.persistence.models.tenant import TenantSettings
from app.persistence.repositories.catalog import ServicePriceRepository, ServiceRepository


def deposit_bps_snapshot(settings: TenantSettings) -> int:
    """El % de seña que se guarda al reservar: el del negocio si la seña está activa, 0 si no."""
    return settings.deposit_bps if settings.deposit_enabled else 0


def item_deposit_bps(business_bps: int, service_bps: int | None) -> int:
    """El % de un ítem: 0 con la seña apagada (`business_bps` 0); si no, el propio del servicio o
    el del negocio (D4-17)."""
    if business_bps == 0 or service_bps is None:
        return business_bps
    return service_bps


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """Un servicio con su precio base para el tamaño y el % de seña vigente al reservar."""

    service_id: uuid.UUID
    name: str
    pricing_mode: PricingMode
    duration_min: int
    price_cents: int
    deposit_bps: int

    @property
    def quoted(self) -> bool:
        return self.pricing_mode == PricingMode.A_COTIZAR

    @property
    def deposit_cents(self) -> int:
        """Seña al reservar. Un ítem a cotizar no lleva seña hasta aceptar la cotización (D4-7)."""
        if self.quoted:
            return 0
        return deposit_required(self.price_cents, self.deposit_bps)


def booking_deposit_cents(items: Sequence[CatalogItem]) -> int:
    """Seña con la que nace un turno. Con un ítem a cotizar es 0: la seña total se calcula al
    aceptar la cotización (D4-7), sobre el precio acordado y junto con la de los demás ítems."""
    if any(item.quoted for item in items):
        return 0
    return sum(item.deposit_cents for item in items)


def require_service_ids(service_ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    """Al menos uno y sin repetidos (único vivo `(…_id, service_id)` de los ítems)."""
    ids = list(service_ids)
    if not ids:
        raise GuardFailedError("at least one service is required")
    if len(set(ids)) != len(ids):
        raise GuardFailedError("a service cannot be added twice")
    return ids


async def catalog_items(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    service_ids: Sequence[uuid.UUID],
    vehicle_size_id: uuid.UUID,
    *,
    deposit_bps: int,
    quoted_without_base: bool = False,
) -> list[CatalogItem]:
    """Los servicios pedidos, en el mismo orden, con precio base y duración del catálogo.

    `deposit_bps` es el snapshot de seña del negocio (`deposit_bps_snapshot`): 0 si la seña está
    apagada. Si está activa, un servicio con % propio lo pisa (D4-17). Todo servicio exige
    su fila de precio para el tamaño, en las dos modalidades. Más de un `A_COTIZAR` es
    `GuardFailedError`.

    `quoted_without_base`: un walk-in toma precio y duración del `A_COTIZAR` de su cotización
    aceptada, así que ahí la fila base del servicio a cotizar puede faltar (queda en 0).
    """
    services = ServiceRepository(session)
    prices = ServicePriceRepository(session)
    items: list[CatalogItem] = []
    for service_id in require_service_ids(service_ids):
        service = await services.get_by_id(service_id, tenant_id)
        if service is None:
            raise NotFoundError("services", service_id)
        row = await prices.find_for(service_id, vehicle_size_id, tenant_id)
        if row is None:
            if not (quoted_without_base and service.pricing_mode == PricingMode.A_COTIZAR):
                raise CatalogIncoherentError("the service has no price row for that vehicle size")
            price_cents, duration_min = 0, 0
        else:
            check_price(row.price_cents)
            price_cents, duration_min = row.price_cents, row.duration_min
        items.append(
            CatalogItem(
                service_id=service.id,
                name=service.name,
                pricing_mode=service.pricing_mode,
                duration_min=duration_min,
                price_cents=price_cents,
                deposit_bps=item_deposit_bps(deposit_bps, service.deposit_bps),
            )
        )
    if sum(1 for item in items if item.quoted) > 1:
        raise GuardFailedError("at most one service to be quoted per booking or job")
    return items
