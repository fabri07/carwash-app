"""Wizard de alta — FASE-4-CONTRATO §4 (fila 4.6), §6 y §11.9.

- `GET /onboarding/status` (OWNER): por paso, si está hecho; los requisitos que faltan; y el paso
  donde se reanuda el wizard.
- `POST /onboarding/complete` (OWNER): exige los requisitos de §6 y marca el alta como terminada.
  Si falta algo, **409 `ONBOARDING_INCOMPLETE`** con `detail.missing` (la misma forma que
  `missing` del estado). Si ya estaba terminada responde 200 sin tocar la fecha.

Solo el dueño: el wizard configura el negocio (Y5). Un empleado ve un aviso en el panel (el front lo
decide con `onboarding_completed` de `GET /auth/me`), no este estado.
"""

from fastapi import APIRouter, status

from app.api.v1.deps import DbSession, OwnerUser
from app.api.v1.errors import ERROR_RESPONSES, ApiError
from app.application.services.onboarding_service import (
    MissingRequirement,
    OnboardingIncompleteError,
    OnboardingService,
    OnboardingStatus,
)
from app.domain.errors import ErrorCode
from app.domain.onboarding import REQUIRED_STEPS, STEPS
from app.schemas.onboarding import (
    OnboardingIncompleteResponse,
    OnboardingMissing,
    OnboardingStatusResponse,
    OnboardingStepStatus,
)

router = APIRouter()


def _missing(items: tuple[MissingRequirement, ...]) -> list[OnboardingMissing]:
    return [
        OnboardingMissing(requirement=m.requirement, step=m.step, service_ids=list(m.service_ids))
        for m in items
    ]


def to_response(current: OnboardingStatus) -> OnboardingStatusResponse:
    return OnboardingStatusResponse(
        completed=current.completed_at is not None,
        completed_at=current.completed_at,
        resume_step=current.resume_step,
        steps=[
            OnboardingStepStatus(step=s, required=s in REQUIRED_STEPS, done=current.done[s])
            for s in STEPS
        ],
        missing=_missing(current.missing),
    )


def incomplete(missing: tuple[MissingRequirement, ...]) -> ApiError:
    """El 409 con la lista: el envelope `{detail: {code, message}}` de siempre más `missing`."""
    return ApiError(
        status.HTTP_409_CONFLICT,
        ErrorCode.ONBOARDING_INCOMPLETE,
        "The business setup is not complete yet.",
        extra={"missing": [m.model_dump(mode="json") for m in _missing(missing)]},
    )


@router.get("/status", response_model=OnboardingStatusResponse, responses=ERROR_RESPONSES)
async def get_status(owner: OwnerUser, session: DbSession) -> OnboardingStatusResponse:
    return to_response(await OnboardingService(session, owner.tenant_id, owner.id).status())


@router.post(
    "/complete",
    response_model=OnboardingStatusResponse,
    responses={**ERROR_RESPONSES, 409: {"model": OnboardingIncompleteResponse}},
)
async def complete(owner: OwnerUser, session: DbSession) -> OnboardingStatusResponse:
    try:
        done = await OnboardingService(session, owner.tenant_id, owner.id).complete()
    except OnboardingIncompleteError as exc:
        raise incomplete(exc.missing) from exc
    return to_response(done)
