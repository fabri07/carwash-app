"""Personalización del negocio — FASE-4-CONTRATO §3.5 (PR 4.5).

Catálogos **en código** (como el glosario, Y6): el negocio elige una paleta entera y una
tipografía, nunca un color suelto. Así ninguna combinación queda ilegible: cada paleta trae su
color de texto sobre el primario (`on_primary`) y un test exige contraste WCAG AA (≥ 4.5).

Los colores se definen en **OKLCH** (perceptualmente uniforme: dos colores con la misma `L` se
ven igual de claros). Para el navegador y para medir contraste se convierten a sRGB.

`default` existe en los dos catálogos porque es lo que ya tiene guardado la base desde la `0005`
(§11.4): reproduce el tema de `globals.css` y la tipografía del sistema.

Funciones puras: sin Pillow ni I/O. Quien decodifica la imagen (el servicio de aplicación) le
pasa los píxeles a `dominant_color`.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass

DEFAULT_PALETTE = "default"
DEFAULT_FONT = "default"

#: Contraste mínimo del texto sobre el primario (WCAG 2.x AA, texto normal).
MIN_CONTRAST = 4.5

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]


@dataclass(frozen=True)
class Oklch:
    l: float  # noqa: E741  # nombre canónico del espacio de color
    c: float
    h: float

    def css(self) -> str:
        return f"oklch({self.l * 100:.1f}% {self.c:.3f} {self.h:.1f})"

    def hex(self) -> str:
        r, g, b = oklch_to_srgb(self)
        return f"#{r:02x}{g:02x}{b:02x}"


@dataclass(frozen=True)
class Palette:
    code: str
    name: str
    background: Oklch
    primary: Oklch
    secondary: Oklch
    #: Color del texto sobre el primario (botones). Blanco o casi negro, el que contraste.
    on_primary: Oklch


@dataclass(frozen=True)
class Font:
    code: str
    name: str
    #: Familia CSS. `None` = la pila del sistema (no se descarga nada).
    family: str | None
    description: str


_WHITE = Oklch(1.0, 0.0, 0.0)

#: El orden es el de la pantalla. Cambiar un `code` rompe a los negocios que lo eligieron: los
#: códigos solo se agregan.
PALETTES: dict[str, Palette] = {
    p.code: p
    for p in (
        Palette(
            code=DEFAULT_PALETTE,
            name="Agua",
            # = `globals.css`: hsl(210 40% 98%) y hsl(199 89% 37%). El primario era 38%; con
            # blanco encima daba 4.44 de contraste y se oscureció un punto para llegar a AA.
            background=Oklch(0.984, 0.003, 247.9),
            primary=Oklch(0.560, 0.121, 237.5),
            secondary=Oklch(0.80, 0.15, 75.0),
            on_primary=_WHITE,
        ),
        Palette(
            code="bosque",
            name="Bosque",
            background=Oklch(0.965, 0.015, 80.0),
            primary=Oklch(0.47, 0.09, 165.0),
            secondary=Oklch(0.70, 0.14, 35.0),
            on_primary=_WHITE,
        ),
        Palette(
            code="oceano",
            name="Océano",
            background=Oklch(0.965, 0.008, 230.0),
            primary=Oklch(0.36, 0.08, 255.0),
            secondary=Oklch(0.78, 0.13, 75.0),
            on_primary=_WHITE,
        ),
        Palette(
            code="terracota",
            name="Terracota",
            background=Oklch(0.975, 0.012, 75.0),
            primary=Oklch(0.52, 0.13, 42.0),
            secondary=Oklch(0.50, 0.07, 185.0),
            on_primary=_WHITE,
        ),
        Palette(
            code="competicion",
            name="Rojo competición",
            background=Oklch(0.975, 0.006, 20.0),
            primary=Oklch(0.52, 0.19, 27.0),
            secondary=Oklch(0.30, 0.02, 260.0),
            on_primary=_WHITE,
        ),
        Palette(
            code="grafito",
            name="Grafito",
            background=Oklch(0.965, 0.003, 260.0),
            primary=Oklch(0.30, 0.012, 260.0),
            secondary=Oklch(0.68, 0.16, 50.0),
            on_primary=_WHITE,
        ),
    )
}

#: Fuentes de Google disponibles en `next/font/google` (el front las declara en `lib/fonts.ts`;
#: un meta-test verifica que estén las mismas). Sans geométricas o humanistas, legibles en
#: tamaños chicos: la app se opera desde el celular.
FONTS: dict[str, Font] = {
    f.code: f
    for f in (
        Font(DEFAULT_FONT, "Sistema", None, "La del teléfono o la computadora; carga al instante."),
        Font("inter", "Inter", "Inter", "Neutra y muy legible en pantallas chicas."),
        Font("manrope", "Manrope", "Manrope", "Cálida y redondeada, sin perder claridad."),
        Font(
            "plus_jakarta",
            "Plus Jakarta Sans",
            "Plus Jakarta Sans",
            "Geométrica y moderna, con personalidad.",
        ),
        Font("outfit", "Outfit", "Outfit", "Redondeada y amigable, ideal para títulos."),
    )
}


def is_palette(code: str) -> bool:
    return code in PALETTES


def is_font(code: str) -> bool:
    return code in FONTS


# ── Conversiones de color (Björn Ottosson, "A perceptual color space", 2020) ───────────────


def _to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _to_gamma(channel: float) -> float:
    return 12.92 * channel if channel <= 0.0031308 else 1.055 * channel ** (1 / 2.4) - 0.055


def srgb_to_oklab(rgb: RGB) -> tuple[float, float, float]:
    r, g, b = (_to_linear(v / 255) for v in rgb)
    lc = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    mc = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    sc = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    lc, mc, sc = (math.copysign(abs(v) ** (1 / 3), v) for v in (lc, mc, sc))
    return (
        0.2104542553 * lc + 0.7936177850 * mc - 0.0040720468 * sc,
        1.9779984951 * lc - 2.4285922050 * mc + 0.4505937099 * sc,
        0.0259040371 * lc + 0.7827717662 * mc - 0.8086757660 * sc,
    )


def oklab_to_oklch(lab: tuple[float, float, float]) -> Oklch:
    lightness, a, b = lab
    return Oklch(lightness, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360)


def oklch_to_oklab(color: Oklch) -> tuple[float, float, float]:
    rad = math.radians(color.h)
    return color.l, color.c * math.cos(rad), color.c * math.sin(rad)


def oklch_to_srgb(color: Oklch) -> RGB:
    """sRGB de 8 bits. Fuera de gamut se recorta por canal (las paletas están dentro)."""
    lightness, a, b = oklch_to_oklab(color)
    lc = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    mc = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    sc = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    linear = (
        4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc,
        -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc,
        -0.0041960863 * lc - 0.7034186147 * mc + 1.7076147010 * sc,
    )
    r, g, bl = (round(min(1.0, max(0.0, _to_gamma(max(0.0, v)))) * 255) for v in linear)
    return r, g, bl


def contrast_ratio(fg: RGB, bg: RGB) -> float:
    """Contraste WCAG 2.x entre dos colores sRGB (1 a 21)."""

    def luminance(rgb: RGB) -> float:
        r, g, b = (_to_linear(v / 255) for v in rgb)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    hi, lo = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# ── Color dominante y sugerencia ──────────────────────────────────────────────────────────

#: Un píxel con menos croma que esto es gris (fondo blanco, texto negro): no define la marca.
_CHROMA_MIN = 0.04
_HUE_BINS = 12
#: Peso de la claridad en la distancia: un celeste y un azul marino son "la misma marca".
_LIGHTNESS_WEIGHT = 0.5


def dominant_color(pixels: Iterable[RGBA]) -> RGB | None:
    """Color que mejor representa la marca de un logo.

    Ignora los píxeles transparentes. Entre los de color (croma ≥ `_CHROMA_MIN`, ni casi blancos
    ni casi negros) agrupa por tono en 12 sectores y promedia, en OKLab, el sector con más
    píxeles: así un logo azul sobre fondo blanco da azul y no gris claro. Si el logo no tiene
    color (blanco y negro), promedia todos los visibles. `None` si no hay ninguno visible.
    """
    visible: list[tuple[float, float, float]] = []
    bins: dict[int, list[tuple[float, float, float]]] = {}
    for r, g, b, a in pixels:
        if a < 128:
            continue
        lab = srgb_to_oklab((r, g, b))
        visible.append(lab)
        lch = oklab_to_oklch(lab)
        if lch.c >= _CHROMA_MIN and 0.2 < lch.l < 0.97:
            bins.setdefault(int(lch.h // (360 / _HUE_BINS)) % _HUE_BINS, []).append(lab)
    if not visible:
        return None
    group = max(bins.values(), key=len) if bins else visible
    n = len(group)
    mean = (
        sum(p[0] for p in group) / n,
        sum(p[1] for p in group) / n,
        sum(p[2] for p in group) / n,
    )
    return oklch_to_srgb(oklab_to_oklch(mean))


def palette_distance(rgb: RGB, palette: Palette) -> float:
    lightness, a, b = srgb_to_oklab(rgb)
    pl, pa, pb = oklch_to_oklab(palette.primary)
    return math.sqrt((_LIGHTNESS_WEIGHT * (lightness - pl)) ** 2 + (a - pa) ** 2 + (b - pb) ** 2)


def suggest_palette(rgb: RGB) -> Palette:
    """La paleta cuyo primario está más cerca del color dado (distancia OKLab, claridad a la
    mitad). Ante empate gana la primera del catálogo.

    Un color sin croma (gris, negro, blanco: un logo monocromo) no tiene tono que acercar: por
    distancia caería en cualquier paleta de claridad parecida. Se sugiere la neutra (la de primario
    con menos croma).
    """
    if oklab_to_oklch(srgb_to_oklab(rgb)).c < _CHROMA_MIN:
        return min(PALETTES.values(), key=lambda p: p.primary.c)
    return min(PALETTES.values(), key=lambda p: palette_distance(rgb, p))
