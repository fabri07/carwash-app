"""Alta de un negocio — FASE-4-CONTRATO Y3.

`provision_tenant` es el **único** camino para crear un negocio: lo usan `/auth/register`,
los tests y `scripts/seed_staging.py`. Crece por etapas, una por PR, y la migración de cada
etapa completa los tenants que ya existen:

- 4.1: tenant, `OWNER` y los perfiles de permisos por defecto;
- 4.2: `slug` y `tenant_settings`;
- 4.4: la copia de la base de vehículos.

Como los servicios de F3: `flush`, nunca `commit`; la transacción es del llamador.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.errors import AlreadyExistsError, SlugUnavailableError
from app.domain.permissions import DEFAULT_PROFILES, sorted_permissions
from app.domain.roles import Role
from app.domain.slug import candidate_slug
from app.persistence.db._savepoint import (
    SavepointConflictError,
    guarded_savepoint,
    unique_violation_classifier,
)
from app.persistence.db.tenant_context import set_tenant_context
from app.persistence.models.permission_profile import PermissionProfile
from app.persistence.models.tenant import Tenant, TenantSettings
from app.persistence.models.user import User

EMAIL_TAKEN = unique_violation_classifier(
    "email", constraint="uq_users_email", columns=("users.email",)
)
SLUG_TAKEN = unique_violation_classifier(
    "slug", constraint="ux_tenants_slug", columns=("tenants.slug",)
)
#: Intentos de slug: el limpio y después con sufijo aleatorio. Dos negocios que se registran a
#: la vez con el mismo nombre chocan una vez; chocar cuatro seguidas es un error de verdad.
SLUG_ATTEMPTS = 4


@dataclass(frozen=True)
class ProvisionedTenant:
    tenant: Tenant
    owner: User
    profiles: dict[str, PermissionProfile]
    settings: TenantSettings


async def provision_tenant(
    session: AsyncSession,
    *,
    name: str,
    owner_email: str,
    owner_password_hash: str,
    tenant_id: uuid.UUID | None = None,
) -> ProvisionedTenant:
    """Crea el negocio con su dueño. `AlreadyExistsError("email")` si el email ya existe.

    El slug (la URL pública) sale del nombre; si ya lo tiene otro negocio vivo se reintenta con
    un sufijo aleatorio, así el registro nunca falla por un nombre repetido.

    El contexto de tenant va ANTES del primer INSERT: el `WITH CHECK` de `tenants`
    (`id = contexto`) y el de las demás tablas (`tenant_id = contexto`) lo exigen. Por eso
    el uuid se genera en Python primero.
    """
    tenant_id = tenant_id or uuid.uuid4()
    await set_tenant_context(session, tenant_id)
    tenant = await _add_tenant(session, tenant_id, name.strip())

    owner = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        email=owner_email.strip().lower(),
        password_hash=owner_password_hash,
        role=Role.OWNER,
    )
    try:
        async with guarded_savepoint(session, EMAIL_TAKEN):
            session.add(owner)
    except SavepointConflictError as exc:
        raise AlreadyExistsError("email") from exc

    profiles = {
        profile_name: PermissionProfile(
            tenant_id=tenant_id,
            name=profile_name,
            permissions=[p.value for p in sorted_permissions(permissions)],
        )
        for profile_name, permissions in DEFAULT_PROFILES.items()
    }
    session.add_all(profiles.values())
    settings = TenantSettings(tenant_id=tenant_id)
    session.add(settings)
    await session.flush()
    return ProvisionedTenant(tenant=tenant, owner=owner, profiles=profiles, settings=settings)


async def _add_tenant(session: AsyncSession, tenant_id: uuid.UUID, name: str) -> Tenant:
    for attempt in range(SLUG_ATTEMPTS):
        suffix = None if attempt == 0 else uuid.uuid4().hex[:6]
        tenant = Tenant(id=tenant_id, name=name, slug=candidate_slug(name, suffix=suffix))
        try:
            async with guarded_savepoint(session, SLUG_TAKEN):
                session.add(tenant)
        except SavepointConflictError:
            continue
        return tenant
    raise SlugUnavailableError("could not allocate a free business slug")
