"""Endpoint de prueba de Sentry (A13). Solo se monta fuera de producción."""

from fastapi import APIRouter

from app.api.v1.deps import CurrentUser
from app.schemas.common import ErrorResponse

router = APIRouter()


class DebugBoomError(RuntimeError):
    """Error intencional para verificar que Sentry recibe el evento con `tenant_id`."""


@router.post("/boom", response_model=ErrorResponse)
async def boom(current_user: CurrentUser) -> ErrorResponse:
    raise DebugBoomError("boom intencional (A13)")
