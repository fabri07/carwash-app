"""Wizard de alta — FASE-4-CONTRATO §6 y §11.9 (PR 4.6).

`status` mira el catálogo del negocio y dice qué falta y dónde reanudar; `complete` lo exige y
marca `tenant_settings.onboarding_completed_at`. Las reglas puras (pasos, requisitos, reanudación)
viven en `domain/onboarding.py`.

Solo cuenta lo **vivo**: un tamaño, servicio, precio, puesto, franja o medio de pago anulado no
cumple ningún requisito. Un precio vale si su servicio y su tamaño están vivos; uno que apunta a un
tamaño anulado no suma ni falta.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import exists, func, select

from app.application.services._base import ServiceBase, as_aware
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
from app.persistence.models.catalog import (
    BusinessHours,
    PaymentMethod,
    Resource,
    Service,
    ServicePrice,
    VehicleSize,
)
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

    async def _status(self, settings: TenantSettings) -> OnboardingStatus:
        missing: list[MissingRequirement] = []

        size_ids = await self._live_ids(VehicleSize)
        if not size_ids:
            missing.append(MissingRequirement(OnboardingRequirement.VEHICLE_SIZE))

        service_ids = await self._live_ids(Service)
        if not service_ids:
            missing.append(MissingRequirement(OnboardingRequirement.SERVICE))
        unpriced = await self._services_without_full_prices(service_ids, size_ids)
        if unpriced:
            missing.append(
                MissingRequirement(OnboardingRequirement.SERVICE_PRICES, service_ids=unpriced)
            )

        has_resource = await self._any(Resource)
        if not has_resource:
            missing.append(MissingRequirement(OnboardingRequirement.RESOURCE))
        has_hours = await self._any(BusinessHours)
        if not has_hours:
            missing.append(MissingRequirement(OnboardingRequirement.BUSINESS_HOURS))

        has_payment = await self._session.scalar(
            select(
                exists().where(
                    PaymentMethod.tenant_id == self._tenant_id,
                    PaymentMethod.voided_at.is_(None),
                    PaymentMethod.for_income.is_(True),
                )
            )
        )
        if not has_payment:
            missing.append(MissingRequirement(OnboardingRequirement.PAYMENT_METHOD))

        has_staff = await self._session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.tenant_id == self._tenant_id,
                User.voided_at.is_(None),
                User.role == Role.STAFF,
            )
        )

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
            OnboardingStep.SIZES: bool(size_ids),
            OnboardingStep.SERVICES: bool(service_ids),
            OnboardingStep.SCHEDULE: bool(has_resource or has_hours),
            OnboardingStep.PAYMENTS: bool(has_payment),
            OnboardingStep.DEPOSIT: settings.deposit_enabled,
            OnboardingStep.STAFF: bool(has_staff),
        }
        done = steps_done(has_data, {m.requirement for m in missing})
        completed_at = settings.onboarding_completed_at
        return OnboardingStatus(
            completed_at=as_aware(completed_at) if completed_at is not None else None,
            done=done,
            missing=tuple(missing),
        )

    async def _live_ids(self, model: type[TenantScopedModel]) -> list[uuid.UUID]:
        rows = await self._session.scalars(
            select(model.id)
            .where(model.tenant_id == self._tenant_id, model.voided_at.is_(None))
            .order_by(model.id)
        )
        return list(rows)

    async def _any(self, model: type[TenantScopedModel]) -> bool:
        return bool(
            await self._session.scalar(
                select(
                    exists().where(model.tenant_id == self._tenant_id, model.voided_at.is_(None))
                )
            )
        )

    async def _services_without_full_prices(
        self, service_ids: list[uuid.UUID], size_ids: list[uuid.UUID]
    ) -> tuple[uuid.UUID, ...]:
        """Servicios vivos sin precio vivo para algún tamaño vivo (A8), ordenados por id."""
        if not service_ids or not size_ids:
            return ()
        rows = await self._session.execute(
            select(ServicePrice.service_id, ServicePrice.vehicle_size_id).where(
                ServicePrice.tenant_id == self._tenant_id,
                ServicePrice.voided_at.is_(None),
            )
        )
        priced: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        for service_id, size_id in rows:
            priced[service_id].add(size_id)
        sizes = set(size_ids)
        return tuple(sid for sid in service_ids if not sizes <= priced[sid])
