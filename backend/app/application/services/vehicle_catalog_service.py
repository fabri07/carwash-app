"""Catálogo de vehículos del negocio — FASE-4-CONTRATO D4-8, Y3, Y7, §3.4, §4 y A10 (PR 4.4).

- **Copia de la base** (`copy_vehicle_base`): la usa `provision_tenant` al dar de alta un negocio.
  Inserta en lote (tres `INSERT`), sin pasar por el ORM fila a fila: son ~450 filas por negocio.
- **Lectura** (`list_*`, `get_*`): cualquier usuario autenticado. Las filas ocultas (`hidden_at`) no
  salen salvo `include_hidden` (la pantalla de administración). Un modelo de una marca oculta
  tampoco sale. Búsqueda con `domain/search.match_tier` en Python sobre el catálogo del tenant (Y8):
  sin `q`, ordena por nombre en la base; con `q`, mejor coincidencia primero. Un modelo se encuentra
  también por "marca modelo" ("toyota hilux").
- **Alta y edición** (`CATALOGO_EDITAR`): lo que se crea es `is_custom`; renombrar recalcula
  `search_key`; ocultar/mostrar fija o borra `hidden_at`. No hay baja: ocultar alcanza y un vehículo
  cargado (F6) puede apuntar a la fila.
- **Carrocería → tamaño** (`body_type_sizes`): `replace_body_type_sizes` fija el mapa entero; cada
  modelo informa el tamaño que sugiere (`suggested_vehicle_size_id`, A10). Un tamaño anulado deja de
  sugerirse aunque la fila siga apuntándolo.
"""

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services._base import ServiceBase, alive_unique
from app.application.services.errors import AlreadyExistsError
from app.domain.exceptions import InvalidAmountError
from app.domain.search import match_tier, normalize
from app.domain.vehicle_catalog import VehicleBodyType, load_vehicle_base
from app.domain.void import VoidReason
from app.persistence.db._savepoint import SavepointConflictError, guarded_savepoint
from app.persistence.db.base import TenantScopedModel
from app.persistence.models.catalog import VehicleSize
from app.persistence.models.tenant import TenantSettings
from app.persistence.models.vehicle_catalog import (
    BodyTypeSize,
    VehicleColor,
    VehicleMake,
    VehicleModel,
)
from app.persistence.repositories.base import Page
from app.persistence.repositories.vehicle_catalog import (
    VehicleColorRepository,
    VehicleMakeRepository,
    VehicleModelRepository,
)
from app.utils.datetime_utils import utcnow

CatalogRow = VehicleMake | VehicleModel | VehicleColor


@dataclass(frozen=True)
class BaseCopy:
    makes: int
    models: int
    colors: int


async def copy_vehicle_base(session: AsyncSession, tenant_id: uuid.UUID) -> BaseCopy:
    """Copia la base del mercado argentino al negocio (Y3, Y7). La transacción es del llamador.

    El contexto de tenant ya tiene que estar fijado (lo hace `provision_tenant`): el `WITH CHECK`
    de RLS lo exige.
    """
    base = load_vehicle_base()
    makes: list[dict[str, Any]] = []
    models: list[dict[str, Any]] = []
    for make in base.makes:
        make_id = uuid.uuid4()
        makes.append(
            {
                "id": make_id,
                "tenant_id": tenant_id,
                "name": make.name,
                "search_key": normalize(make.name),
            }
        )
        models.extend(
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "make_id": make_id,
                "name": model.name,
                "body_type": model.body_type,
                "search_key": normalize(model.name),
            }
            for model in make.models
        )
    colors = [
        {"id": uuid.uuid4(), "tenant_id": tenant_id, "name": name, "search_key": normalize(name)}
        for name in base.colors
    ]
    await session.execute(insert(VehicleMake), makes)
    await session.execute(insert(VehicleModel), models)
    await session.execute(insert(VehicleColor), colors)
    return BaseCopy(makes=len(makes), models=len(models), colors=len(colors))


@dataclass(frozen=True)
class ModelView:
    model: VehicleModel
    make_name: str
    #: Tamaño que sugiere su carrocería (`body_type_sizes`), si el negocio lo cargó (A10).
    suggested_vehicle_size_id: uuid.UUID | None


@dataclass(frozen=True)
class BodyTypeSizeView:
    body_type: VehicleBodyType
    vehicle_size_id: uuid.UUID | None


