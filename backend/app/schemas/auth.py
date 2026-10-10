import uuid
from typing import Annotated

from pydantic import AfterValidator, BaseModel, BeforeValidator, EmailStr, Field

from app.domain.permissions import Permission
from app.domain.roles import Role
from app.schemas.common import CamelModel
from app.utils.security import BCRYPT_MAX_BYTES


def _sin_nul(value: object) -> object:
    """`\\x00` en un texto: Postgres lo rechaza con un error de driver (500). Acá es 422."""
    if isinstance(value, str) and "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


def _max_bytes_bcrypt(value: str) -> str:
    """bcrypt ignora lo que pasa de 72 bytes: dos contraseñas distintas validarían igual."""
    if len(value.encode("utf-8")) > BCRYPT_MAX_BYTES:
        raise ValueError(f"must be at most {BCRYPT_MAX_BYTES} bytes in UTF-8")
    return value


Email = Annotated[EmailStr, BeforeValidator(_sin_nul)]
NewPassword = Annotated[
    str,
    BeforeValidator(_sin_nul),
    Field(min_length=8, max_length=BCRYPT_MAX_BYTES),
    AfterValidator(_max_bytes_bcrypt),
]
Password = Annotated[
    str,
    BeforeValidator(_sin_nul),
    Field(min_length=1, max_length=BCRYPT_MAX_BYTES),
    AfterValidator(_max_bytes_bcrypt),
]


class RegisterRequest(BaseModel):
    email: Email
    password: NewPassword
    #: Nombre del negocio. Crea el tenant del que el usuario queda OWNER.
    tenant: Annotated[str, BeforeValidator(_sin_nul), Field(min_length=1, max_length=200)]


#: Email del dueño o usuario del empleado (D4-3): un solo campo. 254 = largo máximo de un
#: email; un usuario tiene como mucho 40.
Identifier = Annotated[str, BeforeValidator(_sin_nul), Field(min_length=1, max_length=254)]


class LoginRequest(BaseModel):
    identifier: Identifier
    password: Password


class ChangePasswordRequest(BaseModel):
    current_password: Password
    new_password: NewPassword


class UserResponse(CamelModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str | None
    username: str | None
    role: Role
    permission_profile_id: uuid.UUID | None


class TenantResponse(CamelModel):
    id: uuid.UUID
    name: str


class MeResponse(BaseModel):
    """Lo que devuelven registro, login, refresh y `/me`. **Nunca** los tokens (ADR-0009).

    `permissions`: lo que el usuario puede hacer ahora (el `OWNER`, todo). El frontend lo
    usa para ocultar menús y botones; la decisión real la toma el servidor en cada request.
    `must_change_password`: mientras sea `true`, solo responden `me`, `change-password`,
    `refresh` y `logout` (FASE-4-CONTRATO §2.3).
    """

    user: UserResponse
    tenant: TenantResponse
    permissions: list[Permission]
    must_change_password: bool
