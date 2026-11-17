"""Agenda del día por puesto — FASE-6-CONTRATO §3.1 (PR 6.1).

`GET /agenda/day?date=YYYY-MM-DD` con `AGENDA_VER`: los turnos (todavía sin job) y los jobs del día
**del negocio** (`tenants.timezone`, no el del navegador), con estado, hora, cliente, servicios y
saldo, para armar una columna por puesto (D6-1). Una carga retrospectiva aparece en el día de sus
horas reales. Un día es una pantalla: no se pagina (ver `truncated`).
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.v1.deps import DbSession, require_permission
from app.api.v1.errors import IDEMPOTENT_RESPONSES, ApiError
from app.application.services.operation_service import OperationService
from app.domain.errors import ErrorCode
from app.domain.exceptions import GuardFailedError
from app.domain.permissions import Permission
from app.persistence.models.user import User
from app.schemas.operation import DayResponse

ViewerUser = Annotated[User, Depends(require_permission(Permission.AGENDA_VER))]

router = APIRouter()


@router.get("/day", response_model=DayResponse, responses=IDEMPOTENT_RESPONSES)
async def get_day(date: dt.date, user: ViewerUser, session: DbSession) -> DayResponse:
    try:
        view = await OperationService(session, user.tenant_id, user.id).day(date)
    except GuardFailedError as exc:  # fecha fuera de rango
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, str(exc)) from exc
    return DayResponse.of(view)
