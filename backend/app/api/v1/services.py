"""Servicios, categorías y plantillas — FASE-4-CONTRATO §4 (PR 4.3).

- `/service-categories` y `/services`: lectura para cualquier usuario autenticado; escritura con
  `CATALOGO_EDITAR`. **Precios** con `PRECIOS_EDITAR`: `PUT /services/{id}/prices` lo exige;
  `POST /services` y `DELETE /services/{id}` piden **los dos** permisos (§2.3): un servicio
  nace con precios y al anularlo se anulan también.
  `PATCH /services/{id}` no acepta campos de precio ni la seña propia (D4-17), que cambia lo que
  paga el cliente: va por `POST` y `PUT .../prices`.
- `GET /services/search?q=`: búsqueda tolerante (sin tildes, sinónimos del glosario, errores de
  tipeo, defectos). Autenticado.
- `/templates`: solo el `OWNER`. `POST /templates/apply` es idempotente por `glossary_code` (no
  duplica), así que no lleva `Idempotency-Key`: repetirlo devuelve `skipped`, no un 409.
- Recurso ajeno o inexistente → el mismo 404 (Y11, A2). `POST` con `Idempotency-Key` opcional.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.v1._shared import EditorUser, guard, once, taken
from app.api.v1.deps import AuthenticatedUser, DbSession, OwnerUser, require_permission
from app.api.v1.errors import (
    ERROR_RESPONSES,
    IDEMPOTENT_RESPONSES,
    ApiError,
    IdempotencyKeyHeader,
)
from app.api.v1.pagination import Paginacion
from app.application.services.service_catalog_service import ServiceCatalogService, ServiceView
from app.application.services.template_service import (
    ApplyResult,
    TemplateEntry,
    TemplateItemInput,
    TemplateService,
)
from app.domain.errors import ErrorCode
from app.domain.glossary import TemplateKind
from app.domain.permissions import Permission
from app.persistence.models.catalog import ServiceCategory
from app.persistence.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.services import (
    ServiceCategoryCreate,
    ServiceCategoryResponse,
    ServiceCategoryUpdate,
    ServiceCreate,
    ServicePriceResponse,
    ServicePricesPut,
    ServiceResponse,
    ServiceUpdate,
    TemplateApply,
    TemplateApplyResponse,
    TemplateCategoryResponse,
    TemplateResponse,
    TemplateSkippedResponse,
    TemplateTermResponse,
)

#: Precios: el permiso propio (§2.1). `POST /services` los pide **a los dos**.
PricerUser = Annotated[User, Depends(require_permission(Permission.PRECIOS_EDITAR))]
CatalogAndPricerUser = Annotated[
    User,
    Depends(require_permission(Permission.CATALOGO_EDITAR, Permission.PRECIOS_EDITAR)),
]


def _service(session: DbSession, user: User) -> ServiceCatalogService:
    return ServiceCatalogService(session, user.tenant_id, user.id)


def _to_response(view: ServiceView) -> ServiceResponse:
    s = view.service
    return ServiceResponse(
        id=s.id,
        tenant_id=s.tenant_id,
        created_at=s.created_at,
        updated_at=s.updated_at,
        name=s.name,
        pricing_mode=s.pricing_mode,
        notes=s.notes,
        category_id=s.category_id,
        glossary_code=s.glossary_code,
        deposit_bps=s.deposit_bps,
        prices=[ServicePriceResponse.model_validate(p) for p in view.prices],
        missing_price_size_ids=view.missing_price_size_ids,
    )


def _page(
    views: list[ServiceView], total: int, limit: int, offset: int
) -> PaginatedResponse[ServiceResponse]:
    return PaginatedResponse[ServiceResponse](
        items=[_to_response(v) for v in views], total=total, limit=limit, offset=offset
    )


def _name_taken() -> ApiError:
    return taken(ErrorCode.NAME_TAKEN, "A service with that name")


# ── /service-categories ──────────────────────────────────────────────────────

categories = APIRouter()


@categories.get(
    "", response_model=PaginatedResponse[ServiceCategoryResponse], responses=ERROR_RESPONSES
)
async def list_categories(
    user: AuthenticatedUser, session: DbSession, page: Paginacion
) -> PaginatedResponse[ServiceCategoryResponse]:
    result = await _service(session, user).list_categories(limit=page.limit, offset=page.offset)
    return PaginatedResponse[ServiceCategoryResponse](
        items=[ServiceCategoryResponse.model_validate(i) for i in result.items],
        total=result.total,
        limit=page.limit,
        offset=page.offset,
    )


@categories.post(
    "",
    response_model=ServiceCategoryResponse,
    status_code=status.HTTP_201_CREATED,
    responses=IDEMPOTENT_RESPONSES,
)
async def create_category(
    body: ServiceCategoryCreate,
    session: DbSession,
    user: EditorUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ServiceCategory:
    await once(session, user, idempotency_key, "service_categories.create")
    return await guard(
        _service(session, user).create_category(body.name, body.sort_order),
        taken(ErrorCode.NAME_TAKEN, "A category with that name"),
    )


@categories.get("/{id}", response_model=ServiceCategoryResponse, responses=ERROR_RESPONSES)
async def get_category(
    id: uuid.UUID, user: AuthenticatedUser, session: DbSession
) -> ServiceCategory:
    return await guard(_service(session, user).get_category(id))


@categories.patch("/{id}", response_model=ServiceCategoryResponse, responses=IDEMPOTENT_RESPONSES)
async def update_category(
    id: uuid.UUID, body: ServiceCategoryUpdate, session: DbSession, user: EditorUser
) -> ServiceCategory:
    return await guard(
        _service(session, user).update_category(id, name=body.name, sort_order=body.sort_order),
        taken(ErrorCode.NAME_TAKEN, "A category with that name"),
    )


@categories.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=IDEMPOTENT_RESPONSES)
async def delete_category(id: uuid.UUID, session: DbSession, user: EditorUser) -> Response:
    await guard(_service(session, user).void_category(id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── /services ────────────────────────────────────────────────────────────────

services = APIRouter()


@services.get("", response_model=PaginatedResponse[ServiceResponse], responses=ERROR_RESPONSES)
async def list_services(
    user: AuthenticatedUser,
    session: DbSession,
    page: Paginacion,
    category_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> PaginatedResponse[ServiceResponse]:
    views, total = await _service(session, user).list_services(
        limit=page.limit, offset=page.offset, category_id=category_id, q=q
    )
    return _page(views, total, page.limit, page.offset)


# Antes de `/{id}`: si no, "search" se leería como un id.
@services.get(
    "/search", response_model=PaginatedResponse[ServiceResponse], responses=ERROR_RESPONSES
)
async def search_services(
    user: AuthenticatedUser,
    session: DbSession,
    page: Paginacion,
    q: Annotated[str, Query(min_length=1, max_length=100)],
) -> PaginatedResponse[ServiceResponse]:
    views, total = await _service(session, user).list_services(
        limit=page.limit, offset=page.offset, q=q
    )
    return _page(views, total, page.limit, page.offset)


@services.post(
    "",
    response_model=ServiceResponse,
    status_code=status.HTTP_201_CREATED,
    responses=IDEMPOTENT_RESPONSES,
)
async def create_service(
    body: ServiceCreate,
    session: DbSession,
    user: CatalogAndPricerUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ServiceResponse:
    await once(session, user, idempotency_key, "services.create")
    view = await guard(
        _service(session, user).create_priced_service(
            body.name,
            body.pricing_mode,
            [p.to_input() for p in body.prices],
            notes=body.notes,
            category_id=body.category_id,
            deposit_bps=body.deposit_bps,
        ),
        _name_taken(),
    )
    return _to_response(view)


@services.get("/{id}", response_model=ServiceResponse, responses=ERROR_RESPONSES)
async def get_service(
    id: uuid.UUID, user: AuthenticatedUser, session: DbSession
) -> ServiceResponse:
    return _to_response(await guard(_service(session, user).get_service(id)))


@services.patch("/{id}", response_model=ServiceResponse, responses=IDEMPOTENT_RESPONSES)
async def update_service(
    id: uuid.UUID, body: ServiceUpdate, session: DbSession, user: EditorUser
) -> ServiceResponse:
    fields = body.model_fields_set
    return _to_response(
        await guard(
            _service(session, user).update_service(
                id,
                name=body.name,
                notes=body.notes,
                clear_notes="notes" in fields and body.notes is None,
                category_id=body.category_id,
                clear_category="category_id" in fields and body.category_id is None,
            ),
            _name_taken(),
        )
    )


@services.put("/{id}/prices", response_model=ServiceResponse, responses=ERROR_RESPONSES)
async def replace_service_prices(
    id: uuid.UUID, body: ServicePricesPut, session: DbSession, user: PricerUser
) -> ServiceResponse:
    return _to_response(
        await guard(
            _service(session, user).replace_prices(
                id,
                [p.to_input() for p in body.prices],
                pricing_mode=body.pricing_mode,
                deposit_bps=body.deposit_bps,
                set_deposit="deposit_bps" in body.model_fields_set,
            )
        )
    )


@services.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=IDEMPOTENT_RESPONSES)
async def delete_service(id: uuid.UUID, session: DbSession, user: CatalogAndPricerUser) -> Response:
    await guard(_service(session, user).void_service(id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── /templates ───────────────────────────────────────────────────────────────

templates = APIRouter()


def _template_response(kind: TemplateKind, entries: list[TemplateEntry]) -> TemplateResponse:
    grouped: dict[int, TemplateCategoryResponse] = {}
    for entry in entries:
        term = entry.term
        category = grouped.setdefault(
            term.section,
            TemplateCategoryResponse(name=term.section_title, sort_order=term.section, items=[]),
        )
        category.items.append(
            TemplateTermResponse(
                glossary_code=term.code,
                name=term.term,
                type=term.type,
                english=term.english,
                synonyms=list(term.synonyms),
                already_added=entry.already_added,
            )
        )
    return TemplateResponse(kind=kind, categories=list(grouped.values()))


@templates.get("/{kind}", response_model=TemplateResponse, responses=ERROR_RESPONSES)
async def get_template(
    kind: TemplateKind, owner: OwnerUser, session: DbSession
) -> TemplateResponse:
    entries = await TemplateService(session, owner.tenant_id, owner.id).get_template(kind)
    return _template_response(kind, entries)


def _apply_response(result: ApplyResult) -> TemplateApplyResponse:
    return TemplateApplyResponse(
        created=[_to_response(v) for v in result.created],
        skipped=[
            TemplateSkippedResponse(
                glossary_code=s.glossary_code, service_id=s.service.id, reason=s.reason.value
            )
            for s in result.skipped
        ],
        categories_created=[
            ServiceCategoryResponse.model_validate(c) for c in result.categories_created
        ],
    )


@templates.post("/apply", response_model=TemplateApplyResponse, responses=ERROR_RESPONSES)
async def apply_template(
    body: TemplateApply, owner: OwnerUser, session: DbSession
) -> TemplateApplyResponse:
    result = await guard(
        TemplateService(session, owner.tenant_id, owner.id).apply(
            body.kind,
            [
                TemplateItemInput(
                    glossary_code=i.glossary_code,
                    pricing_mode=i.pricing_mode,
                    prices=[p.to_input() for p in i.prices],
                )
                for i in body.items
            ],
        )
    )
    return _apply_response(result)
