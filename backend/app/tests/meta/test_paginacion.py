"""ADR-0006 — toda colección devuelve `PaginatedResponse`."""

from typing import get_origin

from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.main import create_app
from app.schemas.common import PaginatedResponse


def _rutas_de_coleccion(app):
    for r in app.routes:
        if not (isinstance(r, APIRoute) and "GET" in r.methods and r.response_model is not None):
            continue
        modelo = r.response_model
        es_generico_paginado = isinstance(modelo, type) and issubclass(modelo, PaginatedResponse)
        tiene_items = (
            isinstance(modelo, type)
            and issubclass(modelo, BaseModel)
            and any(get_origin(f.annotation) is list for f in modelo.model_fields.values())
        )
        if es_generico_paginado or tiene_items or get_origin(modelo) is list:
            yield r


def test_ningun_get_devuelve_una_lista_plana():
    culpables = [
        r.path
        for r in create_app().routes
        if isinstance(r, APIRoute) and get_origin(r.response_model) is list
    ]
    assert not culpables, f"devuelven list[...] en vez de PaginatedResponse: {culpables}"


def test_toda_coleccion_usa_el_envelope_unico():
    rutas = list(_rutas_de_coleccion(create_app()))
    assert rutas, "no se encontró ninguna colección: el barrido no está mirando nada"
    for r in rutas:
        assert issubclass(
            r.response_model, PaginatedResponse
        ), f"{r.path}: envelope propio ({r.response_model}), no PaginatedResponse"


def _bound(field_info, name):
    for meta in field_info.metadata:
        if hasattr(meta, name):
            return getattr(meta, name)
    return getattr(field_info, name, None)


def test_toda_coleccion_declara_limit_y_offset_con_los_mismos_limites():
    for r in _rutas_de_coleccion(create_app()):
        params = {p.name: p for p in r.dependant.query_params}
        for sub in r.dependant.dependencies:
            params.update({p.name: p for p in sub.query_params})
        assert {"limit", "offset"} <= params.keys(), f"{r.path}: sin limit/offset"
        assert _bound(params["limit"].field_info, "le") == 200
        assert _bound(params["limit"].field_info, "ge") == 1
        assert _bound(params["offset"].field_info, "ge") == 0
