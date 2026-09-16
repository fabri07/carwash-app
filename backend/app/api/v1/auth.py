"""Auth: registro, login, refresh, logout y `/me`.

Reescrito. De Véktor se toma la forma (schemas, códigos de estado, refresh con
rotación); se descarta OAuth, verificación de email, solicitudes de acceso y PIN.
Emite cookies `HttpOnly`, nunca tokens en el body (ADR-0009).

RLS y el orden de las cosas (ADR-0002):
- **Registro** también fija el contexto ANTES de insertar el tenant: `tenants` tiene
  RLS (`id = tenant del contexto`), así que el uuid se genera en Python primero.
- **Login** es el único punto que busca identidad sin conocer el tenant. Lo hace con
  la función `SECURITY DEFINER` `auth_lookup_user` (ver `persistence/db/rls.py`),
  que devuelve solo lo necesario de UNA fila; recién con la contraseña verificada
  fija el tenant y carga el usuario por RLS normal.
- **Refresh** fija el tenant desde el refresh token verificado.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rate_limit import limiter
from app.api.v1.deps import CurrentUser, DbSession
from app.api.v1.errors import ERROR_RESPONSES, ApiError, unauthenticated
from app.config.settings import get_settings
from app.domain.errors import ErrorCode
from app.domain.roles import Role
from app.persistence.db._savepoint import (
    SavepointConflictError,
    guarded_savepoint,
    unique_violation_classifier,
)
from app.persistence.db.tenant_context import set_tenant_context
from app.persistence.models.tenant import Tenant
from app.persistence.models.user import User
from app.persistence.repositories.user_repository import UserRepository
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    RegisterRequest,
    TenantResponse,
    UserResponse,
)
from app.schemas.common import ErrorResponse
from app.utils.cookies import REFRESH_COOKIE_PATH, access_cookie_name, refresh_cookie_name
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    hash_password_async,
    verify_password_async,
)

router = APIRouter()

_EMAIL_TAKEN = unique_violation_classifier(
    "email", constraint="uq_users_email", columns=("users.email",)
)

#: Hash fijo para comparar cuando el email no existe: iguala el tiempo de respuesta
#: y no deja distinguir "email inexistente" de "contraseña incorrecta".
_DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")


def _cookie_options() -> dict[str, Any]:
    settings = get_settings()
    return {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "domain": settings.COOKIE_DOMAIN,
    }


def _set_session_cookies(response: Response, user: User) -> None:
    settings = get_settings()
    claims = {"sub": str(user.id), "tenant_id": str(user.tenant_id), "ver": user.token_version}
    response.set_cookie(
        access_cookie_name(),
        create_access_token(claims),
        max_age=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
        **_cookie_options(),
    )
    response.set_cookie(
        refresh_cookie_name(),
        create_refresh_token(claims),
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path=REFRESH_COOKIE_PATH,
        **_cookie_options(),
    )


def _clear_session_cookies(response: Response) -> None:
    opts = _cookie_options()
    response.delete_cookie(access_cookie_name(), path="/", **opts)
    response.delete_cookie(refresh_cookie_name(), path=REFRESH_COOKIE_PATH, **opts)


async def _me(session: AsyncSession, user: User) -> MeResponse:
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None:  # pragma: no cover  # FK RESTRICT: un usuario no puede quedar sin tenant
        raise unauthenticated()
    return MeResponse(
        user=UserResponse.model_validate(user),
        tenant=TenantResponse.model_validate(tenant),
    )


@router.post(
    "/register",
    response_model=MeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crea un tenant y su usuario OWNER; deja la sesión abierta por cookie",
    responses={409: {"model": ErrorResponse}},
)
@limiter.limit("5/10minutes")
async def register(
    request: Request, response: Response, body: RegisterRequest, session: DbSession
) -> MeResponse:
    tenant_id = uuid.uuid4()
    # Contexto ANTES del primer INSERT: el WITH CHECK de `tenants` (id = contexto) y
    # el de `users` (tenant_id = contexto) lo exigen.
    await set_tenant_context(session, tenant_id)
    tenant = Tenant(id=tenant_id, name=body.tenant.strip())
    session.add(tenant)
    await session.flush()

    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email=body.email.lower(),
        password_hash=await hash_password_async(body.password),
        role=Role.OWNER,
    )
    try:
        async with guarded_savepoint(session, _EMAIL_TAKEN):
            session.add(user)
    except SavepointConflictError as exc:
        raise ApiError(
            status.HTTP_409_CONFLICT, ErrorCode.EMAIL_TAKEN, "Email already registered."
        ) from exc

    _set_session_cookies(response, user)
    return MeResponse(
        user=UserResponse.model_validate(user),
        tenant=TenantResponse.model_validate(tenant),
    )


@router.post(
    "/login",
    response_model=MeResponse,
    summary="Autentica y deja la sesión en cookies HttpOnly",
    responses={401: {"model": ErrorResponse}},
)
@limiter.limit("10/5minutes")
async def login(
    request: Request, response: Response, body: LoginRequest, session: DbSession
) -> MeResponse:
    repo = UserRepository(session)
    candidate = await repo.find_login_candidate(body.email)
    password_ok = await verify_password_async(
        body.password, candidate.password_hash if candidate else _DUMMY_HASH
    )
    if candidate is None or not password_ok:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            ErrorCode.INVALID_CREDENTIALS,
            "Invalid email or password.",
        )

    # Recién con la contraseña verificada se fija el tenant; el usuario completo se
    # carga por la política normal de `users`.
    await set_tenant_context(session, candidate.tenant_id)
    user = await repo.get_active(candidate.id, candidate.tenant_id)
    if user is None:  # pragma: no cover  # carrera: anulado entre la búsqueda y la carga
        raise unauthenticated()
    _set_session_cookies(response, user)
    return await _me(session, user)


async def _user_from_refresh_cookie(request: Request, session: AsyncSession) -> User | None:
    token = request.cookies.get(refresh_cookie_name())
    payload = decode_refresh_token(token) if token else None
    if payload is None:
        return None
    try:
        user_id = uuid.UUID(str(payload.get("sub")))
        tenant_id = uuid.UUID(str(payload.get("tenant_id")))
    except ValueError:
        return None
    await set_tenant_context(session, tenant_id)
    user = await UserRepository(session).get_active(user_id, tenant_id)
    if user is None or payload.get("ver") != user.token_version:
        return None
    return user


@router.post(
    "/refresh",
    response_model=MeResponse,
    summary="Rota access y refresh token a partir de la cookie de refresh",
    responses={401: {"model": ErrorResponse}},
)
async def refresh(request: Request, response: Response, session: DbSession) -> MeResponse:
    user = await _user_from_refresh_cookie(request, session)
    if user is None:
        raise unauthenticated("Invalid or expired refresh token.")
    _set_session_cookies(response, user)
    return await _me(session, user)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoca la sesión del lado del servidor y vence las cookies",
)
async def logout(request: Request, session: DbSession) -> Response:
    # Invalida del lado del servidor (ADR-0009 punto 7): subir `token_version` deja
    # sin efecto todo access y refresh token emitido antes, en cualquier dispositivo.
    user = await _user_from_refresh_cookie(request, session)
    if user is not None:
        user.token_version += 1
        await session.flush()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_session_cookies(response)
    return response


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Usuario y tenant de la sesión actual",
    responses=ERROR_RESPONSES,
)
async def me(current_user: CurrentUser, session: DbSession) -> MeResponse:
    return await _me(session, current_user)
