"""User — reescrito. Lo que NO se copia de Véktor `models/user.py`: la PK `user_id`
(`:21`), el `tenant_id` a mano (`:23`) y `role_code` como `Text` (`:32`)."""

from sqlalchemy import Enum, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.roles import ROLE_ENUM_NAME, Role
from app.persistence.db.base import TenantScopedModel
from app.persistence.db.mixins import enum_values
from app.persistence.models._constraints import parent_key, voidable_table_args


class User(TenantScopedModel):
    __tablename__ = "users"
    #: `UNIQUE (tenant_id, id)`: F3 apunta a usuarios con FKs compuestas (responsable,
    #: actor de cada evento). Cambio aditivo sobre la tabla de F2 (migración 0002).
    __table_args__ = voidable_table_args(parent_key())

    #: Único global: el login es por email solo, sin elegir tenant.
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
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
    #: Se incrementa en logout: invalida del lado del servidor todo token emitido
    #: antes (ADR-0009 punto 7) sin agregar una tabla de tokens a la Fase 2.
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
