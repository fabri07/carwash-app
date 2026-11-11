"""Validación del logo por contenido — FASE-4-CONTRATO A11 (PR 4.5). Imágenes sintéticas."""

import io
import struct
import zlib

import pytest
from PIL import Image

from app.application.services.branding_service import (
    MAX_LOGO_BYTES,
    ImageTooLargeError,
    ImageTooSmallError,
    InvalidImageError,
    inspect_logo,
)


def imagen(fmt="PNG", size=(256, 256), color=(30, 110, 200), mode="RGB", **kwargs) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, fmt, **kwargs)
    return buf.getvalue()


def logo_con_fondo(size=(300, 300)) -> bytes:
    """Fondo blanco con un cuadrado rojo al centro: el dominante tiene que ser el rojo."""
    img = Image.new("RGB", size, (255, 255, 255))
    img.paste((210, 20, 30), (100, 100, 200, 200))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def png_que_declara(width: int, height: int) -> bytes:
    """Un PNG de pocos bytes que declara dimensiones enormes (bomba de descompresión)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00"))


@pytest.mark.parametrize(
    ("fmt", "ext", "content_type"),
    [("PNG", "png", "image/png"), ("JPEG", "jpg", "image/jpeg"), ("WEBP", "webp", "image/webp")],
)
def test_acepta_png_jpeg_y_webp(fmt, ext, content_type):
    logo = inspect_logo(imagen(fmt))
    assert (logo.extension, logo.content_type) == (ext, content_type)
    assert (logo.width, logo.height) == (256, 256)
    assert logo.dominant is not None


def test_la_clave_es_por_hash_del_contenido():
    import uuid

    tenant = uuid.UUID(int=7)
    a, b = inspect_logo(imagen()), inspect_logo(imagen())
    assert a.key_for(tenant) == b.key_for(tenant)
    assert a.key_for(tenant) == f"logos/{tenant}/{a.sha256}.png"
    assert len(a.sha256) == 64


def test_el_dominante_es_el_color_y_no_el_fondo():
    r, g, b = inspect_logo(logo_con_fondo()).dominant or (0, 0, 0)
    assert r > 150 and g < 60


def test_png_transparente_no_tiene_dominante():
    assert inspect_logo(imagen(mode="RGBA", color=(0, 0, 0, 0))).dominant is None


@pytest.mark.parametrize(
    "contenido",
    [
        b"",
        b"hola, esto es texto",
        b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"></svg>',
        b"%PDF-1.4 falso",
    ],
    ids=["vacio", "texto", "svg", "pdf"],
)
def test_rechaza_lo_que_no_es_imagen(contenido):
    with pytest.raises(InvalidImageError):
        inspect_logo(contenido)


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF"])
def test_rechaza_formatos_no_admitidos(fmt):
    with pytest.raises(InvalidImageError):
        inspect_logo(imagen(fmt))


def test_rechaza_una_imagen_truncada():
    datos = imagen("PNG", size=(400, 400), color=(1, 2, 3))
    with pytest.raises(InvalidImageError):
        inspect_logo(datos[: len(datos) // 2])


@pytest.mark.parametrize("size", [(199, 400), (400, 150), (64, 64)])
def test_rechaza_menos_de_200_de_lado(size):
    with pytest.raises(ImageTooSmallError):
        inspect_logo(imagen(size=size))


def test_acepta_exactamente_200():
    assert inspect_logo(imagen(size=(200, 200))).width == 200


def test_rechaza_mas_de_2_mb():
    with pytest.raises(ImageTooLargeError):
        inspect_logo(b"\x89PNG" + b"0" * MAX_LOGO_BYTES)


@pytest.mark.parametrize(
    "lados",
    [(5_000, 5_000), (4_097, 300), (300, 4_097), (60_000, 60_000)],
    ids=["sobre-nuestro-tope", "ancho", "alto", "sobre-el-tope-de-pillow"],
)
def test_rechaza_una_bomba_de_descompresion_sin_decodificarla(lados):
    datos = png_que_declara(*lados)
    assert len(datos) < 200
    with pytest.raises(ImageTooLargeError):
        inspect_logo(datos)


def test_acepta_4096_de_lado():
    assert inspect_logo(imagen(size=(4096, 200))).width == 4096


@pytest.mark.parametrize("mode", ["P", "L", "LA", "RGBA"])
def test_imagenes_grandes_de_cualquier_modo_se_achican_antes_de_convertir(mode):
    """`reduce` no admite todos los modos: si no puede, se convierte igual (≤ 4096 de lado)."""
    color = {"P": 3, "L": 90, "LA": (90, 255), "RGBA": (200, 20, 30, 255)}[mode]
    logo = inspect_logo(imagen(size=(1500, 900), mode=mode, color=color))
    assert logo.width == 1500
