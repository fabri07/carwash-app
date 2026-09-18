"""Inicialización y cierre de dependencias (lifespan de FastAPI).

Portado de Véktor: arranque **tolerante**. uvicorn levanta aunque la base o Redis
estén caídos, para que `/health` responda y Railway no entre en un ciclo de
reinicios. Se saca lo de Celery.

Excepción a la tolerancia (H4): en staging y producción, si la base responde pero el
rol de runtime puede saltearse RLS (superusuario, `BYPASSRLS` o dueño de `users`),
el arranque **aborta**. Correr la app con el rol de migración anula el aislamiento
entero en silencio; es preferible no levantar.
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.observability.logger import get_logger

logger = get_logger(__name__)


class UnsafeDatabaseRoleError(RuntimeError):
    """El rol con el que corre la app puede saltearse RLS."""


@dataclass(frozen=True)
class RoleAudit:
    role: str
    is_superuser: bool
    bypasses_rls: bool
    owns_users: bool

    @property
    def problems(self) -> list[str]:
        found = []
        if self.is_superuser:
            found.append("es superusuario")
        if self.bypasses_rls:
            found.append("tiene BYPASSRLS")
        if self.owns_users:
            found.append("es dueño (o miembro del dueño) de la tabla users")
        return found


_ROLE_AUDIT_SQL = """
SELECT current_user AS role, r.rolsuper, r.rolbypassrls,
       COALESCE((
           SELECT pg_has_role(current_user, c.relowner, 'MEMBER')
           FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relname = 'users' AND n.nspname = 'public'
       ), false) AS owns_users
FROM pg_roles r WHERE r.rolname = current_user
"""


async def audit_runtime_role(conn: AsyncConnection) -> RoleAudit:
    row = (await conn.execute(text(_ROLE_AUDIT_SQL))).one()
    return RoleAudit(
        role=row.role,
        is_superuser=bool(row.rolsuper),
        bypasses_rls=bool(row.rolbypassrls),
        owns_users=bool(row.owns_users),
    )


async def enforce_safe_runtime_role(conn: AsyncConnection, app_env: str) -> None:
    """Aborta en staging/producción si el rol de runtime se saltea RLS."""
    if app_env not in ("staging", "production") or conn.dialect.name != "postgresql":
        return
    audit = await audit_runtime_role(conn)
    if audit.problems:
        raise UnsafeDatabaseRoleError(
            f"El rol de runtime '{audit.role}' no es seguro para APP_ENV={app_env}: "
            f"{', '.join(audit.problems)}. DATABASE_URL tiene que usar carwash_app."
        )


async def startup() -> None:
    try:
        await _init_database()
    except UnsafeDatabaseRoleError:
        logger.error("bootstrap.database.unsafe_role")
        raise
    except Exception as exc:
        logger.warning("bootstrap.database.unavailable", exc_type=type(exc).__name__)
    try:
        await _init_redis()
    except Exception as exc:
        logger.warning("bootstrap.redis.unavailable", exc_type=type(exc).__name__)
    logger.info("bootstrap.startup.complete")


async def shutdown() -> None:
    from app.persistence.db.engine import engine  # noqa: PLC0415
    from app.persistence.db.redis import close_redis_pool  # noqa: PLC0415

    await engine.dispose()
    await close_redis_pool()
    _flush_sentry()
    logger.info("bootstrap.shutdown.complete")


async def _init_database() -> None:
    from app.config.settings import get_settings  # noqa: PLC0415
    from app.persistence.db.engine import engine  # noqa: PLC0415

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        await enforce_safe_runtime_role(conn, get_settings().APP_ENV)
    logger.info("bootstrap.database.connected")


async def _init_redis() -> None:
    import asyncio  # noqa: PLC0415

    from app.persistence.db.redis import get_redis_pool  # noqa: PLC0415

    await asyncio.wait_for(get_redis_pool().ping(), timeout=5.0)
    logger.info("bootstrap.redis.connected")


def _flush_sentry() -> None:
    """Fuerza el envío de eventos pendientes antes de que el proceso termine."""
    import sentry_sdk  # noqa: PLC0415

    if sentry_sdk.is_initialized():  # pragma: no cover  # requiere DSN real; ver A13
        sentry_sdk.flush(timeout=2)
