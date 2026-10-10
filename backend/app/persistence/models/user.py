"""User — reescrito. Lo que NO se copia de Véktor `models/user.py`: la PK `user_id`
(`:21`), el `tenant_id` a mano (`:23`) y `role_code` como `Text` (`:32`).

FASE-4-CONTRATO §3.1: el dueño (`OWNER`) entra con email; los empleados (`STAFF`) con
usuario y contraseña (D4-3), el email es opcional. Cada `STAFF` tiene un perfil de
permisos; el `OWNER` no tiene perfil (tiene todo, Y5).
"""

import uuid

from sqlalchemy import Boolean, Enum, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.roles import ROLE_ENUM_NAME, Role
from app.persistence.db.base import TenantScopedModel
from app.persistence.db.mixins import enum_values
from app.persistence.models._constraints import (
    check,
    parent_key,
    pg_check,
    tenant_fk,
    tenant_index,
    voidable_table_args,
)

#: Formato de `username` (§3.1). El schema de la API lo valida igual; la base es la red.
USERNAME_PATTERN = "^[a-z0-9._-]{3,40}$"


class User(TenantScopedModel):
    __tablename__ = "users"
    #: `UNIQUE (tenant_id, id)`: F3 apunta a usuarios con FKs compuestas (responsable,
    #: actor de cada evento). Cambio aditivo sobre la tabla de F2 (migración 0003).
    __table_args__ = voidable_table_args(
        parent_key(),
        tenant_fk("permission_profile_id", "permission_profiles"),
        tenant_index("users", "permission_profile_id"),
        check("(role = 'OWNER') = (permission_profile_id IS NULL)", "perfil_segun_rol"),
        check("email IS NOT NULL OR username IS NOT NULL", "con_identificador"),
        check("role <> 'OWNER' OR email IS NOT NULL", "owner_con_email"),
        pg_check(f"username ~ '{USERNAME_PATTERN}'", "username_formato"),
    )

    #: Único global: el login es con un solo campo, sin elegir tenant. Nullable desde F4
    #: (un empleado puede no tener email); el `OWNER` siempre lo tiene (CHECK).
    email: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    #: Único global, en minúscula. Nunca contiene `@`, así que no choca con un email en
    #: el login por `identifier`.
    username: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[Role] = mapped_column(
        Enum(
            Role,
            name=ROLE_ENUM_NAME,
            native_enum=True,
            validate_strings=True,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    permission_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    #: El dueño crea al empleado (o le resetea la clave) con una clave inicial: hasta
    #: cambiarla, la sesión solo puede ver `/auth/me`, cambiar la clave, refrescar y salir.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: Se incrementa en logout: invalida del lado del servidor todo token emitido
    #: antes (ADR-0009 punto 7) sin agregar una tabla de tokens a la Fase 2.
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
