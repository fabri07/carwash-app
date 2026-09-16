"""Fixtures contra **PostgreSQL real** — marca `postgres`.

SQLite no tiene RLS: los tests de ADR-0002 no pueden vivir en la suite rápida.

- El esquema `public` se recrea al inicio de la sesión con dueño `carwash_owner`
  (no superusuario, sin BYPASSRLS) y se migra **con ese rol**, como en el deploy
  (`DATABASE_URL_SYNC`). Así la función `SECURITY DEFINER` del login y el `FORCE`
  se prueban con el mismo dueño que en producción, no con un superusuario que se
  saltea todo.
- `pg_admin_engine`: superusuario del contenedor. Solo prepara y limpia datos.
- `pg_engine`: rol **`carwash_app`** — no dueño, sin `BYPASSRLS`. Es el rol de
  runtime; todo lo que se afirma sobre aislamiento se afirma con este.
- `pg_client`: la app con `get_db_session` construida por
  `make_session_dependency` sobre `pg_engine` — el mismo código que en producción.

Correr:  `PG_TEST_URL=postgresql://carwash:carwash@localhost:5433/carwash \\
          uv run pytest -m postgres -n 0`
Sin `PG_TEST_URL` alcanzable, los tests `postgres` se saltean con el motivo; con
`CI` seteado, **fallan** (en CI un Postgres ausente es un error, no un permiso).
"""

import os
import uuid
from collections.abc import AsyncGenerator, Callable, Coroutine
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.domain.roles import Role
from app.persistence.db.rls import APP_ROLE
from app.persistence.db.session import get_db_session, make_session_dependency

PG_TEST_URL = os.environ.get("PG_TEST_URL", "postgresql://carwash:carwash@localhost:5433/carwash")
APP_ROLE_PASSWORD = "carwash_app_test"
OWNER_ROLE = "carwash_owner"
OWNER_ROLE_PASSWORD = "carwash_owner_test"
TENANT_TABLES = ("users", "dummy_resources", "idempotency_keys")
#: Todas las tablas con RLS (L3: `tenants` incluida).
RLS_TABLES = ("tenants", *TENANT_TABLES)


def _with_driver(url: str, driver: str) -> str:
    return url.replace("postgresql://", f"postgresql+{driver}://", 1)


def _as_role(url: str, role: str, password: str) -> str:
    parts = urlsplit(url)
    netloc = f"{role}:{password}@{parts.hostname}:{parts.port or 5432}"
    return urlunsplit(parts._replace(netloc=netloc))


def _as_app_role(url: str) -> str:
    return _as_role(url, APP_ROLE, APP_ROLE_PASSWORD)


def owner_url() -> str:
    return _as_role(PG_TEST_URL, OWNER_ROLE, OWNER_ROLE_PASSWORD)


def migrate_head(url: str) -> None:
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "app/persistence/migrations"))
    previous = os.environ.get("DATABASE_URL_SYNC")
    os.environ["DATABASE_URL_SYNC"] = _with_driver(url, "psycopg2")
    try:
        command.upgrade(cfg, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL_SYNC", None)
        else:  # pragma: no cover  # solo si el entorno ya traía la variable
            os.environ["DATABASE_URL_SYNC"] = previous


@pytest_asyncio.fixture(scope="session")
async def pg_admin_engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(_with_driver(PG_TEST_URL, "asyncpg"), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover  # depende de que haya Postgres
        await engine.dispose()
        motivo = f"Postgres no disponible en PG_TEST_URL ({type(exc).__name__})"
        if os.environ.get("CI"):
            pytest.fail(motivo)
        pytest.skip(motivo)

    async with engine.begin() as conn:
        for role, password in ((APP_ROLE, APP_ROLE_PASSWORD), (OWNER_ROLE, OWNER_ROLE_PASSWORD)):
            await conn.execute(
                text(
                    "DO $$ BEGIN "
                    f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
                    f"CREATE ROLE {role} LOGIN PASSWORD '{password}' "
                    "NOSUPERUSER NOBYPASSRLS; END IF; END $$"
                )
            )
        # Base de test dedicada: se recrea el esquema entero, con dueño no superusuario.
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text(f"CREATE SCHEMA public AUTHORIZATION {OWNER_ROLE}"))
    migrate_head(owner_url())
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def pg_engine(pg_admin_engine: AsyncEngine) -> AsyncGenerator[AsyncEngine, None]:
    """Engine con el rol de runtime (`carwash_app`)."""
    engine = create_async_engine(
        _as_app_role(_with_driver(PG_TEST_URL, "asyncpg")), pool_size=1, max_overflow=0
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_clean(pg_admin_engine: AsyncEngine) -> AsyncGenerator[None, None]:
    """Los tests de Postgres commitean de verdad: se limpia al terminar cada uno."""
    yield
    async with pg_admin_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(RLS_TABLES)}"))


PgFactory = Callable[..., Coroutine[Any, Any, uuid.UUID]]


@pytest_asyncio.fixture
async def pg_tenant_factory(pg_admin_engine: AsyncEngine, pg_clean: None) -> PgFactory:
    async def _make(name: str = "tenant") -> uuid.UUID:
        tenant_id = uuid.uuid4()
        async with pg_admin_engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
                {"id": tenant_id, "name": name},
            )
        return tenant_id

    return _make


@pytest_asyncio.fixture
async def pg_user_factory(pg_admin_engine: AsyncEngine, pg_clean: None) -> PgFactory:
    from app.utils.security import hash_password  # noqa: PLC0415

    async def _make(tenant_id: uuid.UUID, email: str, role: Role = Role.OWNER) -> uuid.UUID:
        user_id = uuid.uuid4()
        async with pg_admin_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (id, tenant_id, email, password_hash, role) "
                    "VALUES (:id, :tenant_id, :email, :hash, :role)"
                ),
                {
                    "id": user_id,
                    "tenant_id": tenant_id,
                    "email": email,
                    "hash": hash_password("correct-horse-battery"),
                    "role": role.value,
                },
            )
        return user_id

    return _make


@pytest_asyncio.fixture
async def pg_tenant_a(pg_tenant_factory: PgFactory) -> uuid.UUID:
    return await pg_tenant_factory("Lavadero A")


@pytest_asyncio.fixture
async def pg_tenant_b(pg_tenant_factory: PgFactory) -> uuid.UUID:
    return await pg_tenant_factory("Lavadero B")


@pytest_asyncio.fixture
async def pg_dummy_de_a(pg_admin_engine: AsyncEngine, pg_tenant_a: uuid.UUID) -> uuid.UUID:
    dummy_id = uuid.uuid4()
    async with pg_admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO dummy_resources (id, tenant_id, name) VALUES (:id, :t, 'de A')"),
            {"id": dummy_id, "t": pg_tenant_a},
        )
    return dummy_id


@pytest_asyncio.fixture
async def pg_session_factory(pg_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=pg_engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def pg_client(
    pg_session_factory: async_sessionmaker[AsyncSession], pg_clean: None
) -> AsyncGenerator[AsyncClient, None]:
    """La app contra Postgres real con el rol `carwash_app` y RLS activo."""
    from app.api.rate_limit import limiter  # noqa: PLC0415
    from app.main import create_app  # noqa: PLC0415

    limiter.reset()
    app = create_app()
    app.dependency_overrides[get_db_session] = make_session_dependency(pg_session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as ac:
        yield ac
