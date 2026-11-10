"""Logo del negocio — FASE-4-CONTRATO Y9, §4, A11 y §11.8 (PR 4.5).

`inspect_logo` valida la imagen **por su contenido** (Pillow abre los bytes; la extensión y el
`Content-Type` que manda el navegador no se creen) y calcula el color dominante. Es CPU pura y
sincrónica: el endpoint la corre en el threadpool. `BrandingService` mueve
`tenant_settings.logo_key` siempre con la fila tomada con `FOR UPDATE`, para que dos subidas del
mismo negocio (o una subida y el borrado del logo anterior) se ordenen. Este módulo no conoce el
almacenamiento: subir y borrar en R2 lo coordina el endpoint.

Reglas (A11): PNG, JPEG o WebP (SVG no: puede traer scripts y no tiene píxeles para la paleta);
hasta 2 MB; entre 200 y 4096 píxeles **por lado**. Contra las "bombas de descompresión" (un PNG
chico que declara 50.000×50.000) el tamaño declarado se mira **antes** de decodificar, y la
advertencia de Pillow pasa a ser error. Para el color dominante la imagen se achica (`draft` en
JPEG, que decodifica directamente chico; `reduce` en el resto, que decodifica en el modo nativo y
achica) **antes** de convertirla a RGBA: nunca se arma una copia RGBA a resolución completa.
"""

import hashlib
import io
import uuid
import warnings
from contextlib import suppress
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from app.application.services._base import ServiceBase
from app.application.services.errors import NotFoundError
from app.domain.branding import RGB, RGBA, dominant_color
from app.persistence.models.tenant import TenantSettings

MAX_LOGO_BYTES = 2 * 1024 * 1024
#: Tope del cuerpo HTTP de las subidas (`BodyLimitMiddleware`): el archivo más el sobre multipart.
MAX_UPLOAD_BODY_BYTES = MAX_LOGO_BYTES + 512 * 1024
MIN_LOGO_SIDE = 200
#: Una foto de celular (4032 × 3024) entra; el peor caso decodificado son ~50 MB en RGB(A).
MAX_LOGO_SIDE = 4096
#: Lado de la miniatura sobre la que se calcula el color dominante: 4096 píxeles alcanzan.
_SAMPLE_SIDE = 64

#: Formato de Pillow → (extensión de la clave, `Content-Type` con que se sirve).
ALLOWED_FORMATS: dict[str, tuple[str, str]] = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
}


class InvalidImageError(Exception):
    """No es una imagen, está rota o es de un formato no admitido."""


class ImageTooSmallError(Exception):
    """Menos de `MIN_LOGO_SIDE` píxeles de lado."""


class ImageTooLargeError(Exception):
    """Más de `MAX_LOGO_BYTES`, o algún lado de más de `MAX_LOGO_SIDE` píxeles."""


@dataclass(frozen=True)
class LogoImage:
    data: bytes
    extension: str
    content_type: str
    width: int
    height: int
    #: `None` si la imagen es toda transparente.
    dominant: RGB | None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def key_for(self, tenant_id: uuid.UUID) -> str:
        """`logos/<tenant_id>/<sha256>.<ext>` (Y9): misma imagen, misma clave."""
        return f"logos/{tenant_id}/{self.sha256}.{self.extension}"


def _sample(image: Image.Image) -> list[RGBA]:
    """Miniatura RGBA de a lo sumo `_SAMPLE_SIDE` de lado, achicando antes de convertir."""
    factor = max(1, min(image.size) // (_SAMPLE_SIDE * 2))
    if factor > 1:
        # En el modo nativo: sin copia RGBA a resolución completa. Los modos que `reduce` no
        # admite (p. ej. `P`) se convierten enteros, que igual son ≤ 4096 de lado.
        with suppress(ValueError):
            image = image.reduce(factor)
    sample = image.convert("RGBA")
    sample.thumbnail((_SAMPLE_SIDE, _SAMPLE_SIDE))
    return list(sample.getdata())


def inspect_logo(data: bytes) -> LogoImage:
    """Valida y analiza el logo. Sincrónica y de CPU: llamarla con `run_in_threadpool`."""
    if len(data) > MAX_LOGO_BYTES:
        raise ImageTooLargeError(f"the file exceeds {MAX_LOGO_BYTES} bytes")
    if not data:
        raise InvalidImageError("empty file")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                fmt = probe.format
                width, height = probe.size
                if fmt not in ALLOWED_FORMATS:
                    raise InvalidImageError(f"unsupported format: {fmt}")
                if max(width, height) > MAX_LOGO_SIDE:
                    raise ImageTooLargeError(f"{width}x{height} exceeds {MAX_LOGO_SIDE}px a side")
                if min(width, height) < MIN_LOGO_SIDE:
                    raise ImageTooSmallError(f"{width}x{height} is below {MIN_LOGO_SIDE}px")
                probe.verify()  # estructura y CRC sin decodificar
            # `verify` deja la imagen inutilizable: se abre de nuevo para decodificar.
            with Image.open(io.BytesIO(data)) as image:
                image.draft("RGB", (_SAMPLE_SIDE * 2, _SAMPLE_SIDE * 2))  # JPEG: decodifica chico
                pixels = _sample(image)
    except (ImageTooLargeError, ImageTooSmallError, InvalidImageError):
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        # Pillow la corta en `open` cuando declara más del doble de `MAX_IMAGE_PIXELS`.
        raise ImageTooLargeError("decompression bomb") from exc
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,  # Pillow lo usa para algunos archivos mal formados
        ValueError,
    ) as exc:
        raise InvalidImageError("not a valid image") from exc
    extension, content_type = ALLOWED_FORMATS[fmt]
    return LogoImage(
        data=data,
        extension=extension,
        content_type=content_type,
        width=width,
        height=height,
        dominant=dominant_color(pixels),
    )


class BrandingService(ServiceBase):
    async def _locked_settings(self) -> TenantSettings:
        """La fila de configuración con `FOR UPDATE` (en Postgres; SQLite lo ignora) y releída:
        serializa a quien cambia o borra el logo del mismo negocio."""
        await self._enter()
        row = await self._session.scalar(
            select(TenantSettings)
            .where(TenantSettings.tenant_id == self._tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError("tenant_settings", self._tenant_id)
        return row

    async def set_logo(self, key: str) -> str | None:
        """Guarda la clave nueva y devuelve la anterior **si era otra** (para borrarla después);
        `None` si no había o era la misma."""
        settings = await self._locked_settings()
        previous = settings.logo_key
        settings.logo_key = key
        await self._session.flush()
        return previous if previous != key else None

    async def revert_logo(self, key: str, previous: str | None) -> None:
        """Deshace `set_logo` si la subida a R2 falló, salvo que otro pedido ya haya cambiado el
        logo (entonces manda ese)."""
        settings = await self._locked_settings()
        if settings.logo_key == key:
            settings.logo_key = previous
            await self._session.flush()

    async def clear_logo(self) -> str | None:
        """Quita el logo. Devuelve la clave que tenía (o `None` si no había)."""
        settings = await self._locked_settings()
        previous = settings.logo_key
        settings.logo_key = None
        await self._session.flush()
        return previous

    async def lock_and_check_in_use(self, key: str) -> bool:
        """Toma la fila y dice si el logo vigente es `key`. Quien borra un objeto de R2 lo hace
        **con la fila tomada** y solo si esto da `False`: una subida que vuelva a poner esa
        clave espera al borrado y sube el objeto después (§11.8)."""
        settings = await self._locked_settings()
        return settings.logo_key == key
