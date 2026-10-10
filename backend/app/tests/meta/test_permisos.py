"""A3 · FASE-4-CONTRATO §2 — permisos como Enum congelado y ninguna ruta sin autorización.

Escrito por el Tester-aislamiento del PR 4.1, que no implementó los permisos. Parte estática
(sin base): la de Postgres (CHECK `permisos_conocidos`) vive en
`tests/persistence/test_cuentas_pg.py` y la matriz HTTP en
`tests/security/test_autorizacion_equipo.py`.

- `Permission` en MAYÚSCULA (= nombre), igual que `Role` (ADR-0005, `test_roles.py`).
- La migración 0004 congela los mismos valores y perfiles por defecto que el dominio: agregar
  un permiso sin migrar el CHECK hace fallar este archivo.
- Nadie escribe un permiso como string en `app/`: se usa el enum (un typo sería un permiso que
  nadie tiene y una ruta que nadie puede usar, o al revés).
- **Toda ruta de `/v1`** fuera de auth (y `/health`, `/ready`) depende de una closure de
  `require_role` o `require_permission`, reconocible por su atributo `.authorization`.
"""

import ast
import importlib.util
from collections.abc import Callable
from types import ModuleType
from typing import Annotated, Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.api.v1.deps import CurrentUser, OwnerUser, require_permission, require_role
from app.domain.permissions import (
    ALL_PERMISSIONS,
    DEFAULT_PROFILES,
    LEGACY_STAFF_PROFILE,
    Permission,
    sorted_permissions,
)
from app.domain.roles import Role
from app.main import create_app
from app.persistence.models.permission_profile import PERMISSIONS_SQL_ARRAY
from app.persistence.models.user import USERNAME_PATTERN
from app.tests.meta._rutas import APP, BACKEND, fuentes_de_app

MIGRACION_0004 = BACKEND / "app/persistence/migrations/versions/20261009_0004_cuentas_permisos.py"

#: Rutas de `/v1` que no llevan autorización por rol o permiso, con el motivo.
SIN_AUTORIZACION_PERMITIDAS: dict[str, str] = {
    "/v1/auth/register": "público: crea el negocio",
    "/v1/auth/login": "público",
    "/v1/auth/refresh": "lee su propia cookie de refresh",
    "/v1/auth/logout": "lee su propia cookie de refresh",
    "/v1/auth/me": "toda sesión válida, aun con cambio de clave pendiente (§2.3)",
    "/v1/auth/change-password": "toda sesión válida: es la salida del cambio obligatorio",
    "/v1/debug/boom": "solo fuera de producción; levanta un error sin tocar datos (A13)",
}


# ── El enum ───────────────────────────────────────────────────────────────────


def test_los_valores_son_mayuscula_e_iguales_al_nombre():
    assert len(Permission) == 12
    assert all(p.value == p.value.upper() == p.name for p in Permission)
    assert frozenset(Permission) == ALL_PERMISSIONS


def test_los_perfiles_por_defecto_son_los_del_contrato():
    """§2.2: Encargado = todo; Cajero ⊇ Lavador (D4-10); ninguno anula cobros salvo Encargado."""
    assert set(DEFAULT_PROFILES) == {"Encargado", "Cajero", "Lavador"}
    assert DEFAULT_PROFILES["Encargado"] == ALL_PERMISSIONS
    assert DEFAULT_PROFILES["Cajero"] == {
        Permission.AGENDA_VER,
        Permission.TURNOS_GESTIONAR,
        Permission.JOBS_OPERAR,
        Permission.COBROS_REGISTRAR,
        Permission.CAJA_VER,
        Permission.CLIENTES_VER,
        Permission.CLIENTES_EDITAR,
    }
    assert DEFAULT_PROFILES["Lavador"] == {
        Permission.AGENDA_VER,
        Permission.JOBS_OPERAR,
        Permission.CLIENTES_VER,
    }
    assert DEFAULT_PROFILES["Lavador"] < DEFAULT_PROFILES["Cajero"]
    assert Permission.COBROS_ANULAR not in DEFAULT_PROFILES["Cajero"]
    assert LEGACY_STAFF_PROFILE == "Encargado"


