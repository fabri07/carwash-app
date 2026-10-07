"""Servicios de un turno o un walk-in, leídos del catálogo — adenda C1 del contrato de F3.

Los servicios **se suman**: un turno o un job lleva N. Lo comparten `BookingService.create` y el
walk-in de `JobService.receive`, para que las reglas se escriban una vez:

- al menos un servicio y ninguno repetido;
- cada uno con su fila de precio para el tamaño, coherente con la modalidad (§1.1);
- **a lo sumo uno `A_COTIZAR`**: la cotización (`quotes.service_id`) es de un servicio.

La seña se calcula **por ítem** (`round_half_up(precio × bps)`) y se suma: así el total no depende
de en qué orden se redondea.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.catalog_service import check_price_coherence
from app.application.services.errors import CatalogIncoherentError, NotFoundError
from app.domain.deposit import deposit_required
from app.domain.enums import PricingMode
from app.domain.exceptions import GuardFailedError
from app.persistence.repositories.catalog import ServicePriceRepository, ServiceRepository


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """Un servicio con su precio para el tamaño. `price_cents` NULL = a cotizar."""

    service_id: uuid.UUID
    name: str
    pricing_mode: PricingMode
    duration_min: int | None
    price_cents: int | None
    deposit_bps: int

    @property
    def quoted(self) -> bool:
        return self.pricing_mode == PricingMode.A_COTIZAR

    @property
    def deposit_cents(self) -> int:
        return deposit_required(self.price_cents, self.deposit_bps)


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
) -> list[CatalogItem]:
    """Los servicios pedidos, en el mismo orden, con precio y duración del catálogo.

    Un `PRECIO_FIJO` exige su fila de precio; un `A_COTIZAR` puede no tenerla (el walk-in toma
    precio y duración de la cotización). Más de un `A_COTIZAR` es `GuardFailedError`.
    """
    services = ServiceRepository(session)
    prices = ServicePriceRepository(session)
    items: list[CatalogItem] = []
    for service_id in require_service_ids(service_ids):
        service = await services.get_by_id(service_id, tenant_id)
        if service is None:
            raise NotFoundError("services", service_id)
        row = await prices.find_for(service_id, vehicle_size_id, tenant_id)
        if row is None and service.pricing_mode == PricingMode.PRECIO_FIJO:
            raise CatalogIncoherentError("the service has no price row for that vehicle size")
        if row is not None:
            check_price_coherence(service.pricing_mode, row.price_cents, row.deposit_bps)
        items.append(
            CatalogItem(
                service_id=service.id,
                name=service.name,
                pricing_mode=service.pricing_mode,
                duration_min=row.duration_min if row is not None else None,
                price_cents=row.price_cents if row is not None else None,
                deposit_bps=row.deposit_bps if row is not None else 0,
            )
        )
    if sum(1 for item in items if item.quoted) > 1:
        raise GuardFailedError("at most one service to be quoted per booking or job")
    return items