def _rank[R: CatalogRow](
    rows: Sequence[R], wanted: str, aliases: Mapping[uuid.UUID, Sequence[str]] | None = None
) -> list[R]:
    """Las filas que la búsqueda encuentra, mejor coincidencia primero (estable por nombre)."""
    ranked: list[tuple[int, R]] = []
    for row in rows:
        tier = match_tier(wanted, row.search_key, aliases=(aliases or {}).get(row.id, ()))
        if tier is not None:
            ranked.append((tier, row))
    ranked.sort(key=lambda pair: pair[0])
    return [row for _, row in ranked]


class VehicleCatalogService(ServiceBase):
    # ── helpers ───────────────────────────────────────────────────────────────────────────

    async def _insert_unique[M: TenantScopedModel](self, entity: M, *columns: str, what: str) -> M:
        table = entity.__tablename__
        try:
            async with guarded_savepoint(self._session, alive_unique(table, *columns)):
                self._session.add(entity)
        except SavepointConflictError as exc:
            raise AlreadyExistsError(f"{what} already exists") from exc
        return entity

    async def _rename[M: CatalogRow](self, row: M, name: str, *columns: str, what: str) -> None:
        try:
            async with guarded_savepoint(self._session, alive_unique(row.__tablename__, *columns)):
                row.name = name
                row.search_key = normalize(name)
        except SavepointConflictError as exc:
            raise AlreadyExistsError(f"{what} already exists") from exc

    @staticmethod
    def _set_hidden(row: CatalogRow, hidden: bool | None) -> None:
        if hidden is None:
            return
        if hidden and row.hidden_at is None:
            row.hidden_at = utcnow()
        elif not hidden:
            row.hidden_at = None

    async def _page[M: CatalogRow](
        self,
        model: type[M],
        scope: list[ColumnElement[bool]],
        *,
        q: str | None,
        limit: int,
        offset: int,
        aliases_of: Callable[[Sequence[M]], Mapping[uuid.UUID, Sequence[str]]] | None = None,
    ) -> Page[M]:
        """Página de filas vivas de `model`: por nombre en la base, o rankeada en Python con `q`.

        Con `q` se lee **todo** el alcance (sin tope) para que `total` sea exacto: un negocio
        tiene unas 450 filas de la base más las que sume a mano (Y8). Un tope fijo truncaría en
        silencio y el total mentiría.
        """
        wanted = normalize(q) if q is not None else ""
        order = (model.name.asc(), model.id.desc())
        if not wanted:
            total = int(
                await self._session.scalar(select(func.count()).select_from(model).where(*scope))
                or 0
            )
            items = list(
                await self._session.scalars(
                    select(model).where(*scope).order_by(*order).limit(limit).offset(offset)
                )
            )
            return Page(items=items, total=total)
        rows = list(await self._session.scalars(select(model).where(*scope).order_by(*order)))
        aliases = aliases_of(rows) if aliases_of is not None else None
        ranked = _rank(rows, wanted, aliases)
        return Page(items=ranked[offset : offset + limit], total=len(ranked))

    def _scope(self, model: type[CatalogRow], include_hidden: bool) -> list[ColumnElement[bool]]:
        scope: list[ColumnElement[bool]] = [
            model.tenant_id == self._tenant_id,
            model.voided_at.is_(None),
        ]
        if not include_hidden:
            scope.append(model.hidden_at.is_(None))
        return scope

    # ── marcas ────────────────────────────────────────────────────────────────────────────

    async def list_makes(
        self, *, limit: int, offset: int, q: str | None = None, include_hidden: bool = False
    ) -> Page[VehicleMake]:
        await self._enter()
        return await self._page(
            VehicleMake, self._scope(VehicleMake, include_hidden), q=q, limit=limit, offset=offset
        )

    async def get_make(self, id: uuid.UUID) -> VehicleMake:
        await self._enter()
        return await self._require(VehicleMakeRepository(self._session), id)

    async def create_make(self, name: str) -> VehicleMake:
        await self._enter()
        return await self._insert_unique(
            VehicleMake(
                tenant_id=self._tenant_id, name=name, search_key=normalize(name), is_custom=True
            ),
            "search_key",
            what="a make with that name",
        )

    async def update_make(
        self, id: uuid.UUID, *, name: str | None = None, hidden: bool | None = None
    ) -> VehicleMake:
        await self._enter()
        make = await self._lock(VehicleMakeRepository(self._session), id)
        if name is not None:
            await self._rename(make, name, "search_key", what="a make with that name")
        self._set_hidden(make, hidden)
        await self._session.flush()
        return make

    # ── modelos ───────────────────────────────────────────────────────────────────────────

    async def _suggestions(self) -> dict[VehicleBodyType, uuid.UUID]:
        """Carrocería → tamaño vivo que sugiere (A10). Una consulta por request."""
        rows = await self._session.execute(
            select(BodyTypeSize.body_type, BodyTypeSize.vehicle_size_id)
            .join(
                VehicleSize,
                (VehicleSize.tenant_id == BodyTypeSize.tenant_id)
                & (VehicleSize.id == BodyTypeSize.vehicle_size_id),
            )
            .where(
                BodyTypeSize.tenant_id == self._tenant_id,
                BodyTypeSize.voided_at.is_(None),
                VehicleSize.voided_at.is_(None),
            )
        )
        return {row.body_type: row.vehicle_size_id for row in rows}

    async def _makes_of(self, make_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, VehicleMake]:
        if not make_ids:
            return {}
        rows = await self._session.scalars(
            select(VehicleMake).where(
                VehicleMake.tenant_id == self._tenant_id, VehicleMake.id.in_(set(make_ids))
            )
        )
        return {make.id: make for make in rows}

    async def _all_makes(self) -> dict[uuid.UUID, VehicleMake]:
        """Todas las marcas del negocio (unas 40): una consulta por request de listado."""
        rows = await self._session.scalars(
            select(VehicleMake).where(VehicleMake.tenant_id == self._tenant_id)
        )
        return {make.id: make for make in rows}

    async def _views(
        self,
        models: Sequence[VehicleModel],
        makes: Mapping[uuid.UUID, VehicleMake] | None = None,
    ) -> list[ModelView]:
        if makes is None:
            makes = await self._makes_of([m.make_id for m in models])
        suggested = await self._suggestions()
        return [ModelView(m, makes[m.make_id].name, suggested.get(m.body_type)) for m in models]

    async def list_models(
        self,
        *,
        limit: int,
        offset: int,
        make_id: uuid.UUID | None = None,
        q: str | None = None,
        include_hidden: bool = False,
    ) -> tuple[list[ModelView], int]:
        """Modelos vivos, de una marca o de todas. Sin `include_hidden`, ni los ocultos ni los de
        una marca oculta. Una marca ajena o inexistente es 404 (no una lista vacía)."""
        await self._enter()
        scope = self._scope(VehicleModel, include_hidden)
        if make_id is not None:
            await self._require(VehicleMakeRepository(self._session), make_id)
            scope.append(VehicleModel.make_id == make_id)
        if not include_hidden:
            visible_makes = select(VehicleMake.id).where(
                VehicleMake.tenant_id == self._tenant_id,
                VehicleMake.voided_at.is_(None),
                VehicleMake.hidden_at.is_(None),
            )
            scope.append(VehicleModel.make_id.in_(visible_makes))

        # Una sola carga de las marcas: sirve para la búsqueda y para armar las vistas.
        makes = await self._all_makes()

        def make_and_model(rows: Sequence[VehicleModel]) -> dict[uuid.UUID, list[str]]:
            # "toyota hilux" encuentra a la Hilux aunque se busque sin elegir marca.
            return {r.id: [f"{makes[r.make_id].search_key} {r.search_key}"] for r in rows}

        page = await self._page(
            VehicleModel, scope, q=q, limit=limit, offset=offset, aliases_of=make_and_model
        )
        return await self._views(page.items, makes), page.total

    async def get_model(self, id: uuid.UUID) -> ModelView:
        await self._enter()
        model = await self._require(VehicleModelRepository(self._session), id)
        return (await self._views([model]))[0]

    async def create_model(
        self, make_id: uuid.UUID, name: str, body_type: VehicleBodyType
    ) -> ModelView:
        await self._enter()
        await self._require(VehicleMakeRepository(self._session), make_id)
        model = await self._insert_unique(
            VehicleModel(
                tenant_id=self._tenant_id,
                make_id=make_id,
                name=name,
                body_type=body_type,
                search_key=normalize(name),
                is_custom=True,
            ),
            "make_id",
            "search_key",
            what="a model with that name for this make",
        )
        return (await self._views([model]))[0]

    async def update_model(
        self,
        id: uuid.UUID,
        *,
        name: str | None = None,
        body_type: VehicleBodyType | None = None,
        hidden: bool | None = None,
    ) -> ModelView:
        """Renombrar, corregir la carrocería u ocultar. La marca no cambia: se crea otro modelo."""
        await self._enter()
        model = await self._lock(VehicleModelRepository(self._session), id)
        if name is not None:
            await self._rename(
                model, name, "make_id", "search_key", what="a model with that name for this make"
            )
        if body_type is not None:
            model.body_type = body_type
        self._set_hidden(model, hidden)
        await self._session.flush()
        return (await self._views([model]))[0]

    # ── colores ───────────────────────────────────────────────────────────────────────────

    async def list_colors(
        self, *, limit: int, offset: int, q: str | None = None, include_hidden: bool = False
    ) -> Page[VehicleColor]:
        await self._enter()
        return await self._page(
            VehicleColor, self._scope(VehicleColor, include_hidden), q=q, limit=limit, offset=offset
        )

    async def get_color(self, id: uuid.UUID) -> VehicleColor:
        await self._enter()
        return await self._require(VehicleColorRepository(self._session), id)

    async def create_color(self, name: str) -> VehicleColor:
        await self._enter()
        return await self._insert_unique(
            VehicleColor(
                tenant_id=self._tenant_id, name=name, search_key=normalize(name), is_custom=True
            ),
            "search_key",
            what="a color with that name",
        )

    async def update_color(
        self, id: uuid.UUID, *, name: str | None = None, hidden: bool | None = None
    ) -> VehicleColor:
        await self._enter()
        color = await self._lock(VehicleColorRepository(self._session), id)
        if name is not None:
            await self._rename(color, name, "search_key", what="a color with that name")
        self._set_hidden(color, hidden)
        await self._session.flush()
        return color

    # ── carrocería → tamaño ───────────────────────────────────────────────────────────────

    async def list_body_type_sizes(self) -> list[BodyTypeSizeView]:
        """Todas las carrocerías, en el orden del enum, con su tamaño o `None`."""
        await self._enter()
        suggested = await self._suggestions()
        return [BodyTypeSizeView(bt, suggested.get(bt)) for bt in VehicleBodyType]

    async def _lock_body_type_map(self) -> None:
        """Serializa los `PUT` del mapa de un negocio: `FOR UPDATE` sobre su `tenant_settings`.

        Bloquear las filas del mapa no alcanza: en un negocio sin filas no hay nada que bloquear y
        dos `PUT` a la vez insertarían la misma carrocería (el único vivo rechazaría al segundo).
        `tenant_settings` existe siempre y es una por negocio (§3.2). En SQLite no se emite.
        """
        await self._session.execute(
            select(TenantSettings.id)
            .where(TenantSettings.tenant_id == self._tenant_id)
            .with_for_update()
        )

    async def _current_body_type_sizes(self) -> dict[VehicleBodyType, BodyTypeSize]:
        rows = await self._session.scalars(
            select(BodyTypeSize).where(
                BodyTypeSize.tenant_id == self._tenant_id, BodyTypeSize.voided_at.is_(None)
            )
        )
        return {row.body_type: row for row in rows}

    async def replace_body_type_sizes(
        self, mapping: Sequence[tuple[VehicleBodyType, uuid.UUID | None]]
    ) -> list[BodyTypeSizeView]:
        """Fija el mapa entero: una carrocería ausente o con `None` deja de sugerir tamaño.

        Una carrocería repetida o un tamaño que no es un tamaño vivo del negocio (ajeno,
        anulado o inexistente: el mismo mensaje) → `InvalidAmountError` (422).
        """
        await self._enter()
        body_types = [bt for bt, _ in mapping]
        if len(set(body_types)) != len(body_types):
            raise InvalidAmountError("a body type appears more than once")
        wanted = {bt: size for bt, size in mapping if size is not None}
        live = set(
            await self._session.scalars(
                select(VehicleSize.id).where(
                    VehicleSize.tenant_id == self._tenant_id, VehicleSize.voided_at.is_(None)
                )
            )
        )
        unknown = sorted(str(s) for s in set(wanted.values()) - live)
        if unknown:
            raise InvalidAmountError(f"unknown vehicle size(s): {', '.join(unknown)}")
        await self._lock_body_type_map()
        current = await self._current_body_type_sizes()
        for body_type, row in current.items():
            if body_type not in wanted:
                row.voided_at = utcnow()
                row.void_reason = VoidReason.PEDIDO_DEL_USUARIO
            elif row.vehicle_size_id != wanted[body_type]:
                row.vehicle_size_id = wanted[body_type]
        # `guarded_savepoint` drena primero lo anulado: el único vivo por carrocería no admite dos
        # filas a la vez. Lo nuevo va adentro del bloque, protegido.
        try:
            async with guarded_savepoint(
                self._session, alive_unique("body_type_sizes", "body_type")
            ):
                self._session.add_all(
                    BodyTypeSize(tenant_id=self._tenant_id, body_type=bt, vehicle_size_id=size)
                    for bt, size in wanted.items()
                    if bt not in current
                )
        except SavepointConflictError as exc:
            # Red de abajo: con el lock de arriba no debería pasar. La API lo da como 409
            # `CONFLICT` (reintentable), nunca como 500.
            raise AlreadyExistsError("body type sizes changed concurrently") from exc
        return await self.list_body_type_sizes()
