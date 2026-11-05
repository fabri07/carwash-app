"""A9 · Y8 — búsqueda sin extensiones: clave normalizada y tolerancia a errores de tipeo."""

import pytest

from app.domain.search import edit_distance, matches, normalize


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Tratamiento Cerámico", "tratamiento ceramico"),
        ("  Film   de protección-de pintura ", "film de proteccion de pintura"),
        ("PPF!", "ppf"),
        ("", ""),
        ("¿Ñandú?", "nandu"),
    ],
)
def test_normalize(texto, esperado):
    assert normalize(texto) == esperado


@pytest.mark.parametrize(
    ("a", "b", "distancia"),
    [
        ("a", "a", 0),
        ("detaling", "detailing", 1),
        ("ab", "ba", 1),
        ("", "abc", 3),
        ("kitten", "sitting", 3),
    ],
)
def test_edit_distance(a, b, distancia):
    assert edit_distance(a, b) == distancia


CATALOGO = {
    "Tratamiento cerámico": ("ppf",),
    "Film de protección de pintura": ("ppf", "film ppf"),
    "Corrección de pintura": ("sacar rayas", "pulido"),
    "Detailing interior": (),
}


def _buscar(consulta):
    return {
        nombre
        for nombre, alias in CATALOGO.items()
        if matches(consulta, normalize(nombre), aliases=alias)
    }


def test_ceramico_sin_acento_encuentra_el_tratamiento():
    assert _buscar("ceramico") == {"Tratamiento cerámico"}


def test_sigla_del_glosario_por_alias():
    assert "Film de protección de pintura" in _buscar("ppf")


def test_frase_del_glosario_por_alias():
    assert _buscar("sacar rayas") == {"Corrección de pintura"}


def test_tolera_un_error_de_tipeo():
    assert _buscar("detaling") == {"Detailing interior"}


def test_tolera_letras_giradas():
    assert _buscar("ceramcio") == {"Tratamiento cerámico"}


def test_prefijo_y_todas_las_palabras():
    assert _buscar("trat cer") == {"Tratamiento cerámico"}
    assert _buscar("trat pintura") == set()


def test_consulta_vacia_o_sin_parecido_no_encuentra_nada():
    assert _buscar("") == set()
    assert _buscar("   ") == set()
    assert _buscar("zzzzzz") == set()


def test_palabras_cortas_no_toleran_errores():
    # 3 letras: un error ya cambia de palabra ("ppf" ≠ "pdf").
    assert not matches("pdf", "ppf")
