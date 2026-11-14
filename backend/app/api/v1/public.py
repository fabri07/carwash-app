"""Turnero público, **sin sesión** — FASE-5-CONTRATO §4.2, §6 y §8 (PR 5.2).

`/v1/public/{slug}/…`: el negocio se resuelve con `find_public_tenant_id` (función `SECURITY
DEFINER`, Z7). Un slug inexistente, de un negocio anulado o con el alta sin terminar da **el mismo
404** byte a byte (Z8); un turno que no existe o no es de ese WhatsApp, también (D5-8).

Sin `CurrentUser` a propósito: están en la lista blanca de
`tests/meta/test_rutas_con_sesion_exigen_usuario.py`. La sesión de base no hereda nada de una
cookie que el navegador mande igual: el servicio fija el contexto del negocio del slug.

Límites (slowapi, `shared_limit` con un scope fijo por ruta: el scope por defecto de slowapi es
la URL, y con una URL distinta por slug enumerar slugs no gastaría nunca el cupo): **por IP** los
del contrato (sobre todos los slugs) y además **por slug** (un techo por negocio, para
que muchas IPs no le llenen la agenda a uno). Los errores no devuelven el cuerpo ni datos de
nadie (§8).
"""

import datetime as dt
import uuid
from collections.abc import Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, status

from app.api.rate_limit import limiter
from app.api.v1.deps import DbSession
from app.api.v1.errors import ApiError, duplicate_idempotent, not_found
from app.application.services._base import as_aware
from app.application.services.errors import (
    IdempotencyKeyReusedError,
    NotFoundError,
    SlotTakenError,
)
from app.application.services.public_booking_service import (
    InvalidPhoneError,
    PublicBookingRequest,
    PublicBookingService,
    PublicBookingView,
    ServiceNotBookableError,
    TooManyActiveBookingsError,
)
from app.config.settings import get_settings
from app.domain.branding import DEFAULT_FONT, DEFAULT_PALETTE, FONTS, PALETTES
from app.domain.errors import ErrorCode
from app.domain.exceptions import (
    DomainError,
    GuardFailedError,
    InvalidParameterError,
    InvalidPlateError,
    InvalidTransition,
)
from app.persistence.repositories.public_tenants import find_public_tenant_id
from app.schemas.branding import FontOut, PaletteOut
from app.schemas.common import ErrorResponse
from app.schemas.public import (
    PublicAvailabilityResponse,
    PublicBookingCancel,
    PublicBookingCreate,
    PublicBookingItem,
    PublicBookingLookup,
    PublicBookingResponse,
    PublicBusinessResponse,
    PublicCancellationResponse,
    PublicCatalogResponse,
    PublicServiceOut,
    PublicServicePrice,
    PublicSlot,
    PublicVehicleSize,
)
from app.utils.datetime_utils import utcnow

router = APIRouter()

#: Por IP (contrato §4.2).
READ_LIMIT = "60/minute"
BOOK_LIMIT = "5/minute;20/day"
LOOKUP_LIMIT = "10/minute"
CANCEL_LIMIT = "5/minute"
#: Por negocio (adenda 5.2): techo para muchas IPs contra un mismo slug.
SLUG_READ_LIMIT = "600/minute"
SLUG_BOOK_LIMIT = "60/minute;500/day"
SLUG_LOOKUP_LIMIT = "120/minute"
SLUG_CANCEL_LIMIT = "60/minute"

_E = {"model": ErrorResponse}
READ_RESPONSES: dict[int | str, dict[str, Any]] = {404: _E, 422: _E, 429: _E}
WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {404: _E, 409: _E, 422: _E, 429: _E}

#: Header obligatorio del alta (§4.2 paso 1): un doble envío devuelve el mismo turno.
RequiredIdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=200)
]


def slug_key(request: Request) -> str:
    """Key del límite por negocio: el slug del path, en minúsculas."""
    return "slug:" + str(request.path_params.get("slug", "")).strip().lower()


async def _service(session: DbSession, slug: str) -> PublicBookingService:
    tenant_id = await find_public_tenant_id(session, slug)
    if tenant_id is None:
        raise not_found()
    return PublicBookingService(session, tenant_id)


def _error(status_code: int, code: ErrorCode, message: str) -> ApiError:
    return ApiError(status_code, code, message)


