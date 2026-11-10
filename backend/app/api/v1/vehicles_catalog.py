"""Catálogo de vehículos del negocio — FASE-4-CONTRATO §4 (PR 4.4).

- `GET /vehicle-makes?q=`, `GET /vehicle-models?make_id=&q=`, `GET /vehicle-colors?q=` y sus
  `GET /{id}`: cualquier usuario autenticado. Sin `include_hidden=true` no salen las filas ocultas
  (ni los modelos de una marca oculta): es lo que ven los selectores; la pantalla de administración
  pide todo.
- `POST` y `PATCH` (renombrar, ocultar/mostrar, corregir la carrocería) con `CATALOGO_EDITAR`. Un
  nombre repetido (misma clave de búsqueda, entre las vivas, ocultas incluidas) → 409 `NAME_TAKEN`.
- `GET /body-type-sizes` (autenticado) y `PUT /body-type-sizes` (`CATALOGO_EDITAR`): qué tamaño
  sugiere cada carrocería. Cada modelo devuelve `suggested_vehicle_size_id` (A10).
- Recurso ajeno o inexistente → el mismo 404 (Y11, A2); `tenant_id` del cuerpo se ignora. `POST` con
  `Idempotency-Key` opcional.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.v1._shared import EditorUser, guard, once, taken
from app.api.v1.deps import AuthenticatedUser, DbSession
from app.api.v1.errors import ERROR_RESPONSES, IDEMPOTENT_RESPONSES, ApiError, IdempotencyKeyHeader
from app.api.v1.pagination import Paginacion
from app.application.services.vehicle_catalog_service import (
    BodyTypeSizeView,
    ModelView,
    VehicleCatalogService,
)
from app.domain.errors import ErrorCode
from app.persistence.models.user import User
from app.persistence.models.vehicle_catalog import VehicleColor, VehicleMake
from app.schemas.common import PaginatedResponse
from app.schemas.vehicles_catalog import (
    BodyTypeSizeResponse,
    BodyTypeSizesPut,
    VehicleColorCreate,
    VehicleColorResponse,
    VehicleColorUpdate,
    VehicleMakeCreate,
    VehicleMakeResponse,
    VehicleMakeUpdate,
    VehicleModelCreate,
    VehicleModelResponse,
    VehicleModelUpdate,
)

#: Búsqueda: vacía o sin nada buscable se trata como "sin filtro" (como `GET /services?q=`).
Search = Annotated[str | None, Query(max_length=100)]
#: Solo la pantalla de administración pide las ocultas.
IncludeHidden = Annotated[bool, Query()]


def _service(session: DbSession, user: User) -> VehicleCatalogService:
    return VehicleCatalogService(session, user.tenant_id, user.id)


def _name_taken(what: str) -> ApiError:
    return taken(ErrorCode.NAME_TAKEN, what)


# ── /vehicle-makes ───────────────────────────────────────────────────────────

makes = APIRouter()


@makes.get("", response_model=PaginatedResponse[VehicleMakeResponse], responses=ERROR_RESPONSES)
async def list_makes(
    user: AuthenticatedUser,
    session: DbSession,
    page: Paginacion,
    q: Search = None,
    include_hidden: IncludeHidden = False,
) -> PaginatedResponse[VehicleMakeResponse]:
    result = await _service(session, user).list_makes(
        limit=page.limit, offset=page.offset, q=q, include_hidden=include_hidden
    )
    return PaginatedResponse[VehicleMakeResponse](
        items=[VehicleMakeResponse.model_validate(i) for i in result.items],
        total=result.total,
        limit=page.limit,
        offset=page.offset,
    )


@makes.post(
    "",
    response_model=VehicleMakeResponse,
    status_code=status.HTTP_201_CREATED,
    responses=IDEMPOTENT_RESPONSES,
)
async def create_make(
    body: VehicleMakeCreate,
    session: DbSession,
    user: EditorUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> VehicleMake:
    await once(session, user, idempotency_key, "vehicle_makes.create")
    return await guard(
        _service(session, user).create_make(body.name), _name_taken("A make with that name")
    )


@makes.get("/{id}", response_model=VehicleMakeResponse, responses=ERROR_RESPONSES)
async def get_make(id: uuid.UUID, user: AuthenticatedUser, session: DbSession) -> VehicleMake:
    return await guard(_service(session, user).get_make(id))


@makes.patch("/{id}", response_model=VehicleMakeResponse, responses=IDEMPOTENT_RESPONSES)
async def update_make(
    id: uuid.UUID, body: VehicleMakeUpdate, session: DbSession, user: EditorUser
) -> VehicleMake:
    return await guard(
        _service(session, user).update_make(id, name=body.name, hidden=body.hidden),
        _name_taken("A make with that name"),
    )


# ── /vehicle-models ──────────────────────────────────────────────────────────

models = APIRouter()


def _model_response(view: ModelView) -> VehicleModelResponse:
    m = view.model
    return VehicleModelResponse(
        id=m.id,
        tenant_id=m.tenant_id,
        created_at=m.created_at,
        updated_at=m.updated_at,
        name=m.name,
        is_custom=m.is_custom,
        hidden_at=m.hidden_at,
        make_id=m.make_id,
        make_name=view.make_name,
        body_type=m.body_type,
        suggested_vehicle_size_id=view.suggested_vehicle_size_id,
    )


@models.get("", response_model=PaginatedResponse[VehicleModelResponse], responses=ERROR_RESPONSES)
async def list_models(
    user: AuthenticatedUser,
    session: DbSession,
    page: Paginacion,
    make_id: uuid.UUID | None = None,
    q: Search = None,
    include_hidden: IncludeHidden = False,
) -> PaginatedResponse[VehicleModelResponse]:
    views, total = await guard(
        _service(session, user).list_models(
            limit=page.limit,
            offset=page.offset,
            make_id=make_id,
            q=q,
            include_hidden=include_hidden,
        )
    )
    return PaginatedResponse[VehicleModelResponse](
        items=[_model_response(v) for v in views],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@models.post(
    "",
    response_model=VehicleModelResponse,
    status_code=status.HTTP_201_CREATED,
    responses=IDEMPOTENT_RESPONSES,
)
async def create_model(
    body: VehicleModelCreate,
    session: DbSession,
    user: EditorUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> VehicleModelResponse:
    await once(session, user, idempotency_key, "vehicle_models.create")
    view = await guard(
        _service(session, user).create_model(body.make_id, body.name, body.body_type),
        _name_taken("A model with that name for this make"),
    )
    return _model_response(view)


@models.get("/{id}", response_model=VehicleModelResponse, responses=ERROR_RESPONSES)
async def get_model(
    id: uuid.UUID, user: AuthenticatedUser, session: DbSession
) -> VehicleModelResponse:
    return _model_response(await guard(_service(session, user).get_model(id)))


@models.patch("/{id}", response_model=VehicleModelResponse, responses=IDEMPOTENT_RESPONSES)
async def update_model(
    id: uuid.UUID, body: VehicleModelUpdate, session: DbSession, user: EditorUser
) -> VehicleModelResponse:
    view = await guard(
        _service(session, user).update_model(
            id, name=body.name, body_type=body.body_type, hidden=body.hidden
        ),
        _name_taken("A model with that name for this make"),
    )
    return _model_response(view)


# ── /vehicle-colors ──────────────────────────────────────────────────────────

colors = APIRouter()


@colors.get("", response_model=PaginatedResponse[VehicleColorResponse], responses=ERROR_RESPONSES)
async def list_colors(
    user: AuthenticatedUser,
    session: DbSession,
    page: Paginacion,
    q: Search = None,
    include_hidden: IncludeHidden = False,
) -> PaginatedResponse[VehicleColorResponse]:
    result = await _service(session, user).list_colors(
        limit=page.limit, offset=page.offset, q=q, include_hidden=include_hidden
    )
    return PaginatedResponse[VehicleColorResponse](
        items=[VehicleColorResponse.model_validate(i) for i in result.items],
        total=result.total,
        limit=page.limit,
        offset=page.offset,
    )


@colors.post(
    "",
    response_model=VehicleColorResponse,
    status_code=status.HTTP_201_CREATED,
    responses=IDEMPOTENT_RESPONSES,
)
async def create_color(
    body: VehicleColorCreate,
    session: DbSession,
    user: EditorUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> VehicleColor:
    await once(session, user, idempotency_key, "vehicle_colors.create")
    return await guard(
        _service(session, user).create_color(body.name), _name_taken("A color with that name")
    )


@colors.get("/{id}", response_model=VehicleColorResponse, responses=ERROR_RESPONSES)
async def get_color(id: uuid.UUID, user: AuthenticatedUser, session: DbSession) -> VehicleColor:
    return await guard(_service(session, user).get_color(id))


@colors.patch("/{id}", response_model=VehicleColorResponse, responses=IDEMPOTENT_RESPONSES)
async def update_color(
    id: uuid.UUID, body: VehicleColorUpdate, session: DbSession, user: EditorUser
) -> VehicleColor:
    return await guard(
        _service(session, user).update_color(id, name=body.name, hidden=body.hidden),
        _name_taken("A color with that name"),
    )


# ── /body-type-sizes ─────────────────────────────────────────────────────────

body_type_sizes = APIRouter()


def _sizes_page(
    views: list[BodyTypeSizeView], limit: int, offset: int
) -> PaginatedResponse[BodyTypeSizeResponse]:
    return PaginatedResponse[BodyTypeSizeResponse](
        items=[
            BodyTypeSizeResponse(body_type=v.body_type, vehicle_size_id=v.vehicle_size_id)
            for v in views[offset : offset + limit]
        ],
        total=len(views),
        limit=limit,
        offset=offset,
    )


@body_type_sizes.get(
    "", response_model=PaginatedResponse[BodyTypeSizeResponse], responses=ERROR_RESPONSES
)
async def list_body_type_sizes(
    user: AuthenticatedUser, session: DbSession, page: Paginacion
) -> PaginatedResponse[BodyTypeSizeResponse]:
    """Una fila por carrocería (siempre todas), con el tamaño que sugiere o `null`."""
    views = await _service(session, user).list_body_type_sizes()
    return _sizes_page(views, page.limit, page.offset)


@body_type_sizes.put(
    "", response_model=PaginatedResponse[BodyTypeSizeResponse], responses=IDEMPOTENT_RESPONSES
)
async def replace_body_type_sizes(
    body: BodyTypeSizesPut, session: DbSession, user: EditorUser
) -> PaginatedResponse[BodyTypeSizeResponse]:
    """Fija el mapa entero (idempotente, por eso sin `Idempotency-Key`)."""
    views = await guard(
        _service(session, user).replace_body_type_sizes(
            [(i.body_type, i.vehicle_size_id) for i in body.items]
        ),
        # Dos `PUT` a la vez: el servicio los serializa; si igual chocan, 409 reintentable.
        ApiError(
            status.HTTP_409_CONFLICT,
            ErrorCode.CONFLICT,
            "The body type map changed at the same time. Try again.",
        ),
    )
    return _sizes_page(views, len(views), 0)
