"""A12 · FASE-4-CONTRATO Y6 — `glosario.json` coincide con `docs/GLOSARIO-RUBRO.md`.

El `.md` lo edita el dueño; el `.json` lo lee la app y lo genera `scripts/build_glosario.py`. Este
test de deriva corre el script sobre el `.md` y exige que el `.json` commiteado sea idéntico
byte a byte: tocar uno sin regenerar el otro deja el test en rojo.

Además fija la regla de las plantillas (adenda §11.5): solo `servicio` y `adicional`.
"""

import importlib.util
import json
from types import ModuleType

import pytest

from app.domain.glossary import DATA_FILE, SELLABLE_TYPES, TemplateKind, load_glossary
from app.domain.search import normalize
from app.tests.meta._rutas import BACKEND, REPO

SCRIPT = BACKEND / "scripts" / "build_glosario.py"
MARKDOWN = REPO / "docs" / "GLOSARIO-RUBRO.md"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_glosario", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_el_json_commiteado_es_lo_que_genera_el_script_desde_el_md():
    script = _script()
    esperado = script.render(script.build(MARKDOWN.read_text(encoding="utf-8")))
    assert DATA_FILE.read_text(encoding="utf-8") == esperado, (
        "app/data/glosario.json está vencido respecto de docs/GLOSARIO-RUBRO.md: "
        "correr `python scripts/build_glosario.py`"
    )


def test_los_codigos_son_unicos_y_estables():
    codigos = [t["code"] for t in json.loads(DATA_FILE.read_text(encoding="utf-8"))["terms"]]
    assert len(codigos) == len(set(codigos)) > 100
    for t in load_glossary().terms.values():
        assert t.code == normalize(t.term).replace(" ", "_")


@pytest.mark.parametrize("kind", list(TemplateKind))
def test_una_plantilla_solo_trae_servicios_y_adicionales(kind):
    plantilla = load_glossary().template(kind)
    assert plantilla
    assert {t.type for t in plantilla} <= SELLABLE_TYPES
    prohibidos = {"tecnica", "defecto", "producto", "insumo", "modificador", "entidad"}
    assert not {t.type for t in plantilla} & prohibidos


def test_lavadero_son_las_secciones_1_y_2_y_detailing_de_la_1_a_la_4():
    g = load_glossary()
    assert {t.section for t in g.template(TemplateKind.LAVADERO)} == {1, 2}
    assert {t.section for t in g.template(TemplateKind.DETAILING)} == {1, 2, 3, 4}
    lavadero = {t.code for t in g.template(TemplateKind.LAVADERO)}
    assert lavadero < {t.code for t in g.template(TemplateKind.DETAILING)}


def test_las_tecnicas_y_los_defectos_no_son_plantilla_pero_se_buscan():
    g = load_glossary()
    sugeridos = {t.code for k in TemplateKind for t in g.template(k)}
    for code in ("lavado_a_mano", "clay_bar_arcilla", "swirls_telaranas", "aromatizante"):
        assert code in g.terms and code not in sugeridos


def test_todo_defecto_lleva_a_algun_servicio_vendible():
    datos = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    g = load_glossary()
    assert len(datos["defects"]) >= 10
    for defecto in datos["defects"]:
        assert defecto["services"], defecto["code"]
        assert all(g.is_sellable(c) for c in defecto["services"])


def test_sin_pii_ni_secretos_en_el_json():
    texto = DATA_FILE.read_text(encoding="utf-8")
    assert "@" not in texto
    assert "REDACTADO" not in texto
