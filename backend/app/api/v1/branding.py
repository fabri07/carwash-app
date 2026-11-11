"""Personalización: catálogo, logo y paleta sugerida — FASE-4-CONTRATO §3.5, §4, A11 y §11.8.

- `GET /branding/catalog`: paletas y tipografías (en código). Cualquier usuario autenticado: el
  layout del front las necesita para pintar la marca del negocio.
- `POST /branding/logo` (multipart, OWNER): valida la imagen, la sube a R2 con clave por hash
  (Y9), guarda `logo_key` y devuelve la URL pública y la paleta sugerida. Sin R2: 503
  `STORAGE_UNAVAILABLE`.
- `POST /branding/suggest-palette` (multipart, OWNER): la misma validación, no guarda nada. No
  necesita R2.
- `DELETE /branding/logo` (OWNER): quita el logo.

**Tamaño:** el cuerpo lo corta antes `BodyLimitMiddleware` (FastAPI lee el multipart antes de
autenticar). **Conexiones:** la subida no retiene una conexión de la base durante la I/O: la
transacción del request (la de la autenticación) se cierra apenas se autentica; Pillow corre en el
threadpool; la clave se guarda en una transacción corta propia y recién después se sube a R2. Si
R2 falla, otra transacción corta deshace la clave (si nadie la cambió entretanto).

**Borrado del objeto anterior** (al reemplazar o quitar el logo): en una tarea de fondo, después
de la respuesta, con la fila del negocio tomada (`FOR UPDATE`) y solo si el logo vigente ya no es
esa clave. Como la subida guarda la clave **antes** de subir el objeto, una subida concurrente que
vuelva a la misma imagen (A → B → A) o bien ya la guardó (y el borrado no ocurre) o espera al
borrado y sube el objeto después. Si el borrado falla queda un huérfano público e inofensivo.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Response, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.api.v1.deps import AuthenticatedUser, DbSession, OwnerUser
from app.api.v1.errors import ERROR_RESPONSES, ApiError
from app.application.services.branding_service import (
    MAX_LOGO_BYTES,
    MAX_LOGO_SIDE,
    MIN_LOGO_SIDE,
    BrandingService,
    ImageTooLargeError,
    ImageTooSmallError,
    InvalidImageError,
    LogoImage,
    inspect_logo,
)
from app.config.settings import get_settings
from app.domain.branding import (
    DEFAULT_FONT,
    DEFAULT_PALETTE,
    FONTS,
    PALETTES,
    suggest_palette,
)
from app.domain.errors import ErrorCode
from app.infrastructure.storage import ObjectStorage, StorageError, get_storage
from app.observability.logger import get_logger
from app.persistence.db.session import SessionFactory, get_session_factory
from app.schemas.branding import (
    BrandingCatalog,
    FontOut,
    LogoResponse,
    PaletteOut,
    PaletteSuggestion,
)
from app.schemas.common import ErrorResponse

router = APIRouter()
logger = get_logger(__name__)

Storage = Annotated[ObjectStorage | None, Depends(get_storage)]
Sessions = Annotated[SessionFactory, Depends(get_session_factory)]
LogoFile = Annotated[
    UploadFile, File(description="PNG, JPEG o WebP; ≤ 2 MB; de 200 a 4096 píxeles de lado.")
]

_IMAGE_RESPONSES = {
    **ERROR_RESPONSES,
    413: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_UPLOAD_RESPONSES = {**_IMAGE_RESPONSES, 503: {"model": ErrorResponse}}

_CATALOG = BrandingCatalog(
    palettes=[PaletteOut.of(p) for p in PALETTES.values()],
    fonts=[FontOut.of(f) for f in FONTS.values()],
    default_palette=DEFAULT_PALETTE,
    default_font=DEFAULT_FONT,
)


@router.get("/catalog", response_model=BrandingCatalog, responses=ERROR_RESPONSES)
async def get_catalog(_user: AuthenticatedUser) -> BrandingCatalog:
    return _CATALOG


async def _read_logo(file: UploadFile) -> LogoImage:
    # Se lee un byte de más: alcanza para saber que se pasó sin cargar un archivo enorme entero.
    data = await file.read(MAX_LOGO_BYTES + 1)
    try:
        # Pillow es CPU sincrónica: fuera del event loop.
        return await run_in_threadpool(inspect_logo, data)
    except ImageTooLargeError as exc:
        raise ApiError(
            status.HTTP_413_CONTENT_TOO_LARGE,
            ErrorCode.FILE_TOO_LARGE,
            f"The image must weigh at most 2 MB and measure at most {MAX_LOGO_SIDE} pixels "
            "per side.",
        ) from exc
    except ImageTooSmallError as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ErrorCode.IMAGE_TOO_SMALL,
            f"The image must be at least {MIN_LOGO_SIDE}x{MIN_LOGO_SIDE} pixels.",
        ) from exc
    except InvalidImageError as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ErrorCode.INVALID_IMAGE,
            "The file must be a PNG, JPEG or WebP image.",
        ) from exc


def _suggestion(logo: LogoImage) -> PaletteSuggestion:
    if logo.dominant is None:
        return PaletteSuggestion(palette=DEFAULT_PALETTE, dominant_color=None)
    r, g, b = logo.dominant
    return PaletteSuggestion(
        palette=suggest_palette(logo.dominant).code, dominant_color=f"#{r:02x}{g:02x}{b:02x}"
    )


def _storage_unavailable() -> ApiError:
    return ApiError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        ErrorCode.STORAGE_UNAVAILABLE,
        "Logo storage is not available right now.",
    )


async def discard_unused_logo(
    sessions: SessionFactory, storage: ObjectStorage, tenant_id: uuid.UUID, key: str
) -> None:
    """Borra `key` de R2 si el negocio ya no la usa, con la fila tomada mientras borra.

    Corre como tarea de fondo: nada de lo que pase acá puede llegar al cliente (que ya tiene su
    respuesta), así que todo error termina en el log.
    """
    try:
        async with sessions() as session:
            service = BrandingService(session, tenant_id, None)
            if not await service.lock_and_check_in_use(key):
                await storage.delete(key)
            await session.commit()
    except StorageError:
        pass  # ya quedó en el log de `storage`; un huérfano no rompe nada
    except Exception as exc:  # noqa: BLE001  # tarea de fondo: no hay a quién propagarlo
        logger.warning("branding.discard_failed", error=type(exc).__name__)


@router.post("/logo", response_model=LogoResponse, responses=_UPLOAD_RESPONSES)
async def upload_logo(
    file: LogoFile,
    owner: OwnerUser,
    session: DbSession,
    sessions: Sessions,
    storage: Storage,
    background: BackgroundTasks,
) -> LogoResponse:
    if storage is None:
        raise _storage_unavailable()
    tenant_id, actor_id = owner.tenant_id, owner.id
    # Ya se autenticó: se cierra la transacción del request y se devuelve su conexión al pool
    # antes de decodificar y de hablar con R2. Esa sesión no se vuelve a usar.
    await session.commit()

    logo = await _read_logo(file)
    key = logo.key_for(tenant_id)
    async with sessions() as tx:
        previous = await BrandingService(tx, tenant_id, actor_id).set_logo(key)
        await tx.commit()
    try:
        await storage.put(key, logo.data, logo.content_type)
    except StorageError as exc:
        async with sessions() as tx:
            await BrandingService(tx, tenant_id, actor_id).revert_logo(key, previous)
            await tx.commit()
        raise _storage_unavailable() from exc
    if previous is not None:
        background.add_task(discard_unused_logo, sessions, storage, tenant_id, previous)
    logger.info("branding.logo_uploaded", bytes=len(logo.data), format=logo.extension)
    return LogoResponse(
        logo_key=key, logo_url=get_settings().public_url(key), suggestion=_suggestion(logo)
    )


@router.post("/suggest-palette", response_model=PaletteSuggestion, responses=_IMAGE_RESPONSES)
async def suggest_palette_for(
    file: LogoFile, _owner: OwnerUser, session: DbSession
) -> PaletteSuggestion:
    await session.commit()  # no hace falta la base para decodificar: se suelta la conexión
    return _suggestion(await _read_logo(file))


@router.delete("/logo", status_code=status.HTTP_204_NO_CONTENT, responses=ERROR_RESPONSES)
async def delete_logo(
    owner: OwnerUser,
    session: DbSession,
    sessions: Sessions,
    storage: Storage,
    background: BackgroundTasks,
) -> Response:
    """Funciona sin R2 (la base deja de apuntar al logo); el objeto se borra solo si hay R2."""
    previous = await BrandingService(session, owner.tenant_id, owner.id).clear_logo()
    if previous is not None and storage is not None:
        background.add_task(discard_unused_logo, sessions, storage, owner.tenant_id, previous)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
