"""A3 · A4 · FASE-4-CONTRATO §4 (filas 4.2) — las marcas de autorización de la configuración.

Escrito por el tester de aceptación del PR 4.2. Estático (sin base): mira qué `require_role`
/ `require_permission` cuelga de cada ruta, con el mismo detector que `test_permisos.py`. La
prueba HTTP de lo mismo está en `tests/security/test_autorizacion_configuracion.py`.

- Catálogos: lectura con sesión (rol OWNER o STAFF, sin permiso), escritura con
  `CATALOGO_EDITAR` y nada más (ni `PRECIOS_EDITAR`, que es de 4.3).
- `/settings`: `GET` con sesión, `PATCH` OWNER only (Y5).
"""

from typing import Any

import pytest
from fastapi.routing import APIRoute

from app.domain.permissions import Permission
from app.domain.roles import Role
from app.main import create_app
from app.tests.meta.test_permisos import _autorizaciones

CATALOGOS = ["/v1/business-hours", "/v1/payment-methods", "/v1/resources", "/v1/vehicle-sizes"]
ESCRITURA = {"POST", "PATCH", "PUT", "DELETE"}
LECTURA_OK = {("role", frozenset({Role.OWNER, Role.STAFF}))}
CATALOGO = ("permission", frozenset({Permission.CATALOGO_EDITAR}))
SOLO_OWNER = {("role", frozenset({Role.OWNER}))}


def _rutas() -> dict[tuple[str, str], APIRoute]:
    rutas: dict[tuple[str, str], APIRoute] = {}
    for ruta in create_app().routes:
        if isinstance(ruta, APIRoute):
            for metodo in ruta.methods:
                rutas[(metodo, ruta.path)] = ruta
    return rutas


def _marcas(ruta: APIRoute) -> set[tuple[str, frozenset[Any]]]:
    return _autorizaciones(ruta.dependant)


@pytest.mark.parametrize("base", CATALOGOS)
def test_cada_catalogo_tiene_su_crud_completo(base):
    rutas = _rutas()
    esperadas = {
        ("GET", base),
        ("POST", base),
        ("GET", f"{base}/{{id}}"),
        ("PATCH", f"{base}/{{id}}"),
        ("DELETE", f"{base}/{{id}}"),
    }
    # el nombre del parámetro de ruta no es contrato: se normaliza a `{id}`
    normalizadas = {(m, _normalizar(p)) for m, p in rutas}
    faltan = esperadas - normalizadas
    assert not faltan, f"faltan rutas: {sorted(faltan)}"


def _normalizar(path: str) -> str:
    partes = path.split("/")
    return "/".join("{id}" if p.startswith("{") and p.endswith("}") else p for p in partes)


@pytest.mark.parametrize("base", CATALOGOS)
def test_escribir_un_catalogo_exige_catalogo_editar(base):
    for (metodo, path), ruta in _rutas().items():
        if not (path == base or path.startswith(f"{base}/")) or metodo not in ESCRITURA:
            continue
        marcas = _marcas(ruta)
        assert CATALOGO in marcas, (metodo, path, marcas)
        permisos = {p for tipo, valores in marcas if tipo == "permission" for p in valores}
        assert permisos == {Permission.CATALOGO_EDITAR}, (metodo, path, permisos)


@pytest.mark.parametrize("base", CATALOGOS)
def test_leer_un_catalogo_no_exige_permiso(base):
    """§4: lectura "autenticado" — un Lavador también ve puestos, horarios, medios, tamaños."""
    for (metodo, path), ruta in _rutas().items():
        if (path == base or path.startswith(f"{base}/")) and metodo == "GET":
            marcas = _marcas(ruta)
            assert not any(tipo == "permission" for tipo, _ in marcas), (path, marcas)
            assert marcas, f"{path} sin marca de autorización"
            roles = {r for tipo, valores in marcas if tipo == "role" for r in valores}
            assert {Role.OWNER, Role.STAFF} <= roles, (path, roles)


def test_settings_get_con_sesion_y_patch_solo_owner():
    rutas = _rutas()
    assert ("GET", "/v1/settings") in rutas and ("PATCH", "/v1/settings") in rutas
    get = _marcas(rutas[("GET", "/v1/settings")])
    assert not any(tipo == "permission" for tipo, _ in get), get
    assert {Role.OWNER, Role.STAFF} <= {r for t, v in get if t == "role" for r in v}, get
    assert _marcas(rutas[("PATCH", "/v1/settings")]) == SOLO_OWNER
