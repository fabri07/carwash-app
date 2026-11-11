"""Configuración del negocio — FASE-4-CONTRATO §3.2 y §4 (PR 4.2a).

`get` devuelve la fila de `tenant_settings` y el slug (que vive en `tenants`, Y10). `update`
aplica solo los campos presentes. El slug lo cambia únicamente quien llama con `change_slug=True`
(el endpoint exige `OWNER` para todo el PATCH; el flag existe para que el servicio no decida por
su cuenta).

El % de seña cambia **solo reservas futuras**: el snapshot vive en `booking_items`.
"""

from dataclasses import dataclass
from typing import Any

from app.application.services._base import ServiceBase
from app.application.services.errors import AlreadyExistsError, NotFoundError
from app.domain.exceptions import InvalidParameterError
from app.domain.slug import RESERVED_SLUGS, is_valid_slug
from app.persistence.db._savepoint import (
    SavepointConflictError,
    guarded_savepoint,
    unique_violation_classifier,
)
from app.persistence.models.tenant import Tenant, TenantSettings

_SLUG_TAKEN = unique_violation_classifier(
    "ux_tenants_slug", constraint="ux_tenants_slug", columns=("tenants.slug",)
)


class SlugReservedError(InvalidParameterError):
    """El slug choca con una ruta del sistema (`admin`, `login`…)."""


class SlugTakenError(AlreadyExistsError):
    """Otro negocio vivo ya usa ese slug."""


@dataclass(frozen=True)
class BusinessSettings:
    tenant: Tenant
    settings: TenantSettings


#: Campos de `tenant_settings` editables por PATCH. Quedan afuera `onboarding_completed_at` y
#: `logo_key`, que mueven el wizard y la subida del logo.
EDITABLE_FIELDS: frozenset[str] = frozenset(
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
        "quote_validity_hours",
        "palette",
        "font",
        # F5 (FASE-5-CONTRATO §3.4).
        "booking_horizon_days",
        "slot_step_min",
        "deposit_instructions",
    }
)


class SettingsService(ServiceBase):
    async def get(self) -> BusinessSettings:
        await self._enter()
        tenant = await self._session.get(Tenant, self._tenant_id)
        if tenant is None or tenant.voided_at is not None:
            raise NotFoundError("tenants", self._tenant_id)
        return BusinessSettings(tenant=tenant, settings=await self._settings())

    async def update(self, values: dict[str, Any], *, slug: str | None = None) -> BusinessSettings:
        """`values`: solo claves de `EDITABLE_FIELDS` (el schema ya validó el formato)."""
        await self._enter()
        unknown = set(values) - EDITABLE_FIELDS
        if unknown:
            raise InvalidParameterError(f"fields not editable: {sorted(unknown)}")
        current = await self.get()
        for field, value in values.items():
            setattr(current.settings, field, value)
        if slug is not None and slug != current.tenant.slug:
            await self._change_slug(current.tenant, slug)
        await self._session.flush()
        return current

    async def _change_slug(self, tenant: Tenant, slug: str) -> None:
        if slug in RESERVED_SLUGS:
            raise SlugReservedError(f"slug is reserved: {slug}")
        if not is_valid_slug(slug):
            raise InvalidParameterError(f"invalid slug: {slug}")
        try:
            async with guarded_savepoint(self._session, _SLUG_TAKEN):
                tenant.slug = slug
        except SavepointConflictError as exc:
            raise SlugTakenError("slug already taken") from exc
