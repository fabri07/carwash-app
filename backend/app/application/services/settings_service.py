"""Configuración del negocio — FASE-4-CONTRATO §3.2 y §4 (PR 4.2).

`tenant_settings` (1:1) más `tenants.slug` (Y10). Leerla puede cualquier usuario del tenant;
cambiarla, solo el `OWNER` (lo decide la API). Los servicios de turnos, jobs y cotizaciones la
leen con `load_settings` y le pasan los valores al dominio por parámetro (regla de F3).
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services._base import ServiceBase
from app.application.services.errors import AlreadyExistsError, NotFoundError
from app.application.services.tenant_provisioning import SLUG_TAKEN
from app.persistence.db._savepoint import SavepointConflictError, guarded_savepoint
from app.persistence.models.tenant import Tenant, TenantSettings
from app.utils.datetime_utils import utcnow

#: Los campos de `tenant_settings` que se editan por `PATCH /settings`. `palette`, `font`,
#: `logo_key` y `onboarding_completed_at` tienen su propio camino (PR 4.5 y 4.6).
EDITABLE_FIELDS = frozenset(
    {
        "address",
        "whatsapp_e164",
        "instagram_url",
        "welcome_text",
        "cancellation_policy",
        "deposit_enabled",
        "deposit_bps",
        "tolerance_min",
        "late_cancel_threshold_min",
        "hold_min",
        "min_advance_min",
    }
)


@dataclass(frozen=True)
class BusinessSettings:
    tenant: Tenant
    settings: TenantSettings


async def load_settings(
    session: AsyncSession, tenant_id: uuid.UUID, *, for_update: bool = False
) -> TenantSettings:
    """La fila de configuración del tenant. Nace con él (`provision_tenant`, migración 0005)."""
    statement = select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    settings = (await session.execute(statement)).scalar_one_or_none()
    if settings is None:
        raise NotFoundError("tenant_settings", tenant_id)
    return settings


class SettingsService(ServiceBase):
    async def _tenant(self, *, for_update: bool = False) -> Tenant:
        statement = select(Tenant).where(Tenant.id == self._tenant_id)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        tenant = (await self._session.execute(statement)).scalar_one_or_none()
        if tenant is None:
            raise NotFoundError("tenants", self._tenant_id)
        return tenant

    async def get(self) -> BusinessSettings:
        await self._enter()
        return BusinessSettings(
            tenant=await self._tenant(),
            settings=await load_settings(self._session, self._tenant_id),
        )

    async def update(
        self, changes: Mapping[str, object], *, slug: str | None = None
    ) -> BusinessSettings:
        """Cambio parcial: solo las claves presentes en `changes` (un `None` explícito borra un
        campo de texto opcional). `AlreadyExistsError("slug")` si el slug ya es de otro negocio.
        """
        unknown = set(changes) - EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"not editable: {sorted(unknown)}")
        await self._enter()
        tenant = await self._tenant(for_update=slug is not None)
        settings = await load_settings(self._session, self._tenant_id, for_update=True)
        for field, value in changes.items():
            setattr(settings, field, value)
        if slug is not None and slug != tenant.slug:
            try:
                async with guarded_savepoint(self._session, SLUG_TAKEN):
                    tenant.slug = slug
            except SavepointConflictError as exc:
                raise AlreadyExistsError("slug") from exc
            # La respuesta muestra `tenant_settings.updated_at`: el slug también cuenta.
            settings.updated_at = utcnow()
        await self._session.flush()
        return BusinessSettings(tenant=tenant, settings=settings)
