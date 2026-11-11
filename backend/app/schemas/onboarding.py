"""Wizard de alta — FASE-4-CONTRATO §6 y §11.9 (PR 4.6)."""

import uuid
from datetime import datetime
from typing import Literal

from app.domain.errors import ErrorCode
from app.domain.onboarding import OnboardingRequirement, OnboardingStep
from app.schemas.common import CamelModel


class OnboardingStepStatus(CamelModel):
    step: OnboardingStep
    #: `True` si `POST /onboarding/complete` exige algo de este paso.
    required: bool
    done: bool


class OnboardingMissing(CamelModel):
    requirement: OnboardingRequirement
    #: El paso del wizard donde se resuelve.
    step: OnboardingStep
    #: Solo en `SERVICE_PRICES`: los servicios vivos sin precio para algún tamaño vivo.
    service_ids: list[uuid.UUID] = []


class OnboardingStatusResponse(CamelModel):
    completed: bool
    completed_at: datetime | None
    #: Donde se reanuda el wizard: el primer paso sin hacer (o el último, si están todos).
    resume_step: OnboardingStep
    steps: list[OnboardingStepStatus]
    missing: list[OnboardingMissing]


class OnboardingIncompleteDetail(CamelModel):
    code: Literal[ErrorCode.ONBOARDING_INCOMPLETE]
    message: str
    missing: list[OnboardingMissing]


class OnboardingIncompleteResponse(CamelModel):
    """El 409 de `POST /onboarding/complete`: el envelope de siempre con la lista de faltantes."""

    detail: OnboardingIncompleteDetail
