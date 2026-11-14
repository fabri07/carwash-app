"""FASE-5-CONTRATO §2 (PR 5.3): la bandeja del dueño tiene la autorización del contrato.

Ver (lista, contador, detalle, señas a resolver) con `AGENDA_VER`; confirmar y resolver señas con
`COBROS_REGISTRAR`; todo lo demás (aprobar, rechazar, cancelar, no asistió, incidencias,
reprogramar) con `TURNOS_GESTIONAR`. Sin permisos nuevos (Z3). Estático: sin base.
"""

from fastapi.routing import APIRoute

from app.domain.permissions import Permission
from app.main import create_app
from app.tests.meta.test_permisos import _autorizaciones

PREFIJOS = ("/v1/bookings", "/v1/incident-marks", "/v1/cancellations")
COBRO = {"/v1/bookings/{id}/confirm-deposit", "/v1/cancellations/{id}/resolve"}


def _esperado(ruta: APIRoute) -> Permission:
    if ruta.path in COBRO:
        return Permission.COBROS_REGISTRAR
    if ruta.methods == {"GET"}:
        return Permission.AGENDA_VER
    return Permission.TURNOS_GESTIONAR


def test_la_bandeja_tiene_la_autorizacion_del_contrato():
    vistas = 0
    for ruta in create_app().routes:
        if not (isinstance(ruta, APIRoute) and ruta.path.startswith(PREFIJOS)):
            continue
        assert _autorizaciones(ruta.dependant) == {("permission", frozenset({_esperado(ruta)}))}, (
            ruta.methods,
            ruta.path,
        )
        vistas += 1
    # turnos: lista, contador, detalle + 7 acciones; marca (anular); cancelaciones: lista, resolver
    assert vistas == 3 + 7 + 1 + 2