def test_sorted_permissions_sigue_el_orden_del_enum():
    assert sorted_permissions(ALL_PERMISSIONS) == list(Permission)
    desordenado = {Permission.REPORTES_VER, Permission.AGENDA_VER}
    assert sorted_permissions(desordenado) == [Permission.AGENDA_VER, Permission.REPORTES_VER]


# ── La migración congela lo mismo ─────────────────────────────────────────────


def _cargar_migracion() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migracion_0004", MIGRACION_0004)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_la_migracion_congela_los_mismos_permisos_en_el_mismo_orden():
    migracion = _cargar_migracion()
    assert tuple(p.value for p in Permission) == migracion.PERMISSIONS


def test_la_migracion_congela_los_mismos_perfiles_por_defecto():
    migracion = _cargar_migracion()
    assert {nombre: set(perms) for nombre, perms in migracion.DEFAULT_PROFILES.items()} == {
        nombre: {p.value for p in perms} for nombre, perms in DEFAULT_PROFILES.items()
    }
    assert migracion.LEGACY_STAFF_PROFILE == LEGACY_STAFF_PROFILE
    assert migracion.USERNAME_PATTERN == USERNAME_PATTERN


def test_el_check_del_modelo_contiene_todos_los_permisos():
    for p in Permission:
        assert f"'{p.value}'" in PERMISSIONS_SQL_ARRAY, p
    # y nada más: tantos literales como valores del enum
    assert PERMISSIONS_SQL_ARRAY.count("'") == 2 * len(Permission)
    assert PERMISSIONS_SQL_ARRAY.endswith("]::text[]")


def test_el_check_de_la_migracion_es_el_mismo_que_el_del_modelo():
    migracion = _cargar_migracion()
    assert migracion._array(migracion.PERMISSIONS) == PERMISSIONS_SQL_ARRAY


# ── Sin literales de permiso en el código ─────────────────────────────────────

#: Donde un valor de permiso como string es legítimo: la definición del enum.
DEFINICIONES = {APP / "domain/permissions.py"}


def test_no_hay_literales_de_permiso_en_el_codigo():
    valores = {p.value for p in Permission}
    culpables = []
    for fuente in fuentes_de_app():
        if fuente in DEFINICIONES:
            continue
        for nodo in ast.walk(ast.parse(fuente.read_text())):
            if isinstance(nodo, ast.Constant) and nodo.value in valores:
                culpables.append(f"{fuente.relative_to(APP)}:{nodo.lineno} {nodo.value!r}")
    assert not culpables, f"usar `Permission.<X>`, no el string: {culpables}"


def test_el_detector_de_literales_ve_un_string_suelto():
    codigo = 'require_permission("COBROS_ANULAR")'
    constantes = {n.value for n in ast.walk(ast.parse(codigo)) if isinstance(n, ast.Constant)}
    assert constantes & {p.value for p in Permission} == {"COBROS_ANULAR"}


# ── Toda ruta de negocio depende de require_role / require_permission ─────────


def _autorizaciones(dependant: Dependant) -> set[tuple[str, frozenset[Any]]]:
    """Las `.authorization` de toda dependencia, recursivo (como `_llamadas` de L2)."""
    encontradas: set[tuple[str, frozenset[Any]]] = set()
    for sub in dependant.dependencies:
        marca = getattr(sub.call, "authorization", None)
        if marca is not None:
            encontradas.add(marca)
        encontradas |= _autorizaciones(sub)
    return encontradas


def _rutas_sin_autorizacion(app: FastAPI) -> set[str]:
    return {
        f"{sorted(ruta.methods)[0]} {ruta.path}"
        for ruta in app.routes
        if isinstance(ruta, APIRoute)
        and ruta.path.startswith("/v1/")
        and not _autorizaciones(ruta.dependant)
    }


