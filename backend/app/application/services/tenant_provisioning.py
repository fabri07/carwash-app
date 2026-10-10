"""Alta de un negocio — FASE-4-CONTRATO Y3.

`provision_tenant` es el **único** camino para crear un negocio: lo usan `/auth/register`,
los tests y `scripts/seed_staging.py`. Crece por etapas, una por PR, y la migración de cada
etapa completa los tenants que ya existen:

- 4.1: tenant, `OWNER` y los perfiles de permisos por defecto;
- 4.2: `tenant_settings`;
- 4.4: la copia de la base de vehículos.

Como los servicios de F3: `flush`, nunca `commit`; la transacción es del llamador.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.errors import AlreadyExistsError
from app.domain.permissions import DEFAULT_PROFILES, sorted_permissions
from app.domain.roles import Role
from app.persistence.db._savepoint import (
    SavepointConflictError,
    guarded_savepoint,
    unique_violation_classifier,
)
from app.persistence.db.tenant_context import set_tenant_context
from app.persistence.models.permission_profile import PermissionProfile
from app.persistence.models.tenant import Tenant
from app.persistence.models.user import User

EMAIL_TAKEN = unique_violation_classifier(
    "email", constraint="uq_users_email", columns=("users.email",)
)


@dataclass(frozen=True)
class ProvisionedTenant:
    tenant: Tenant
    owner: User
    profiles: dict[str, PermissionProfile]


async def provision_tenant(
    session: AsyncSession,
    *,
    name: str,
    owner_email: str,
    owner_password_hash: str,
    tenant_id: uuid.UUID | None = None,
) -> ProvisionedTenant:
    """Crea el negocio con su dueño. `AlreadyExistsError("email")` si el email ya existe.

    El contexto de tenant va ANTES del primer INSERT: el `WITH CHECK` de `tenants`
    (`id = contexto`) y el de las demás tablas (`tenant_id = contexto`) lo exigen. Por eso
    el uuid se genera en Python primero.
    """
    tenant_id = tenant_id or uuid.uuid4()
    await set_tenant_context(session, tenant_id)
    tenant = Tenant(id=tenant_id, name=name.strip())
    session.add(tenant)
    await session.flush()

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
    await session.flush()
    return ProvisionedTenant(tenant=tenant, owner=owner, profiles=profiles)
