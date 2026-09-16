"""Redis — un solo `get_redis`.

Véktor tiene dos (`redis.py` con pool global y `redis_client.py` por request, que
abre y cierra una conexión en cada uno) y usa cada uno en lugares distintos. Acá:
pool global + dependency que NO cierra el pool.
"""

from redis.asyncio import ConnectionPool, Redis

from app.config.settings import get_settings

_pool: ConnectionPool | None = None


def get_redis_pool() -> Redis:
    global _pool  # noqa: PLW0603
    if _pool is None:
        _pool = ConnectionPool.from_url(
            get_settings().REDIS_URL,
            max_connections=20,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
    return Redis(connection_pool=_pool)


async def close_redis_pool() -> None:
    global _pool  # noqa: PLW0603
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def get_redis() -> Redis:
    """Dependency de FastAPI. Devuelve un cliente sobre el pool compartido."""
    return get_redis_pool()
