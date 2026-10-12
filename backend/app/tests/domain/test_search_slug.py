"""Normalización de búsqueda (Y8) y slug del negocio (Y10) — FASE-4-CONTRATO, PR 4.2."""

import pytest

from app.domain.search import normalize, strip_accents
from app.domain.slug import SLUG_MAX, is_valid_slug, slugify, with_suffix


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Tratamiento CERÁMICO", "tratamiento ceramico"),
        ("  pulido   y  encerado ", "pulido y encerado"),
        ("PPF (film) - 3M", "ppf film 3m"),
        ("Ñandú", "nandu"),
        ("", ""),
        ("!!!", ""),
    ],
)
def test_normalize(texto, esperado):
    assert normalize(texto) == esperado


def test_strip_accents_conserva_mayusculas():
    assert strip_accents("Pañuelo Árbol") == "Panuelo Arbol"


@pytest.mark.parametrize(
    ("nombre", "esperado"),
    [
        ("Lavadero Ñandú & Cía.", "lavadero-nandu-cia"),
        ("  Sola CleanCars ", "sola-cleancars"),
        ("A", "negocio"),  # menos de 3 caracteres
        ("???", "negocio"),
    ],
)
def test_slugify(nombre, esperado):
    slug = slugify(nombre)
    assert slug == esperado
    assert is_valid_slug(slug)


def test_slugify_deja_lugar_para_el_sufijo():
    slug = slugify("x" * 100)
    con_sufijo = with_suffix(slug, "abc123")
    assert len(con_sufijo) <= SLUG_MAX
    assert con_sufijo.endswith("-abc123")
    assert is_valid_slug(con_sufijo)


@pytest.mark.parametrize("valor", ["ab", "con espacio", "MAYUS", "a" * 41, "tilde-á"])
def test_slugs_invalidos(valor):
    assert not is_valid_slug(valor)
