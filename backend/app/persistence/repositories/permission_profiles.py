import uuid

from sqlalchemy import func, select

from app.persistence.models.permission_profile import PermissionProfile
from app.persistence.models.user import User
from app.persistence.repositories.base import BaseRepository


class PermissionProfileRepository(BaseRepository[PermissionProfile]):
    model = PermissionProfile

    async def count_alive_staff(self, profile_id: uuid.UUID, tenant_id: uuid.UUID) -> int:
        """Empleados vivos con este perfil: un perfil en uso no se anula (409)."""
        total = await self._session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.tenant_id == tenant_id,
                User.permission_profile_id == profile_id,
                User.voided_at.is_(None),
            )
        )
        return int(total or 0)
