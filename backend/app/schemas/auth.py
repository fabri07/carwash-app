import uuid
from typing import Annotated

from pydantic import AfterValidator, BaseModel, BeforeValidator, EmailStr, Field

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


class LoginRequest(BaseModel):
    email: Email
    password: Password


class UserResponse(CamelModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: Role


class TenantResponse(CamelModel):
    id: uuid.UUID
    name: str


class MeResponse(BaseModel):
    """Lo que devuelven registro, login, refresh y `/me`. **Nunca** los tokens (ADR-0009)."""

    user: UserResponse
    tenant: TenantResponse
