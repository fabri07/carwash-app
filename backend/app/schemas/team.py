"""Equipo: perfiles de permisos y empleados — FASE-4-CONTRATO §2 y §4 (PR 4.1).

`extra="ignore"` en los cuerpos de entrada: un `tenant_id` inyectado se descarta en
silencio. El tenant sale del token, nunca del cuerpo.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, StringConstraints

from app.domain.permissions import Permission
from app.domain.roles import Role
from app.persistence.models.user import USERNAME_PATTERN
from app.schemas.auth import Email, NewPassword, _sin_nul
from app.schemas.common import CamelModel


def _nombre(value: str) -> str:
    """Sin espacios alrededor y no vacío: `"   "` no es un nombre (y la base lo rechazaría
    con un CHECK, que llegaría como 500)."""
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


ProfileName = Annotated[
    str,
    BeforeValidator(_sin_nul),
    StringConstraints(max_length=40),
    AfterValidator(_nombre),
]


def _sin_repetidos(value: list[Permission]) -> list[Permission]:
    if len(set(value)) != len(value):
        raise ValueError("permissions must not repeat")
    return value


Permissions = Annotated[list[Permission], AfterValidator(_sin_repetidos)]


def _lower(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


Username = Annotated[
    str,
    BeforeValidator(_sin_nul),
    BeforeValidator(_lower),
    StringConstraints(pattern=USERNAME_PATTERN),
]


class PermissionProfileCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: ProfileName
    permissions: Permissions


class PermissionProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: ProfileName | None = None
    permissions: Permissions | None = None


class PermissionProfileResponse(CamelModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    permissions: list[Permission]
    created_at: datetime
    updated_at: datetime


class StaffCreate(BaseModel):
    """El dueño elige el usuario y una clave inicial; el empleado la cambia al entrar."""

    model_config = ConfigDict(extra="ignore")

    username: Username
    password: NewPassword
    permission_profile_id: uuid.UUID
    email: Email | None = None


class StaffUpdate(BaseModel):
    """Campos ausentes no se tocan. `email: null` explícito borra el email."""

    model_config = ConfigDict(extra="ignore")

    username: Username | None = None
    email: Email | None = None
    permission_profile_id: uuid.UUID | None = None


class StaffPasswordReset(BaseModel):
    password: NewPassword


class StaffResponse(CamelModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    username: str | None
    email: str | None
    role: Role
    permission_profile_id: uuid.UUID | None
    must_change_password: bool
    created_at: datetime
    updated_at: datetime
