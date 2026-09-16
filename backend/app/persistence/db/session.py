"""Session factory async y dependency de FastAPI.

Adaptado de Véktor: se agrega el `SET LOCAL app.tenant_id` dentro de la
transacción antes del `yield` (ADR-0002) — la línea que Véktor nunca escribió.
El `tenant_id` sale del access token **verificado** de la cookie, nunca del body
ni del path. Sin token válido no se setea nada y las políticas no dejan ver
ninguna fila: el modo por defecto es no ver nada.

L2 — **el contexto sale de la firma del token, no de la base.** Acá no se verifica
que el usuario exista ni que `ver` coincida con `users.token_version`: hacerlo
exigiría leer `users` antes de tener contexto. Por eso **todo endpoint de datos debe
depender de `CurrentUser`** (`api/v1/deps.py`), que sí lo verifica y corta con 401
antes de tocar datos. `app/tests/meta/test_rutas_con_sesion_exigen_usuario.py`
falla si una ruta usa la sesión sin `CurrentUser` y no está en la lista blanca.
"""

import uuid
from collections.abc import AsyncGenerator, Callable

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.persistence.db.engine import engine
from app.persistence.db.tenant_context import set_tenant_context
from app.utils.cookies import access_cookie_name
from app.utils.security import decode_access_token

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


def tenant_id_from_request(request: Request) -> uuid.UUID | None:
    """Tenant del access token de la cookie, o `None` si no hay token válido."""
    token = request.cookies.get(access_cookie_name())
    payload = decode_access_token(token) if token else None
    raw = payload.get("tenant_id") if payload else None
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


def make_session_dependency(
    factory: async_sessionmaker[AsyncSession],
) -> Callable[[Request], AsyncGenerator[AsyncSession, None]]:
    """Construye la dependency sobre una factory. Los tests de Postgres la reusan
    con su propio engine (rol `carwash_app`) para ejercitar exactamente este código."""

    async def _get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
        # Una transacción por request: commit al final, rollback ante excepción.
        # `SET LOCAL` exige transacción abierta; con autocommit se perdería en
        # silencio y las políticas bloquearían todo (falla cerrado).
        async with factory() as session, session.begin():
            tenant_id = tenant_id_from_request(request)
            if tenant_id is not None:
                await set_tenant_context(session, tenant_id)
            yield session

    return _get_db_session


get_db_session = make_session_dependency(async_session_factory)
