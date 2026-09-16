"""
Fixtures de pytest del backend.

Adaptado de Véktor (`app/tests/conftest.py`). Se conservan textualmente
`FakeRedis`, `db_engine` session-scoped con SQLite in-memory + `StaticPool` y **el
truco de aiosqlite**, `db_session` con transacción externa +
`join_transaction_mode="create_savepoint"`, `isolated_db_engine`, el override de
bcrypt a `rounds=4` y `client`. Los fixtures de dominio se reescriben como
`tenant_a`/`tenant_b`, `owner`/`staff` y **cookies** en vez de headers (ADR-0009).

El par `tenant_b` + `cookies_b` (alias `second_tenant` / `second_auth_headers`) es
el andamio del test cruzado de T3.
"""

import os

# La config se lee al importar `app`: el entorno de test se fija ANTES.
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["SENTRY_DSN"] = ""
os.environ["REDIS_URL"] = "redis://localhost:1/0"  # nunca se usa: FakeRedis
os.environ["CORS_ORIGINS"] = "https://app.carwash.test"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-not-for-production-use-0123456789"

import time  # noqa: E402
import uuid  # noqa: E402
from collections.abc import AsyncGenerator  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine  # noqa: E402

from app.config.settings import get_settings  # noqa: E402
from app.domain.roles import Role  # noqa: E402
from app.main import create_app  # noqa: E402
from app.persistence.db.base import Base  # noqa: E402
from app.persistence.db.redis import get_redis  # noqa: E402
from app.persistence.db.session import get_db_session  # noqa: E402
from app.persistence.models import DummyResource, Tenant, User  # noqa: E402
from app.utils import security as _security  # noqa: E402
from app.utils.cookies import ACCESS_COOKIE  # noqa: E402
from app.utils.security import create_access_token, hash_password  # noqa: E402

pytest_plugins = ["app.tests.conftest_pg"]

# bcrypt con work factor 4 (mínimo válido) SOLO en tests: rounds=12 (~0.3s por
# hash) es uno de los cuellos del tiempo total. La seguridad de prod no cambia.
_security._pwd_context.update(bcrypt__rounds=4)

TEST_PASSWORD = "correct-horse-battery"


