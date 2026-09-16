"""Instancia compartida del rate limiter (fuera de `main` para evitar import circular)."""

from slowapi import Limiter

from app.api.v1.deps import rate_limit_key
from app.config.settings import Settings, get_settings


def storage_uri_for(settings: Settings) -> str:
    """M6: en staging/producción el conteo vive en Redis, compartido entre réplicas.
    En memoria de proceso, N réplicas harían el límite N veces más laxo."""
    return settings.REDIS_URL if settings.uses_shared_rate_limit_storage else "memory://"


def build_limiter(settings: Settings) -> Limiter:
    # `key_func=rate_limit_key` y no `get_remote_address`: detrás del edge de Railway,
    # `get_remote_address` devuelve siempre la IP del edge y el límite pasa a ser un
    # techo GLOBAL. Ver `deps.client_ip`.
    return Limiter(
        key_func=rate_limit_key,
        default_limits=["200/minute"],
        storage_uri=storage_uri_for(settings),
        # Si Redis cae, se degrada a memoria en vez de devolver 500 en cada request.
        in_memory_fallback_enabled=True,
    )


limiter = build_limiter(get_settings())
