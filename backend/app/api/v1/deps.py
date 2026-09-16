"""Dependencias de FastAPI: identidad, tenant, roles e IP del cliente.

Adaptado de Véktor (`api/v1/deps.py`). Se conservan `get_current_user` con sus
tags de Sentry (`tenant_id` como tag, `set_user` sin email), `get_current_tenant_id`,
`client_ip` **portado entero y sin tocar** y `rate_limit_key`. Se cambia: el token
se lee de la cookie `HttpOnly`, no del header `Authorization` (ADR-0009), y
`require_role` se tipa `*roles: Role` (ADR-0005). Se sacan el step-up con PIN,
`get_current_tenant`, `require_open_registration` y el lock de mantenimiento.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

import sentry_sdk
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.errors import forbidden, unauthenticated
from app.config.settings import get_settings
from app.domain.roles import Role
from app.observability.logger import bind_request_context, get_logger
from app.persistence.db.session import get_db_session
from app.persistence.models.user import User
from app.persistence.repositories.user_repository import UserRepository
from app.utils.cookies import access_cookie_name
from app.utils.security import decode_access_token

#: `scope="function"` NO es opcional (T3b #1). Con el scope por defecto, FastAPI corre
#: la salida de una dependencia `yield` DESPUÉS de enviar la respuesta: el commit
#: ocurría después del 201/204, un commit fallido se reportaba como éxito (y la cola
#: offline descartaba el ítem), y `/auth/me` justo después del registro podía dar 401.
#: Con `"function"` la transacción se cierra al terminar el endpoint, antes de
#: responder. Lo verifican `tests/api/test_transaccion_antes_de_responder.py` y
#: `tests/meta/test_scope_de_la_sesion.py`.
DbSession = Annotated[AsyncSession, Depends(get_db_session, scope="function")]


async def get_current_user(request: Request, session: DbSession) -> User:
    """Decodifica el access token de la cookie y devuelve el usuario. 401 si no sirve.

    Todos los 401 llevan el MISMO mensaje: distinguir "token inválido" de "usuario
    inexistente" le diría a quien prueba firmas cuándo acertó una (oráculo de firma).
    """
    token = request.cookies.get(access_cookie_name())
    payload = decode_access_token(token) if token else None
    if payload is None:
        raise unauthenticated()

    try:
        user_id = uuid.UUID(str(payload.get("sub")))
        tenant_id = uuid.UUID(str(payload.get("tenant_id")))
    except ValueError as exc:
        raise unauthenticated() from exc

    user = await UserRepository(session).get_active(user_id, tenant_id)
    if user is None or payload.get("ver") != user.token_version:
        raise unauthenticated()

    bind_request_context(tenant_id=user.tenant_id, user_id=user.id)
    # Sentry: tag por negocio, sin email — alcanza con el id para medir cuántos
    # usuarios/tenants distintos pisan un mismo error.
    sentry_sdk.set_tag("tenant_id", str(user.tenant_id))
    sentry_sdk.set_user({"id": str(user.id)})
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_tenant_id(current_user: CurrentUser) -> uuid.UUID:
    """Tenant del usuario autenticado. Es el único origen válido de `tenant_id`."""
    return current_user.tenant_id


CurrentTenantId = Annotated[uuid.UUID, Depends(get_current_tenant_id)]


def require_role(*roles: Role) -> Callable[[User], Awaitable[User]]:
    """Factory de dependencia por rol. Con un string literal en vez de `Role`, mypy falla."""
    allowed = frozenset(roles)

    async def _check(current_user: CurrentUser) -> User:
        if current_user.role not in allowed:
            raise forbidden("Your role is not allowed to perform this action.")
        return current_user

    return _check


#: Header que setea el edge de Railway: UN solo valor, sin la cadena ambigua de
#: `X-Forwarded-For` (que puede traer varias IPs y cuyo primer valor no se puede
#: creer sin una política de proxies confiables).
_HEADER_IP_REAL = "x-real-ip"

#: Key del limiter cuando no se pudo determinar la IP. Vive acá y NO en
#: `client_ip` a propósito: `client_ip` devuelve `None` para decir "no la sé", y
#: `hash_ip(None)` es `None`. Si `client_ip` devolviera este centinela, el
#: `ip_hash` guardado sería el hash de un literal inventado, indistinguible de
#: una IP real y compartido por todos los clientes sin IP.
_SIN_IP = "unknown"

#: Se avisa UNA sola vez por proceso: es una condición de configuración del
#: despliegue, no un evento por request — loguearla en cada uno inundaría.
_aviso_sin_x_real_ip_emitido = False


def _avisar_sin_x_real_ip() -> None:
    """Denuncia una sola vez que el edge no manda `X-Real-IP` en producción.

    Nunca loguea la IP ni el hash: solo que la suposición del diseño no se
    cumple, que es lo único que hace falta saber para ir a mirar.
    """
    global _aviso_sin_x_real_ip_emitido
    if _aviso_sin_x_real_ip_emitido:
        return
    _aviso_sin_x_real_ip_emitido = True
    get_logger(__name__).warning(
        "client_ip.sin_x_real_ip",
        detalle=(
            "En producción no llegó X-Real-IP: el rate limit y el ip_hash caen "
            "a request.client.host, que detrás de un proxy es la IP del edge e "
            "igual para todos los visitantes."
        ),
    )


def client_ip(request: Request) -> str | None:
    """IP del cliente. **Definición única** de "el cliente" en toda la app.

    La comparten el `ip_hash` anti-abuso de los dos formularios públicos
    anónimos (contacto y solicitud de acceso) y la key del rate limiter global
    (`rate_limit_key`). Antes eran dos nociones distintas dentro del MISMO
    handler: el `ip_hash` leía `X-Forwarded-For` y el `@limiter.limit("5/hour")`
    usaba `get_remote_address`, que lo ignora — detrás del edge de Railway eso
    podía volver el 5/hour un techo GLOBAL del único embudo de alta.

    La confianza en el header está atada al DESPLIEGUE, no al header: solo se
    lee `X-Real-IP` en producción, porque cualquiera que alcance uvicorn directo
    puede inventarlo. Fuera de producción (dev y tests) se usa siempre
    `request.client.host`.

    Sin header, degrada a `request.client.host`. Para el limiter eso es
    exactamente lo que hacía `get_remote_address` y no empeora nada; para el
    `ip_hash` **sí** sería una regresión, porque antes leía `X-Forwarded-For`
    incondicionalmente: si el edge mandara solo XFF y no `X-Real-IP`, la columna
    pasaría a guardar un único hash (el del edge) para todos los visitantes, y
    eso no se nota mirando los datos — un hash de la IP del proxy es
    indistinguible de uno de cliente.

    Por eso la suposición se autodenuncia: en producción, la primera vez que
    falte `X-Real-IP` se loguea una advertencia (sin IP ni hash). Si aparece en
    los logs de prod, hay que revisar qué manda el edge — no queda pendiente de
    que alguien se acuerde de ir a mirar.

    `None` cuando no hay forma de saberla: no se inventa un valor.
    """
    if get_settings().is_production:
        real = request.headers.get(_HEADER_IP_REAL)
        if real and real.strip():
            return real.strip()
        _avisar_sin_x_real_ip()
    return request.client.host if request.client else None


def rate_limit_key(request: Request) -> str:
    """Key del rate limiter global — la MISMA IP que hashea el `ip_hash`.

    `slowapi` exige un `str`, así que acá (y solo acá) el "no la sé" se colapsa
    a un centinela: todos los requests sin IP comparten cubeta, que es el
    comportamiento conservador y equivale a lo que ya hacía `get_remote_address`.
    """
    ip = client_ip(request)
    return ip if ip is not None else _SIN_IP