async def _guard[T](call: Coroutine[Any, Any, T]) -> T:
    """Errores del turnero → API. Un mensaje fijo por código: nunca se refleja lo que mandó el
    cliente."""
    try:
        return await call
    except NotFoundError as exc:
        raise not_found() from exc
    except SlotTakenError as exc:
        raise _error(
            status.HTTP_409_CONFLICT, ErrorCode.SLOT_TAKEN, "That time is no longer available."
        ) from exc
    except TooManyActiveBookingsError as exc:
        raise _error(
            status.HTTP_409_CONFLICT,
            ErrorCode.TOO_MANY_ACTIVE_BOOKINGS,
            "This WhatsApp number already has the maximum of active bookings.",
        ) from exc
    except IdempotencyKeyReusedError as exc:
        raise duplicate_idempotent() from exc
    except InvalidTransition as exc:
        raise _error(
            status.HTTP_409_CONFLICT, ErrorCode.CONFLICT, "The booking cannot be cancelled."
        ) from exc
    except ServiceNotBookableError as exc:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ErrorCode.SERVICE_NOT_BOOKABLE,
            "A service cannot be booked online.",
        ) from exc
    except InvalidPhoneError as exc:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ErrorCode.INVALID_PHONE,
            "The WhatsApp number is not valid.",
        ) from exc
    except (GuardFailedError, InvalidParameterError, InvalidPlateError) as exc:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.VALIDATION_ERROR, str(exc)
        ) from exc
    except DomainError as exc:  # pragma: no cover  # red: cualquier otro del dominio es 422
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.VALIDATION_ERROR, "Invalid request."
        ) from exc


def _booking(view: PublicBookingView) -> PublicBookingResponse:
    b = view.booking
    return PublicBookingResponse(
        code=b.code,
        status=b.status,
        # Con zona siempre: SQLite (suite rápida) la pierde al releer; Postgres ya la trae.
        start_at=as_aware(b.start_at),
        end_at=as_aware(b.end_at),
        timezone=view.timezone,
        vehicle_size_label=view.vehicle_size_label,
        services=[
            PublicBookingItem(
                name=i.service_name_snapshot, duration_min=i.duration_min, price_cents=i.price_cents
            )
            for i in view.items
        ],
        total_cents=b.price_cents or 0,
        deposit_cents=view.deposit_cents,
        hold_expires_at=as_aware(b.hold_expires_at) if b.hold_expires_at else None,
        can_cancel=view.can_cancel,
    )


# ── El negocio ───────────────────────────────────────────────────────────────


@router.get("/{slug}", response_model=PublicBusinessResponse, responses=READ_RESPONSES)
@limiter.shared_limit(READ_LIMIT, scope="public.business")
@limiter.shared_limit(SLUG_READ_LIMIT, scope="public.business.slug", key_func=slug_key)
async def get_business(request: Request, slug: str, session: DbSession) -> PublicBusinessResponse:
    profile = await (await _service(session, slug)).profile()
    tenant, settings = profile.tenant, profile.settings
    palette = PALETTES.get(settings.palette) or PALETTES[DEFAULT_PALETTE]
    font = FONTS.get(settings.font) or FONTS[DEFAULT_FONT]
    return PublicBusinessResponse(
        slug=tenant.slug,
        name=tenant.name,
        timezone=tenant.timezone,
        currency=tenant.currency,
        palette=PaletteOut.of(palette),
        font=FontOut.of(font),
        logo_url=get_settings().public_url(settings.logo_key),
        welcome_text=settings.welcome_text,
        address=settings.address,
        whatsapp_e164=settings.whatsapp_e164,
        cancellation_policy=settings.cancellation_policy,
        deposit_enabled=settings.deposit_enabled,
        deposit_instructions=settings.deposit_instructions,
        hold_min=settings.hold_min,
        booking_horizon_days=settings.booking_horizon_days,
    )


