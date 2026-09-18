import uuid
from dataclasses import dataclass

from sqlalchemy import select, text

from app.domain.roles import Role
from app.persistence.db.rls import AUTH_LOOKUP_FUNCTION
from app.persistence.db.tenant_context import is_postgres
from app.persistence.models.user import User
from app.persistence.repositories.base import BaseRepository


@dataclass(frozen=True)
class LoginCandidate:
    """Lo mínimo que el login necesita antes de conocer el tenant. Nada más."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    password_hash: str
    token_version: int
    role: Role


class UserRepository(BaseRepository[User]):
    model = User

    async def find_login_candidate(self, email: str) -> LoginCandidate | None:
        """Búsqueda de identidad SIN tenant. Solo para login.

        En PostgreSQL pasa por la función `SECURITY DEFINER` `auth_lookup_user`: el rol
        de runtime no puede leer `users` sin tenant en contexto, ni siquiera acá. En
        SQLite (suite rápida, sin RLS) es un SELECT equivalente.
        """
        normalized = email.lower()
        if is_postgres(self._session):
            row = (
                await self._session.execute(
                    text(
                        "SELECT id, tenant_id, password_hash, token_version, role "
                        f"FROM {AUTH_LOOKUP_FUNCTION}(:email)"
                    ),
                    {"email": normalized},
                )
            ).one_or_none()
            if row is None:
                return None
            return LoginCandidate(
                id=row.id,
                tenant_id=row.tenant_id,
                password_hash=row.password_hash,
                token_version=row.token_version,
                role=Role(row.role),
            )
        user = await self._session.scalar(
            select(User).where(User.email == normalized, User.voided_at.is_(None))
        )
        if user is None:
            return None
        return LoginCandidate(
            id=user.id,
            tenant_id=user.tenant_id,
            password_hash=user.password_hash,
            token_version=user.token_version,
            role=user.role,
        )

    async def get_active(self, id: uuid.UUID, tenant_id: uuid.UUID) -> User | None:
        return await self.get_by_id(id, tenant_id)
