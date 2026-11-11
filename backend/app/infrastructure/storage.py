"""Almacenamiento de objetos — FASE-4-CONTRATO Y9 (PR 4.5).

Una interfaz (`ObjectStorage`) y dos implementaciones:

- `R2Storage`: Cloudflare R2 por su API compatible con S3, con **boto3**. Se eligió boto3 y no
  una firma SigV4 propia sobre `httpx`: es el cliente que Cloudflare documenta para R2, el que ya
  usa el dueño en app-gim (`django-storages`), y la firma propia sería código criptográfico nuestro
  que solo se puede probar contra R2 real. El costo es una dependencia más pesada y llamadas
  síncronas, que corren en el threadpool para no frenar el event loop.
- `InMemoryStorage`: para tests (y para probar a mano sin R2, con un override de la dependencia).

`get_storage` es la dependencia de FastAPI: devuelve `None` si faltan variables de R2, y el
endpoint responde 503. Los tests la reemplazan con `app.dependency_overrides`.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from starlette.concurrency import run_in_threadpool

from app.config.settings import Settings, get_settings
from app.observability.logger import get_logger

logger = get_logger(__name__)

#: Los objetos son inmutables (la clave lleva el hash del contenido): se cachean un año.
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"


class StorageError(Exception):
    """El almacenamiento respondió con error (red, credenciales, bucket)."""


class ObjectStorage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def delete(self, key: str) -> None: ...


@dataclass
class StoredObject:
    data: bytes
    content_type: str
    cache_control: str


class InMemoryStorage:
    """Un dict. `objects` queda a la vista para que los tests verifiquen qué se subió."""

    def __init__(self) -> None:
        self.objects: dict[str, StoredObject] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = StoredObject(data, content_type, IMMUTABLE_CACHE_CONTROL)

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


def _boto3_client(settings: Settings) -> Any:
    import boto3.session  # noqa: PLC0415  # carga diferida: boto3 tarda en importar
    from botocore.config import Config  # noqa: PLC0415

    # Una `Session` propia y no `boto3.client(...)`: este usa la sesión por defecto del módulo,
    # que no es segura entre hilos (FastAPI resuelve dependencias sync en el threadpool).
    return boto3.session.Session().client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=15,
        ),
    )


class R2Storage:
    """R2 por la API S3. `client_factory` existe para los tests: nunca tocan la red."""

    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[[Settings], Any] | None = None,
    ) -> None:
        self._bucket = settings.R2_BUCKET
        self._client = (client_factory or _boto3_client)(settings)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            await run_in_threadpool(
                self._client.put_object,
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                CacheControl=IMMUTABLE_CACHE_CONTROL,
            )
        except Exception as exc:
            # El detalle (que puede traer el bucket o el endpoint) va al log, no a la respuesta.
            logger.warning("storage.put_failed", key=key, error=type(exc).__name__)
            raise StorageError("could not store the object") from exc

    async def delete(self, key: str) -> None:
        try:
            await run_in_threadpool(self._client.delete_object, Bucket=self._bucket, Key=key)
        except Exception as exc:
            logger.warning("storage.delete_failed", key=key, error=type(exc).__name__)
            raise StorageError("could not delete the object") from exc


_r2_lock = threading.Lock()
_r2_instance: R2Storage | None = None


def _r2_storage() -> R2Storage:
    """Un solo cliente por proceso, creado bajo lock (lo pueden pedir dos hilos a la vez). El
    cliente de boto3 ya creado sí es seguro entre hilos."""
    global _r2_instance
    with _r2_lock:
        if _r2_instance is None:
            _r2_instance = R2Storage(get_settings())
        return _r2_instance


def _reset_r2_storage() -> None:
    """Para tests: olvida el cliente (p. ej. tras cambiar la configuración)."""
    global _r2_instance
    with _r2_lock:
        _r2_instance = None


_partial_config_warned = False


def get_storage() -> ObjectStorage | None:
    """Dependencia de FastAPI. `None` = sin R2 configurado (el endpoint responde 503)."""
    global _partial_config_warned
    settings = get_settings()
    if settings.r2_configured:
        return _r2_storage()
    if not _partial_config_warned and any(value.strip() for value in settings.r2_values):
        # Una sola vez por proceso: es un error de despliegue, no un evento por request.
        _partial_config_warned = True
        logger.warning("storage.r2_partially_configured")
    return None
