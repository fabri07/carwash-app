"""Catálogo de personalización y paleta sugerida — FASE-4-CONTRATO §3.5 y A11 (PR 4.5)."""

import re

import pytest

from app.domain.branding import (
    DEFAULT_FONT,
    DEFAULT_PALETTE,
    FONTS,
    MIN_CONTRAST,
    PALETTES,
    Oklch,
    contrast_ratio,
    dominant_color,
    is_font,
    is_palette,
    oklab_to_oklch,
    oklch_to_srgb,
    srgb_to_oklab,
    suggest_palette,
)
from app.persistence.models import tenant as tenant_model

_FORMATO_DB = re.compile(r"^[a-z0-9_-]{1,40}$")  # CHECK paleta_formato / fuente_formato
#: Texto principal del tema (`--foreground: 222 47% 11%`).
_TEXTO = (15, 23, 42)


def test_seis_paletas_y_cinco_tipografias_con_default():
    assert len(PALETTES) == 6 and len(FONTS) == 5
    assert DEFAULT_PALETTE in PALETTES and DEFAULT_FONT in FONTS
    assert FONTS[DEFAULT_FONT].family is None  # la del sistema: no descarga nada


def test_el_default_de_la_base_esta_en_el_catalogo():
    assert tenant_model.DEFAULT_PALETTE == DEFAULT_PALETTE
    assert tenant_model.DEFAULT_FONT == DEFAULT_FONT


@pytest.mark.parametrize("code", [*PALETTES, *FONTS])
def test_los_codigos_cumplen_el_check_de_la_base(code):
    assert _FORMATO_DB.fullmatch(code)


def test_nombres_unicos_y_en_castellano():
    assert len({p.name for p in PALETTES.values()}) == len(PALETTES)
    assert PALETTES["oceano"].name == "Océano"


@pytest.mark.parametrize("palette", PALETTES.values(), ids=list(PALETTES))
def test_el_texto_sobre_el_primario_es_legible(palette):
    ratio = contrast_ratio(oklch_to_srgb(palette.on_primary), oklch_to_srgb(palette.primary))
    assert ratio >= MIN_CONTRAST, f"{palette.code}: {ratio:.2f}"


@pytest.mark.parametrize("palette", PALETTES.values(), ids=list(PALETTES))
def test_el_texto_del_tema_sobre_el_fondo_es_legible(palette):
    assert contrast_ratio(_TEXTO, oklch_to_srgb(palette.background)) >= 7


@pytest.mark.parametrize("palette", PALETTES.values(), ids=list(PALETTES))
def test_los_colores_estan_dentro_de_srgb(palette):
    """Recortar un canal cambia el tono: las paletas se eligieron dentro del gamut."""
    for color in (palette.background, palette.primary, palette.secondary, palette.on_primary):
        r, g, b = oklch_to_srgb(color)
        vuelta = oklab_to_oklch(srgb_to_oklab((r, g, b)))
        assert abs(vuelta.l - color.l) < 0.01, (palette.code, color)


def test_default_reproduce_el_tema_de_globals_css():
    # hsl(199 89% 37%) = rgb(10, 125, 178); hsl(210 40% 98%) = rgb(248, 250, 252)
    p = PALETTES[DEFAULT_PALETTE]

    def cerca(color, rgb):
        return all(abs(a - b) <= 2 for a, b in zip(oklch_to_srgb(color), rgb, strict=True))

    assert cerca(p.primary, (10, 125, 178))
    assert cerca(p.background, (248, 250, 252))


def test_conversion_ida_y_vuelta():
    for rgb in [(0, 0, 0), (255, 255, 255), (255, 0, 0), (12, 200, 99), (10, 125, 178)]:
        assert oklch_to_srgb(oklab_to_oklch(srgb_to_oklab(rgb))) == rgb


def test_css_y_hex():
    assert Oklch(0.5, 0.1, 200).css() == "oklch(50.0% 0.100 200.0)"
    assert Oklch(1.0, 0.0, 0.0).hex() == "#ffffff"


def test_contraste_extremos():
    assert contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21)
    assert contrast_ratio((90, 90, 90), (90, 90, 90)) == pytest.approx(1)


def test_is_palette_e_is_font():
    assert is_palette("bosque") and not is_palette("rosa")
    assert is_font("inter") and not is_font("comic_sans")


# ── Sugerencia ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("palette", PALETTES.values(), ids=list(PALETTES))
def test_cada_paleta_se_sugiere_a_si_misma(palette):
    assert suggest_palette(oklch_to_srgb(palette.primary)).code == palette.code


@pytest.mark.parametrize(
    ("rgb", "esperada"),
    [
        ((220, 20, 30), "competicion"),  # rojo Ferrari
        ((34, 139, 34), "bosque"),  # verde
        ((0, 40, 100), "oceano"),  # azul marino
        ((0, 150, 220), DEFAULT_PALETTE),  # celeste
        ((200, 90, 40), "terracota"),  # naranja quemado
        ((20, 20, 20), "grafito"),  # negro
        ((128, 128, 128), "grafito"),  # gris
    ],
)
def test_sugiere_la_paleta_mas_cercana(rgb, esperada):
    assert suggest_palette(rgb).code == esperada


def test_dominante_ignora_el_fondo_blanco_y_lo_transparente():
    logo = [(255, 255, 255, 255)] * 700 + [(0, 0, 0, 0)] * 500 + [(200, 20, 30, 255)] * 100
    r, g, b = dominant_color(logo) or (0, 0, 0)
    assert r > 150 and g < 60 and b < 70


def test_dominante_elige_el_tono_mas_frecuente():
    logo = [(20, 120, 40, 255)] * 300 + [(220, 30, 30, 255)] * 100
    assert suggest_palette(dominant_color(logo) or (0, 0, 0)).code == "bosque"


def test_logo_sin_color_promedia_lo_visible():
    logo = [(0, 0, 0, 255)] * 50 + [(255, 255, 255, 255)] * 50
    r, g, b = dominant_color(logo) or (0, 0, 0)
    assert r == g == b  # gris
    assert suggest_palette((r, g, b)).code == "grafito"


def test_todo_transparente_no_tiene_dominante():
    assert dominant_color([(10, 10, 10, 0)] * 20) is None
    assert dominant_color([]) is None
