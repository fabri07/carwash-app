"""
Utilidades de JWT y contraseñas.

Portado de Véktor: JWT HS256 con la validación del claim `type` (un refresh token
no sirve como access token) y bcrypt con `rounds=12`. Cambios:
- `iss` / `aud` por ambiente (M2), emitidos y verificados.
- la config se lee en cada llamada, no al importar.
- `hash_password_async` / `verify_password_async`: bcrypt es CPU puro (~0,3 s);
  corrido en el event loop frena a todos los requests concurrentes (M6).
- bcrypt solo mira los primeros 72 bytes: el schema rechaza contraseñas más largas
  para que dos contraseñas distintas no validen igual (L1).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext
from starlette.concurrency import run_in_threadpool

from app.config.settings import get_settings

#: Límite de bcrypt. Todo lo que exceda se ignoraría en silencio.
BCRYPT_MAX_BYTES = 72

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


# ── Password ──────────────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


async def hash_password_async(plain: str) -> str:
    return await run_in_threadpool(hash_password, plain)


async def verify_password_async(plain: str, hashed: str) -> bool:
    return await run_in_threadpool(verify_password, plain, hashed)


# ── JWT ───────────────────────────────────────────────────────────────────────


def _create_token(payload: dict[str, Any], expires_delta: timedelta) -> str:
    settings = get_settings()
    to_encode = payload.copy()
    to_encode["exp"] = datetime.now(UTC) + expires_delta
    to_encode["iss"] = settings.jwt_issuer
    to_encode["aud"] = settings.jwt_audience
    return str(jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM))


def create_access_token(payload: dict[str, Any]) -> str:
    return _create_token(
        {**payload, "type": "access"},
        timedelta(minutes=get_settings().JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(payload: dict[str, Any]) -> str:
    return _create_token(
        {**payload, "type": "refresh"},
        timedelta(days=get_settings().JWT_REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_access_token(token: str) -> dict[str, Any] | None:
    return _decode_token(token, expected_type="access")


def decode_refresh_token(token: str) -> dict[str, Any] | None:
    return _decode_token(token, expected_type="refresh")


def _decode_token(token: str, expected_type: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require_iss": True, "require_aud": True, "require_exp": True},
        )
    except JWTError:
        return None
    if payload.get("type") != expected_type:
        return None
    return payload