def test_toda_ruta_v1_fuera_de_auth_exige_rol_o_permiso():
    app = create_app()
    sin = {r.split(" ", 1)[1] for r in _rutas_sin_autorizacion(app)}
    sobrantes = sin - set(SIN_AUTORIZACION_PERMITIDAS)
    assert not sobrantes, f"rutas sin require_role/require_permission: {sorted(sobrantes)}"
    # y las excepciones siguen existiendo (si una se borra, se saca de la lista)
    rutas = {r.path for r in app.routes if isinstance(r, APIRoute)}
    assert set(SIN_AUTORIZACION_PERMITIDAS) <= rutas


def test_las_rutas_de_equipo_son_solo_del_owner():
    """A4: perfiles y staff son OWNER-only (Y5), en todos los métodos."""
    app = create_app()
    for ruta in app.routes:
        if isinstance(ruta, APIRoute) and ruta.path.startswith(
            ("/v1/permission-profiles", "/v1/staff")
        ):
            assert _autorizaciones(ruta.dependant) == {("role", frozenset({Role.OWNER}))}, (
                ruta.methods,
                ruta.path,
            )


def _mini_app(*dependencias: Callable[..., Any]) -> FastAPI:
    router = APIRouter()

    @router.get("/sin")
    async def sin(
        user: CurrentUser,
    ) -> dict[str, str]:  # pragma: no cover  # la ruta solo se inspecciona, nunca se llama
        return {}

    @router.get("/rol")
    async def rol(
        user: OwnerUser,
    ) -> dict[str, str]:  # pragma: no cover  # la ruta solo se inspecciona, nunca se llama
        return {}

    @router.get("/permiso", dependencies=[Depends(require_permission(Permission.CAJA_VER))])
    async def permiso() -> (
        dict[str, str]
    ):  # pragma: no cover  # la ruta solo se inspecciona, nunca se llama
        return {}

    Anidado = Annotated[Any, Depends(require_permission(Permission.REPORTES_VER))]

    async def intermedia(
        x: Anidado,
    ) -> None:  # pragma: no cover  # la ruta solo se inspecciona, nunca se llama
        return None

    @router.get("/anidado", dependencies=[Depends(intermedia)])
    async def anidado() -> (
        dict[str, str]
    ):  # pragma: no cover  # la ruta solo se inspecciona, nunca se llama
        return {}

    app = FastAPI()
    app.include_router(router, prefix="/v1")
    return app


def test_el_detector_distingue_rutas_con_y_sin_autorizacion():
    app = _mini_app()
    assert _rutas_sin_autorizacion(app) == {"GET /v1/sin"}
    rutas = {r.path: r for r in app.routes if isinstance(r, APIRoute)}
    assert _autorizaciones(rutas["/v1/rol"].dependant) == {("role", frozenset({Role.OWNER}))}
    assert _autorizaciones(rutas["/v1/permiso"].dependant) == {
        ("permission", frozenset({Permission.CAJA_VER}))
    }
    # anidada dos niveles: igual la ve
    assert _autorizaciones(rutas["/v1/anidado"].dependant) == {
        ("permission", frozenset({Permission.REPORTES_VER}))
    }


def test_las_closures_llevan_la_marca_que_lee_el_detector():
    rol = require_role(Role.OWNER)
    assert getattr(rol, "authorization", None) == ("role", frozenset({Role.OWNER}))
    combinado = require_permission(Permission.CATALOGO_EDITAR, Permission.PRECIOS_EDITAR)
    assert getattr(combinado, "authorization", None) == (
        "permission",
        frozenset({Permission.CATALOGO_EDITAR, Permission.PRECIOS_EDITAR}),
    )


def test_require_permission_sin_permisos_no_se_puede_construir():
    # Un `require_permission()` vacío dejaría pasar a cualquier STAFF: falla al importar.
    with pytest.raises(ValueError):
        require_permission()
