"""Repositorio genérico async, tenant-scoped.

Adaptado de Véktor (`persistence/repositories/base.py`):
- se conserva el `tenant_id` obligatorio en toda firma;
- **se agrega** el filtro `voided_at IS NULL` por defecto (`include_voided=False`,
  ADR-0003) y `void()` como única forma de anular;
- `list_by_tenant` devuelve `Page(items, total)` para que el endpoint no pueda dar
  uno sin el otro (ADR-0006);
- **se sacan** los `# type: ignore[attr-defined]`: `ModelT` se acota a
  `TenantScopedModel`, posible ahora que la PK siempre se llama `id` (ADR-0004).
  (Se probó un `Protocol` con atributos `Mapped[...]`: mypy no reconoce a los
  modelos como subtipos porque `tenant_id` es un `declared_attr`. La base abstracta
  da el mismo chequeo sin ningún `type: ignore`.)

El filtro por `tenant_id` es la TERCERA red (RLS es la primera, los tests la
segunda). Es gratis y no se saca.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.void import VoidReason
from app.persistence.db.base import TenantScopedModel
from app.utils.datetime_utils import utcnow


async def one_or_none[T](session: AsyncSession, statement: Select[tuple[T]]) -> T | None:
    """`scalar_one_or_none` tipado (`AsyncSession.scalar` devuelve `Any` para mypy)."""
    return (await session.execute(statement)).scalar_one_or_none()


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    total: int


class BaseRepository[ModelT: TenantScopedModel]:
    """CRUD genérico. Las subclases fijan `model`."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _scope(self, tenant_id: uuid.UUID, include_voided: bool) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = [self.model.tenant_id == tenant_id]
        if not include_voided:
            conditions.append(self.model.voided_at.is_(None))
        return conditions

    async def get_by_id(
        self, id: uuid.UUID, tenant_id: uuid.UUID, *, include_voided: bool = False
    ) -> ModelT | None:
        result = await self._session.execute(
            select(self.model).where(self.model.id == id, *self._scope(tenant_id, include_voided))
        )
        return result.scalar_one_or_none()

    async def get_for_update(self, id: uuid.UUID, tenant_id: uuid.UUID) -> ModelT | None:
        """`SELECT … FOR UPDATE` de una fila viva (FASE-3-CONTRATO §2, C-18).

        `populate_existing`: si la fila ya estaba en el identity map, se pisa con lo que
        devuelve la base **después** de tomar el lock. Sin eso, el servicio validaría la
        transición contra una copia vieja y la guarda `from == estado actual` no serviría.
        En SQLite `FOR UPDATE` no se emite (no hay locks de fila): la carrera se prueba en
        Postgres.
        """
        result = await self._session.execute(
            select(self.model)
            .where(self.model.id == id, *self._scope(tenant_id, include_voided=False))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        include_voided: bool = False,
    ) -> Page[ModelT]:
        scope = self._scope(tenant_id, include_voided)
        total = await self._session.scalar(
            select(func.count()).select_from(self.model).where(*scope)
        )
        result = await self._session.execute(
            select(self.model)
            .where(*scope)
            # Orden determinista: uuid4 no es ordenable en el tiempo (ADR-0004).
            .order_by(self.model.created_at.desc(), self.model.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return Page(items=list(result.scalars().all()), total=int(total or 0))

    async def save(self, entity: ModelT) -> ModelT:
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def void(self, id: uuid.UUID, tenant_id: uuid.UUID, reason: VoidReason) -> ModelT | None:
        """Anula: setea las dos columnas juntas. No existe un camino que setee una sola."""
        entity = await self.get_by_id(id, tenant_id)
        if entity is None:
            return None
        entity.voided_at = utcnow()
        entity.void_reason = reason
        await self._session.flush()
        return entity
