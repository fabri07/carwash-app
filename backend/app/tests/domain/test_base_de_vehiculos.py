"""Base de vehículos del mercado argentino — FASE-4-CONTRATO D4-8, Y7, §3.4 y §8 (PR 4.4).

`app/data/vehiculos_ar.json` es lo que se copia a cada negocio. Acá se prueba que:

- trae lo que promete el contrato (~40 marcas, ~400 modelos, ~15 colores), autos y motos;
- **no hay duplicados por `search_key`** (las marcas entre sí, los modelos dentro de su marca, los
  colores): si los hubiera, la copia chocaría con los únicos entre vivos;
- todo nombre entra en los CHECK de largo y deja una clave buscable;
- la migración `0008` congela los mismos valores del enum y la misma `normalize`.
"""

import importlib.util
import json
from types import ModuleType

import pytest

from app.domain.search import normalize
from app.domain.vehicle_catalog import (
    COLOR_NAME_MAX,
    DATA_FILE,
    MAKE_NAME_MAX,
    MODEL_NAME_MAX,
    VehicleBodyType,
    load_vehicle_base,
)
from app.tests.meta._rutas import BACKEND

MIGRACION_0008 = BACKEND / "app/persistence/migrations/versions/20261110_0008_catalogo_vehiculos.py"

BASE = load_vehicle_base()


def _migracion() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migracion_0008", MIGRACION_0008)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_trae_el_tamano_que_promete_el_contrato():
    assert 35 <= len(BASE.makes) <= 50
    assert 350 <= BASE.model_count <= 500
    assert 15 <= len(BASE.colors) <= 25


def test_estan_las_marcas_y_modelos_comunes_del_mercado():
    modelos = {
        (normalize(make.name), normalize(model.name)): model.body_type
        for make in BASE.makes
        for model in make.models
    }
    esperados = {
        ("toyota", "hilux"): VehicleBodyType.PICKUP,
        ("toyota", "etios"): VehicleBodyType.HATCHBACK,
        ("volkswagen", "gol"): VehicleBodyType.HATCHBACK,
        ("volkswagen", "amarok"): VehicleBodyType.PICKUP,
        ("ford", "ranger"): VehicleBodyType.PICKUP,
        ("fiat", "cronos"): VehicleBodyType.SEDAN,
        ("chevrolet", "onix"): VehicleBodyType.HATCHBACK,
        ("renault", "kangoo"): VehicleBodyType.UTILITARIO,
        ("mercedes benz", "sprinter"): VehicleBodyType.FURGON,
        ("honda", "wave 110"): VehicleBodyType.MOTO,
        ("motomel", "blitz 110"): VehicleBodyType.MOTO,
    }
    for clave, carroceria in esperados.items():
        assert modelos.get(clave) == carroceria, clave


def test_cubre_todas_las_carrocerias_salvo_otro():
    usadas = {model.body_type for make in BASE.makes for model in make.models}
    assert usadas == set(VehicleBodyType) - {VehicleBodyType.OTRO}


def test_sin_duplicados_por_search_key():
    marcas = [normalize(m.name) for m in BASE.makes]
    assert len(marcas) == len(set(marcas)), "marca repetida"
    for make in BASE.makes:
        modelos = [normalize(m.name) for m in make.models]
        repetidos = {k for k in modelos if modelos.count(k) > 1}
        assert not repetidos, (make.name, repetidos)
    colores = [normalize(c) for c in BASE.colors]
    assert len(colores) == len(set(colores)), "color repetido"


def test_todo_nombre_entra_en_los_checks_y_es_buscable():
    for make in BASE.makes:
        assert 1 <= len(make.name) <= MAKE_NAME_MAX and normalize(make.name), make.name
        assert make.models, f"{make.name} sin modelos"
        for model in make.models:
            assert 1 <= len(model.name) <= MODEL_NAME_MAX and normalize(model.name), model.name
            assert model.name == model.name.strip()
    for color in BASE.colors:
        assert 1 <= len(color) <= COLOR_NAME_MAX and normalize(color), color


def test_el_archivo_no_trae_mas_que_marcas_y_colores():
    """Sin PII ni campos sueltos (§8): la forma del archivo es fija."""
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    assert set(data) == {"makes", "colors"}
    for make in data["makes"]:
        assert set(make) == {"name", "models"}
        for model in make["models"]:
            assert set(model) == {"name", "body_type"}


def test_la_migracion_congela_el_mismo_enum_y_la_misma_normalize():
    migracion = _migracion()
    assert tuple(v.value for v in VehicleBodyType) == migracion.BODY_TYPES
    assert migracion.BODY_TYPE_ENUM == "vehicle_body_type"
    assert (migracion.MAKE_NAME_MAX, migracion.MODEL_NAME_MAX, migracion.COLOR_NAME_MAX) == (
        MAKE_NAME_MAX,
        MODEL_NAME_MAX,
        COLOR_NAME_MAX,
    )
    nombres = [m.name for m in BASE.makes] + [
        model.name for make in BASE.makes for model in make.models
    ]
    for nombre in [*nombres, *BASE.colors, "Citroën C-Elysée", "Ñandú  Up!"]:
        assert migracion._normalize(nombre) == normalize(nombre), nombre


def test_la_migracion_lee_la_misma_base():
    base = _migracion()._base()
    assert len(base["makes"]) == len(BASE.makes)
    assert len(base["models"]) == BASE.model_count
    assert [name for name, _ in base["colors"]] == list(BASE.colors)


@pytest.mark.parametrize("valor", ["O'Higgins", "a'b'c"])
def test_la_migracion_escapa_las_comillas(valor):
    assert _migracion()._literal(valor) == "'" + valor.replace("'", "''") + "'"
