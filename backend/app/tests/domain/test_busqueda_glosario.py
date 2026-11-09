"""A9 · FASE-4-CONTRATO — la búsqueda con el glosario del rubro.

"ceramico" → Tratamiento cerámico; "ppf" → Film de protección de pintura; "sacar rayas" →
Corrección de pintura (un defecto llega al servicio que lo resuelve); "detaling" tolera el error.
Se prueba sobre la plantilla `detailing` entera, como la vería un negocio recién armado.
"""

import pytest

from app.domain.glossary import TemplateKind, load_glossary
from app.domain.search import (
    TIER_ALIAS,
    TIER_EXACT_ALIAS,
    TIER_EXACT_KEY,
    TIER_KEY,
    match_tier,
    normalize,
)


def _buscar(consulta: str) -> list[str]:
    """Los nombres que encuentra `consulta`, mejor coincidencia primero."""
    g = load_glossary()
    wanted = normalize(consulta)  # una vez; los alias ya vienen normalizados del glosario
    hits = []
    for term in g.template(TemplateKind.DETAILING):
        tier = match_tier(wanted, normalize(term.term), aliases=g.aliases_for(term.code))
        if tier is not None:
            hits.append((tier, term.term))
    return [name for _, name in sorted(hits, key=lambda h: h[0])]


@pytest.mark.parametrize(
    ("consulta", "esperado"),
    [
        ("ceramico", "Tratamiento cerámico"),
        ("cerámico", "Tratamiento cerámico"),
        ("ppf", "Film de protección de pintura"),
        ("PPF", "Film de protección de pintura"),
        ("sacar rayas", "Corrección de pintura"),
        ("sacarle las rayas", "Corrección de pintura"),
        ("hacele el completo", "Lavado completo"),
        ("polarisado", "Polarizado"),
    ],
)
def test_el_primer_resultado_es_el_esperado(consulta, esperado):
    assert _buscar(consulta)[0] == esperado


def test_detaling_tolera_el_error_de_tipeo():
    assert "Limpieza de interior" in _buscar("detaling")  # sinónimo "detailing interior"
    assert "Limpieza de interior" in _buscar("detailin")


def test_un_defecto_lleva_al_servicio_que_lo_resuelve():
    assert "Corrección de pintura" in _buscar("swirls")
    assert "Restauración de ópticas" in _buscar("faros amarillos")
    assert "Ozonizado" in _buscar("olor a cigarrillo")


def test_en_ingles_con_el_mismo_peso():
    assert _buscar("ceramic coating")[0] == "Tratamiento cerámico"
    assert "Encerado" in _buscar("wax")


def test_el_orden_de_calidad():
    assert match_tier("lavado completo", "lavado completo") == TIER_EXACT_KEY
    assert match_tier("ppf", "film de proteccion", aliases=["ppf"]) == TIER_EXACT_ALIAS
    assert match_tier("film", "film de proteccion", aliases=["ppf"]) == TIER_KEY
    assert match_tier("lamina", "film de proteccion", aliases=["lamina protectora"]) == TIER_ALIAS
    assert match_tier("zzzz", "film de proteccion", aliases=["ppf"]) is None
    assert match_tier(normalize("   "), "film") is None


def test_los_alias_del_glosario_vienen_normalizados():
    g = load_glossary()
    for term in g.terms.values():
        for alias in g.aliases_for(term.code):
            assert alias == normalize(alias) and alias, (term.code, alias)
    assert "sacarle las rayas" in g.aliases_for("correccion_de_pintura")
    assert "ppf" in g.aliases_for("film_de_proteccion_de_pintura")


def test_un_servicio_que_no_existe_no_aparece():
    assert _buscar("zapatillas") == []