@router.get("/{slug}/catalog", response_model=PublicCatalogResponse, responses=READ_RESPONSES)
@limiter.shared_limit(READ_LIMIT, scope="public.catalog")
@limiter.shared_limit(SLUG_READ_LIMIT, scope="public.catalog.slug", key_func=slug_key)
async def get_catalog(request: Request, slug: str, session: DbSession) -> PublicCatalogResponse:
    catalog = await (await _service(session, slug)).catalog()
    return PublicCatalogResponse(
        vehicle_sizes=[PublicVehicleSize(id=s.id, label=s.label) for s in catalog.vehicle_sizes],
        services=[
            PublicServiceOut(
                id=s.id,
                name=s.name,
                category_name=s.category_name,
                pricing_mode=s.pricing_mode,
                bookable=s.bookable,
                deposit_bps=s.deposit_bps,
                prices=[
                    PublicServicePrice(
                        vehicle_size_id=p.vehicle_size_id,
                        price_cents=p.price_cents,
                        duration_min=p.duration_min,
                        deposit_cents=p.deposit_cents,
                    )
                    for p in s.prices
                ],
            )
            for s in catalog.services
        ],
    )


@router.get(
    "/{slug}/availability", response_model=PublicAvailabilityResponse, responses=READ_RESPONSES
)
@limiter.shared_limit(READ_LIMIT, scope="public.availability")
@limiter.shared_limit(SLUG_READ_LIMIT, scope="public.availability.slug", key_func=slug_key)
async def get_availability(
    request: Request,
    slug: str,
    session: DbSession,
    date: dt.date,
    vehicle_size_id: uuid.UUID,
    service_ids: Annotated[list[uuid.UUID], Query(min_length=1, max_length=20)],
) -> PublicAvailabilityResponse:
    service = await _service(session, slug)
    result = await _guard(
        service.availability(
            day=date, vehicle_size_id=vehicle_size_id, service_ids=service_ids, now=utcnow()
        )
    )
    duration = dt.timedelta(minutes=result.duration_min)
    return PublicAvailabilityResponse(
        date=date,
        timezone=result.timezone,
        duration_min=result.duration_min,
        slots=[
            PublicSlot(start_at=s.start_at, end_at=s.start_at + duration) for s in result.starts
        ],
    )


# ── Reservar, ver y cancelar ─────────────────────────────────────────────────


@router.post(
    "/{slug}/bookings",
    response_model=PublicBookingResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_RESPONSES,
)
@limiter.shared_limit(BOOK_LIMIT, scope="public.book")
@limiter.shared_limit(SLUG_BOOK_LIMIT, scope="public.book.slug", key_func=slug_key)
async def create_booking(
    request: Request,
    slug: str,
    body: PublicBookingCreate,
    session: DbSession,
    idempotency_key: RequiredIdempotencyKey,
) -> PublicBookingResponse:
    service = await _service(session, slug)
    view = await _guard(
        service.book(
            PublicBookingRequest(
                vehicle_size_id=body.vehicle_size_id,
                service_ids=body.service_ids,
                start_at=body.start_at,
                name=body.name,
                phone=body.phone,
                plate=body.plate,
                accept_policy=body.accept_policy,
            ),
            idempotency_key=idempotency_key,
            now=utcnow(),
        )
    )
    return _booking(view)


@router.post(
    "/{slug}/bookings/lookup", response_model=PublicBookingResponse, responses=WRITE_RESPONSES
)
@limiter.shared_limit(LOOKUP_LIMIT, scope="public.lookup")
@limiter.shared_limit(SLUG_LOOKUP_LIMIT, scope="public.lookup.slug", key_func=slug_key)
async def lookup_booking(
    request: Request, slug: str, body: PublicBookingLookup, session: DbSession
) -> PublicBookingResponse:
    service = await _service(session, slug)
    return _booking(await _guard(service.lookup(body.code, body.phone)))


@router.post(
    "/{slug}/bookings/cancel", response_model=PublicCancellationResponse, responses=WRITE_RESPONSES
)
@limiter.shared_limit(CANCEL_LIMIT, scope="public.cancel")
@limiter.shared_limit(SLUG_CANCEL_LIMIT, scope="public.cancel.slug", key_func=slug_key)
async def cancel_booking(
    request: Request, slug: str, body: PublicBookingCancel, session: DbSession
) -> PublicCancellationResponse:
    service = await _service(session, slug)
    result = await _guard(service.cancel(body.code, body.phone, reason=body.reason, now=utcnow()))
    return PublicCancellationResponse(
        code=result.booking.code,
        status=result.booking.status,
        classification=result.cancellation.classification,
        deposit_status=result.cancellation.deposit_status,
    )