class FakeRedis:
    """Stub async de Redis con TTL real."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str | None, float | None]] = {}

    def _is_expired(self, exp: float | None) -> bool:
        return exp is not None and time.monotonic() > exp

    async def get(self, key: str) -> str | None:
        value, exp = self._store.get(key, (None, None))
        if self._is_expired(exp):
            self._store.pop(key, None)
            return None
        return value

    async def exists(self, key: str) -> int:
        return 1 if (await self.get(key)) is not None else 0

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and (await self.get(key)) is not None:
            return False
        exp_time = time.monotonic() + ex if ex is not None else None
        self._store[key] = (value, exp_time)
        return True

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if (await self.get(key)) is not None:
                deleted += 1
            self._store.pop(key, None)
        return deleted

    async def incr(self, key: str) -> int:
        val, exp = self._store.get(key, ("0", None))
        if self._is_expired(exp):
            val, exp = "0", None
        new_val = int(val or 0) + 1
        self._store[key] = (str(new_val), exp)
        return new_val

    async def expire(self, key: str, ttl: int) -> bool:
        entry = self._store.get(key)
        if entry is None:
            return False
        self._store[key] = (entry[0], time.monotonic() + ttl)
        return True

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        pass


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


# ── Base de test (SQLite in-memory) ───────────────────────────────────────────
#
# Patrón rápido:
#   - `db_engine` es scope="session": el schema se crea UNA vez por proceso (por
#     worker de xdist), no una vez por test.
#   - `db_session` abre una transacción EXTERNA sobre la única conexión
#     (StaticPool) y le da al test una AsyncSession con
#     `join_transaction_mode="create_savepoint"`: los `commit()` del test son
#     SAVEPOINTs; al terminar, el rollback externo deja la DB prístina.
#
# Si un test necesita commits reales fuera de la transacción del test, usar
# `isolated_db_engine`.
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="session")
async def db_engine() -> AsyncGenerator[AsyncEngine, None]:
    from sqlalchemy import event  # noqa: PLC0415
    from sqlalchemy.pool import StaticPool  # noqa: PLC0415

    engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=StaticPool)

    # Receta oficial de SQLAlchemy para SAVEPOINTs sobre pysqlite/aiosqlite:
    # su emulación de transacciones comitea implícitamente (rompería el patrón
    # rollback-por-test dejando fugas entre tests). Se desactiva y SQLAlchemy
    # emite BEGIN explícito.
    @event.listens_for(engine.sync_engine, "connect")
    def _do_connect(dbapi_connection, connection_record):
        dbapi_connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _do_begin(conn):
        conn.exec_driver_sql("BEGIN")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    async with db_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if trans.is_active:
                await trans.rollback()


@pytest_asyncio.fixture
async def isolated_db_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Engine efímero con schema propio (lento). Solo para tests que commitean de verdad."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ── Entidades de prueba ───────────────────────────────────────────────────────


async def make_tenant(session: AsyncSession, name: str) -> Tenant:
    tenant = Tenant(id=uuid.uuid4(), name=name)
    session.add(tenant)
    await session.flush()
    return tenant


async def make_user(session: AsyncSession, tenant: Tenant, role: Role, email: str) -> User:
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(TEST_PASSWORD),
        role=role,
    )
    session.add(user)
    await session.flush()
    return user


def session_cookies(user: User) -> dict[str, str]:
    token = create_access_token(
        {"sub": str(user.id), "tenant_id": str(user.tenant_id), "ver": user.token_version}
    )
    return {ACCESS_COOKIE: token}


def cookie_header(cookies: dict[str, str]) -> dict[str, str]:
    return {"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())}


@pytest_asyncio.fixture
async def tenant_a(db_session: AsyncSession) -> Tenant:
    return await make_tenant(db_session, "Lavadero A")


@pytest_asyncio.fixture
async def tenant_b(db_session: AsyncSession) -> Tenant:
    return await make_tenant(db_session, "Lavadero B")


@pytest_asyncio.fixture
async def owner(db_session: AsyncSession, tenant_a: Tenant) -> User:
    return await make_user(db_session, tenant_a, Role.OWNER, "owner-a@example.com")


@pytest_asyncio.fixture
async def staff(db_session: AsyncSession, tenant_a: Tenant) -> User:
    return await make_user(db_session, tenant_a, Role.STAFF, "staff-a@example.com")


@pytest_asyncio.fixture
async def owner_b(db_session: AsyncSession, tenant_b: Tenant) -> User:
    return await make_user(db_session, tenant_b, Role.OWNER, "owner-b@example.com")


@pytest.fixture
def cookies_a(owner: User) -> dict[str, str]:
    return session_cookies(owner)


@pytest.fixture
def cookies_b(owner_b: User) -> dict[str, str]:
    return session_cookies(owner_b)


@pytest.fixture
def staff_cookies(staff: User) -> dict[str, str]:
    return session_cookies(staff)


@pytest_asyncio.fixture
async def dummy_a(db_session: AsyncSession, tenant_a: Tenant) -> DummyResource:
    """Recurso dummy del tenant A."""
    dummy = DummyResource(id=uuid.uuid4(), tenant_id=tenant_a.id, name="dummy de A")
    db_session.add(dummy)
    await db_session.flush()
    return dummy


@pytest.fixture
def id_de_a(dummy_a: DummyResource) -> uuid.UUID:
    return dummy_a.id


# Alias con los nombres de Véktor. `*_headers` llevan la cookie en el header
# `Cookie`, para usarse como `client.get(..., headers=auth_headers)`.
@pytest.fixture
def sample_tenant(tenant_a: Tenant) -> Tenant:
    return tenant_a


@pytest.fixture
def second_tenant(tenant_b: Tenant) -> Tenant:
    return tenant_b


@pytest.fixture
def auth_headers(cookies_a: dict[str, str]) -> dict[str, str]:
    return cookie_header(cookies_a)


@pytest.fixture
def second_auth_headers(cookies_b: dict[str, str]) -> dict[str, str]:
    return cookie_header(cookies_b)


@pytest.fixture
def staff_headers(staff_cookies: dict[str, str]) -> dict[str, str]:
    return cookie_header(staff_cookies)


# ── Cliente HTTP ──────────────────────────────────────────────────────────────


def _reset_limiter() -> None:
    from app.api.rate_limit import limiter  # noqa: PLC0415

    limiter.reset()


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession, fake_redis: FakeRedis
) -> AsyncGenerator[AsyncClient, None]:
    _reset_limiter()
    app = create_app()

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def override_redis() -> FakeRedis:
        return fake_redis

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_redis] = override_redis

    # https: las cookies de sesión son `Secure` fuera de `local`, y el jar de httpx
    # no las devuelve sobre http.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as ac:
        yield ac


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch):
    """Cambia variables de entorno y recarga `get_settings()`; restaura al salir."""

    def _set(**values: str) -> None:
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    yield _set
    monkeypatch.undo()
    get_settings.cache_clear()
