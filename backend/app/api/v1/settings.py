"""Configuración del negocio — FASE-4-CONTRATO §4 (PR 4.2a).

`GET`: cualquier usuario autenticado del negocio (el front la necesita para mostrar la seña y los
umbrales). `PATCH`: solo el `OWNER` (Y5). El `slug` vive en `tenants` pero se edita por acá (Y10).
"""

from fastapi import APIRouter, status

from app.api.v1.deps import AuthenticatedUser, DbSession, OwnerUser
from app.api.v1.errors import ERROR_RESPONSES, IDEMPOTENT_RESPONSES, ApiError, not_found
from app.application.services.errors import NotFoundError
from app.application.services.settings_service import (
    BusinessSettings,
    SettingsService,
    SlugReservedError,
    SlugTakenError,
)
from app.domain.errors import ErrorCode
from app.domain.exceptions import InvalidParameterError
from app.schemas.settings import SettingsResponse, SettingsUpdate

router = APIRouter()


def to_response(current: BusinessSettings) -> SettingsResponse:
    settings, tenant = current.settings, current.tenant
    return SettingsResponse(
        slug=tenant.slug,
        name=tenant.name,
        timezone=tenant.timezone,
        currency=tenant.currency,
        address=settings.address,
        whatsapp_e164=settings.whatsapp_e164,
        instagram_url=settings.instagram_url,
        welcome_text=settings.welcome_text,
        cancellation_policy=settings.cancellation_policy,
        deposit_enabled=settings.deposit_enabled,
        deposit_bps=settings.deposit_bps,
        tolerance_min=settings.tolerance_min,
        late_cancel_threshold_min=settings.late_cancel_threshold_min,
        hold_min=settings.hold_min,
        min_advance_min=settings.min_advance_min,
        quote_validity_hours=settings.quote_validity_hours,
        palette=settings.palette,
        font=settings.font,
        logo_key=settings.logo_key,
        onboarding_completed_at=settings.onboarding_completed_at,
    )


@router.get("", response_model=SettingsResponse, responses=ERROR_RESPONSES)
async def get_settings(user: AuthenticatedUser, session: DbSession) -> SettingsResponse:
    try:
        return to_response(await SettingsService(session, user.tenant_id, user.id).get())
    except NotFoundError as exc:  # pragma: no cover  # el negocio del token siempre existe
        raise not_found() from exc


@router.patch("", response_model=SettingsResponse, responses=IDEMPOTENT_RESPONSES)
async def update_settings(
    body: SettingsUpdate, owner: OwnerUser, session: DbSession
) -> SettingsResponse:
    values = body.model_dump(exclude_unset=True, exclude={"slug"})
    try:
        updated = await SettingsService(session, owner.tenant_id, owner.id).update(
            values, slug=body.slug
        )
    except SlugReservedError as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ErrorCode.SLUG_RESERVED,
            "That address is reserved by the system.",
        ) from exc
    except SlugTakenError as exc:
        raise ApiError(
            status.HTTP_409_CONFLICT, ErrorCode.SLUG_TAKEN, "That address is already in use."
        ) from exc
    except InvalidParameterError as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.VALIDATION_ERROR, str(exc)
        ) from exc
    return to_response(updated)
