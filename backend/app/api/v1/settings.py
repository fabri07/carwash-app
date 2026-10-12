"""Configuración del negocio — FASE-4-CONTRATO §3.2 y §4 (PR 4.2).

- `GET /settings`: cualquier usuario del tenant (los empleados ven la seña y las reglas que
  aplican al reservar).
- `PATCH /settings`: solo el `OWNER` (Y5). Cambio parcial; el slug tomado → 409 `SLUG_TAKEN`.

No hay `{id}`: la fila es la del tenant del token, así que no hay recurso ajeno que adivinar.
"""

from fastapi import APIRouter, status

from app.api.v1.deps import DbSession, OwnerUser, TenantMember
from app.api.v1.errors import ERROR_RESPONSES, IDEMPOTENT_RESPONSES, ApiError
from app.application.services.errors import AlreadyExistsError
from app.application.services.settings_service import SettingsService
from app.domain.errors import ErrorCode
from app.schemas.configuracion import SettingsResponse, SettingsUpdate

router = APIRouter()


@router.get("", response_model=SettingsResponse, responses=ERROR_RESPONSES)
async def get_settings(user: TenantMember, session: DbSession) -> SettingsResponse:
    business = await SettingsService(session, user.tenant_id, user.id).get()
    return SettingsResponse.of(business)


@router.patch(
    "",
    response_model=SettingsResponse,
    responses=IDEMPOTENT_RESPONSES,
)
async def update_settings(
    body: SettingsUpdate, owner: OwnerUser, session: DbSession
) -> SettingsResponse:
    try:
        business = await SettingsService(session, owner.tenant_id, owner.id).update(
            body.changes(), slug=body.slug
        )
    except AlreadyExistsError as exc:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            ErrorCode.SLUG_TAKEN,
            "That address is already used by another business.",
        ) from exc
    return SettingsResponse.of(business)
