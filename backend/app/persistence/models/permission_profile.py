"""Perfiles de permisos — FASE-4-CONTRATO §2 y §3.1.

Un perfil es un nombre más un conjunto de `Permission`. El dueño los edita; cada `STAFF`
tiene exactamente uno (`users.permission_profile_id`). No hay tabla de permisos (Y4): los
valores los define el código y la base solo verifica que sean un subconjunto del enum.

Reemplaza a `dummy_resources` como sujeto de los tests HTTP de aislamiento (Y12).
"""

from sqlalchemy import Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.permissions import Permission
from app.persistence.db.base import PGTEXTARRAY, TenantScopedModel
from app.persistence.models._constraints import (
    check,
    parent_key,
    pg_check,
    unique_alive,
    voidable_table_args,
)

#: Literal del CHECK de subconjunto. Lo comparten el modelo y el meta-test que congela el
#: enum; la migración lo escribe a mano (no importa el ORM).
PERMISSIONS_SQL_ARRAY = "ARRAY[" + ", ".join(f"'{p.value}'" for p in Permission) + "]::text[]"

NAME_MAX = 40


class PermissionProfile(TenantScopedModel):
    __tablename__ = "permission_profiles"
    __table_args__ = voidable_table_args(
        parent_key(),
        unique_alive("permission_profiles", "name"),
        check(f"length(name) BETWEEN 1 AND {NAME_MAX}", "nombre_largo"),
        pg_check(f"permissions <@ {PERMISSIONS_SQL_ARRAY}", "permisos_conocidos"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    #: `text[]` en Postgres, JSON en SQLite. Siempre en el orden del enum
    #: (`sorted_permissions`), sin repetidos. `DEFAULT '{}'` en la base (§3.1): un perfil
    #: insertado sin permisos queda vacío, nunca NULL.
    permissions: Mapped[list[str]] = mapped_column(
        PGTEXTARRAY, nullable=False, default=list, server_default=text("'{}'")
    )
