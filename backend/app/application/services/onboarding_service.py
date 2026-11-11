"""Wizard de alta — FASE-4-CONTRATO §6 y §11.9 (PR 4.6).

`status` mira el catálogo del negocio y dice qué falta y dónde reanudar; `complete` lo exige y
marca `tenant_settings.onboarding_completed_at`. Las reglas puras (pasos, requisitos, reanudación)
viven en `domain/onboarding.py`.

Solo cuenta lo **vivo**: un tamaño, servicio, precio, puesto, franja o medio de pago anulado no
cumple ningún requisito. Un precio vale si su servicio y su tamaño están vivos; uno que apunta a un
tamaño anulado no suma ni falta.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import ColumnElement, Exists, exists, select

from app.application.services._base import ServiceBase, as_aware
from app.application.services.service_catalog_service import ServiceCatalogService
from app.domain.branding import DEFAULT_FONT, DEFAULT_PALETTE
from app.domain.exceptions import GuardFailedError
from app.domain.onboarding import (
    REQUIREMENT_STEP,
    OnboardingRequirement,
    OnboardingStep,
    resume_step,
    steps_done,
)
from app.domain.roles import Role
from app.persistence.db.base import TenantScopedModel
from app.persistence.models.catalog import BusinessHours, PaymentMethod, Resource
from app.persistence.models.tenant import TenantSettings
from app.persistence.models.user import User
from app.utils.datetime_utils import utcnow


@dataclass(frozen=True)
class MissingRequirement:
    requirement: OnboardingRequirement
    #: Para `SERVICE_PRICES`: servicios vivos sin precio para algún tamaño vivo.
    service_ids: tuple[uuid.UUID, ...] = ()

    @property
    def step(self) -> OnboardingStep:
        return REQUIREMENT_STEP[self.requirement]


@dataclass(frozen=True)
class OnboardingStatus:
    completed_at: datetime | None
    done: dict[OnboardingStep, bool]
    missing: tuple[MissingRequirement, ...]
    resume_step: OnboardingStep = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "resume_step", resume_step(self.done))


class OnboardingIncompleteError(GuardFailedError):
    """Falta algún requisito de §6. La API lo traduce a 409 `ONBOARDING_INCOMPLETE` con la lista."""

    def __init__(self, missing: tuple[MissingRequirement, ...]) -> None:
        self.missing = missing
        super().__init__(
            "onboarding incomplete: " + ", ".join(m.requirement.value for m in missing)
        )


class OnboardingService(ServiceBase):
    async def status(self) -> OnboardingStatus:
        await self._enter()
        settings = await self._settings()
        return await self._status(settings)

    async def complete(self) -> OnboardingStatus:
        """Marca el alta como terminada. Idempotente: si ya lo estaba, no toca la fecha.

        Toma la fila de `tenant_settings` con `FOR UPDATE` (en Postgres; SQLite lo ignora): dos
        "Terminar" a la vez (dos pestañas, un doble toque) se ordenan y el segundo ve la fecha del
        primero en vez de pisarla. Los requisitos se cuentan con la fila tomada.
        """
        await self._enter()
        settings = await self._session.scalar(
            select(TenantSettings)
            .where(TenantSettings.tenant_id == self._tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if settings is None:  # pragma: no cover  # la crea `provision_tenant`
            settings = await self._settings()
        current = await self._status(settings)
        if settings.onboarding_completed_at is not None:
            return current
        if current.missing:
            raise OnboardingIncompleteError(current.missing)
        settings.onboarding_completed_at = utcnow()
        await self._session.flush()
        return OnboardingStatus(
            completed_at=settings.onboarding_completed_at, done=current.done, missing=()
        )

    # ── Lo que hay cargado ───────────────────────────────────────────────────

    def _any(self, model: type[TenantScopedModel], *where: ColumnElement[bool]) -> Exists:
        """`EXISTS` de una fila viva del negocio que cumple `where` (se juntan en un SELECT)."""
        return exists().where(model.tenant_id == self._tenant_id, model.voided_at.is_(None), *where)

    async def _status(self, settings: TenantSettings) -> OnboardingStatus:
        # Precios: la misma regla que `missing_price_size_ids` (tres consultas).
        coverage = await ServiceCatalogService(
            self._session, self._tenant_id, self._actor_user_id
        ).price_coverage()
        # Lo demás, en un solo SELECT de booleanos.
        row = (
            await self._session.execute(
                select(
                    self._any(Resource).label("resource"),
                    self._any(BusinessHours).label("hours"),
                    self._any(PaymentMethod, PaymentMethod.for_income.is_(True)).label("payment"),
                    self._any(User, User.role == Role.STAFF).label("staff"),
                )
            )
        ).one()

        missing: list[MissingRequirement] = []
        if not coverage.size_ids:
            missing.append(MissingRequirement(OnboardingRequirement.VEHICLE_SIZE))
        if not coverage.service_ids:
            missing.append(MissingRequirement(OnboardingRequirement.SERVICE))
        if coverage.unpriced_service_ids:
            missing.append(
                MissingRequirement(
                    OnboardingRequirement.SERVICE_PRICES,
                    service_ids=coverage.unpriced_service_ids,
                )
            )
        if not row.resource:
            missing.append(MissingRequirement(OnboardingRequirement.RESOURCE))
        if not row.hours:
            missing.append(MissingRequirement(OnboardingRequirement.BUSINESS_HOURS))
        if not row.payment:
            missing.append(MissingRequirement(OnboardingRequirement.PAYMENT_METHOD))

        has_data = {
            OnboardingStep.BUSINESS: any(
                getattr(settings, f)
                for f in (
                    "address",
                    "whatsapp_e164",
                    "instagram_url",
                    "welcome_text",
                    "cancellation_policy",
                )
            ),
            OnboardingStep.BRANDING: (
                settings.palette != DEFAULT_PALETTE
                or settings.font != DEFAULT_FONT
                or settings.logo_key is not None
            ),
            OnboardingStep.SIZES: bool(coverage.size_ids),
            OnboardingStep.SERVICES: bool(coverage.service_ids),
            OnboardingStep.SCHEDULE: bool(row.resource or row.hours),
            OnboardingStep.PAYMENTS: bool(row.payment),
            OnboardingStep.DEPOSIT: settings.deposit_enabled,
            OnboardingStep.STAFF: bool(row.staff),
        }
        done = steps_done(has_data, {m.requirement for m in missing})
        completed_at = settings.onboarding_completed_at
        return OnboardingStatus(
            completed_at=as_aware(completed_at) if completed_at is not None else None,
            done=done,
            missing=tuple(missing),
        )
