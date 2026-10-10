"""Equipo: perfiles de permisos y empleados — FASE-4-CONTRATO §2, §4 (PR 4.1).

Solo el `OWNER` llega acá (Y5): el endpoint lo exige con `require_role`. Reglas:

- un perfil con empleados vivos no se anula (`ProfileInUseError` → 409);
- un empleado es siempre `STAFF`: el `OWNER` no se crea ni se edita por acá (para este
  servicio, su id no existe);
- crear un empleado o resetearle la clave deja `must_change_password`: la clave la eligió
  el dueño y el empleado tiene que cambiarla al entrar;
- anular un empleado o resetearle la clave sube `token_version`: sus sesiones mueren en el
  request siguiente (ADR-0009 punto 7);
- los permisos se leen de la base en cada request (`effective_permissions`), nunca del
  token: cambiar un perfil rige desde el request siguiente.
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services._base import ServiceBase, alive_unique, first_match
from app.application.services.errors import AlreadyExistsError, NotFoundError
from app.domain.exceptions import GuardFailedError, InvalidParameterError
from app.domain.permissions import ALL_PERMISSIONS, Permission, sorted_permissions
from app.domain.roles import Role
from app.domain.void import VoidReason
from app.persistence.db._savepoint import (
    SavepointConflictError,
    guarded_savepoint,
    unique_violation_classifier,
)
from app.persistence.models.permission_profile import PermissionProfile
from app.persistence.models.user import User
from app.persistence.repositories.base import Page
from app.persistence.repositories.permission_profiles import PermissionProfileRepository
from app.persistence.repositories.user_repository import UserRepository
from app.utils.datetime_utils import utcnow

_PROFILE_NAME_TAKEN = alive_unique("permission_profiles", "name")
_USERNAME_TAKEN = unique_violation_classifier(
    "username", constraint="uq_users_username", columns=("users.username",)
)
_EMAIL_TAKEN = unique_violation_classifier(
    "email", constraint="uq_users_email", columns=("users.email",)
)
#: `username` o `email` ya usados: son únicos globales, no por tenant.
_IDENTITY_TAKEN = first_match(_USERNAME_TAKEN, _EMAIL_TAKEN)


class ProfileInUseError(GuardFailedError):
    """El perfil tiene empleados vivos: anularlo los dejaría sin permisos (409)."""


class MissingIdentifierError(InvalidParameterError):
    """El cambio dejaría al empleado sin email ni usuario: no podría entrar (422).

    Pasa con un `STAFF` de antes de F4, que tiene solo email: borrárselo sin darle un
    usuario dispararía `ck_users_con_identificador` como un 500.
    """


def _stored(permissions: Iterable[Permission]) -> list[str]:
    return [p.value for p in sorted_permissions(set(permissions))]


async def effective_permissions(session: AsyncSession, user: User) -> frozenset[Permission]:
    """Lo que el usuario puede hacer **ahora**. El `OWNER` todo (Y5); el `STAFF` lo de su
    perfil vivo; sin perfil vivo, nada (falla cerrado)."""
    if user.role == Role.OWNER:
        return ALL_PERMISSIONS
    if user.permission_profile_id is None:
        return frozenset()
    stored = await session.scalar(
        select(PermissionProfile.permissions).where(
            PermissionProfile.id == user.permission_profile_id,
            PermissionProfile.tenant_id == user.tenant_id,
            PermissionProfile.voided_at.is_(None),
        )
    )
    known = {p.value for p in Permission}
    return frozenset(Permission(v) for v in (stored or []) if v in known)


class TeamService(ServiceBase):
    def __init__(
        self, session: AsyncSession, tenant_id: uuid.UUID, actor_user_id: uuid.UUID
    ) -> None:
        super().__init__(session, tenant_id, actor_user_id)
        self._profiles = PermissionProfileRepository(session)
        self._users = UserRepository(session)

    # ── Perfiles ──────────────────────────────────────────────────────────────

    async def list_profiles(self, *, limit: int, offset: int) -> Page[PermissionProfile]:
        await self._enter()
        return await self._profiles.list_by_tenant(self._tenant_id, limit=limit, offset=offset)

    async def get_profile(self, profile_id: uuid.UUID) -> PermissionProfile:
        await self._enter()
        return await self._require(self._profiles, profile_id)

    async def create_profile(
        self, name: str, permissions: Iterable[Permission]
    ) -> PermissionProfile:
        await self._enter()
        profile = PermissionProfile(
            tenant_id=self._tenant_id, name=name.strip(), permissions=_stored(permissions)
        )
        try:
            async with guarded_savepoint(self._session, _PROFILE_NAME_TAKEN):
                self._session.add(profile)
        except SavepointConflictError as exc:
            raise AlreadyExistsError("permission_profiles: name already taken") from exc
        return profile

    async def update_profile(
        self,
        profile_id: uuid.UUID,
        *,
        name: str | None = None,
        permissions: Iterable[Permission] | None = None,
    ) -> PermissionProfile:
        await self._enter()
        profile = await self._lock(self._profiles, profile_id)
        try:
            async with guarded_savepoint(self._session, _PROFILE_NAME_TAKEN):
                if name is not None:
                    profile.name = name.strip()
                if permissions is not None:
                    profile.permissions = _stored(permissions)
        except SavepointConflictError as exc:
            raise AlreadyExistsError("permission_profiles: name already taken") from exc
        return profile

    async def void_profile(self, profile_id: uuid.UUID) -> PermissionProfile:
        await self._enter()
        profile = await self._lock(self._profiles, profile_id)
        if await self._profiles.count_alive_staff(profile.id, self._tenant_id):
            raise ProfileInUseError("the profile has active staff members")
        profile.voided_at = utcnow()
        profile.void_reason = VoidReason.PEDIDO_DEL_USUARIO
        await self._session.flush()
        return profile

    # ── Empleados ─────────────────────────────────────────────────────────────

    async def list_staff(self, *, limit: int, offset: int) -> Page[User]:
        await self._enter()
        return await self._users.list_staff(self._tenant_id, limit=limit, offset=offset)

    async def get_staff(self, user_id: uuid.UUID) -> User:
        await self._enter()
        return await self._require_staff(user_id)

    async def create_staff(
        self,
        *,
        username: str,
        password_hash: str,
        permission_profile_id: uuid.UUID,
        email: str | None = None,
    ) -> User:
        await self._enter()
        # Con lock, como `void_profile`: sin él, una baja concurrente del perfil dejaría al
        # empleado asignado a un perfil anulado (falla cerrado, pero sin permisos).
        await self._lock(self._profiles, permission_profile_id)
        user = User(
            tenant_id=self._tenant_id,
            username=username.strip().lower(),
            email=email.strip().lower() if email else None,
            password_hash=password_hash,
            role=Role.STAFF,
            permission_profile_id=permission_profile_id,
            must_change_password=True,
        )
        await self._insert_identity(user)
        return user

    async def update_staff(
        self,
        user_id: uuid.UUID,
        *,
        username: str | None = None,
        email: str | None = None,
        clear_email: bool = False,
        permission_profile_id: uuid.UUID | None = None,
    ) -> User:
        await self._enter()
        user = await self._require_staff(user_id, lock=True)
        if permission_profile_id is not None:
            await self._lock(self._profiles, permission_profile_id)
        new_username = username.strip().lower() if username is not None else user.username
        new_email = None if clear_email else (email.strip().lower() if email else user.email)
        if new_username is None and new_email is None:
            raise MissingIdentifierError("staff member needs a username or an email")
        try:
            async with guarded_savepoint(self._session, _IDENTITY_TAKEN):
                user.username = new_username
                user.email = new_email
                if permission_profile_id is not None:
                    user.permission_profile_id = permission_profile_id
        except SavepointConflictError as exc:
            raise AlreadyExistsError(exc.constraint) from exc
        return user

    async def void_staff(self, user_id: uuid.UUID) -> User:
        await self._enter()
        user = await self._require_staff(user_id, lock=True)
        user.voided_at = utcnow()
        user.void_reason = VoidReason.DESACTIVADO
        user.token_version += 1
        await self._session.flush()
        return user

    async def reset_password(self, user_id: uuid.UUID, password_hash: str) -> User:
        await self._enter()
        user = await self._require_staff(user_id, lock=True)
        user.password_hash = password_hash
        user.must_change_password = True
        user.token_version += 1
        await self._session.flush()
        return user

    async def _require_staff(self, user_id: uuid.UUID, *, lock: bool = False) -> User:
        user = (
            await self._users.get_for_update(user_id, self._tenant_id)
            if lock
            else await self._users.get_by_id(user_id, self._tenant_id)
        )
        if user is None or user.role != Role.STAFF:
            raise NotFoundError("users", user_id)
        return user

    async def _insert_identity(self, user: User) -> None:
        try:
            async with guarded_savepoint(self._session, _IDENTITY_TAKEN):
                self._session.add(user)
        except SavepointConflictError as exc:
            raise AlreadyExistsError(exc.constraint) from exc
