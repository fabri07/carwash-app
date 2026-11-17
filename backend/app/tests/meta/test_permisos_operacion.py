"""FASE-6-CONTRATO §1 (PR 6.1): la operación del job tiene la autorización del contrato.

Ver (el job y su línea de tiempo) con `AGENDA_VER`; llegó, iniciar, finalizar, retirar, cancelar por
demora, la recepción opcional y la carga retrospectiva con `JOBS_OPERAR`; cobrar con
`COBROS_REGISTRAR` y anular un cobro con `COBROS_ANULAR`. El turno "solo a mano" es
`TURNOS_GESTIONAR` (lo cubre `test_permisos_bandeja.py`). Sin permisos nuevos. Estático: sin base.
"""

from fastapi.routing import APIRoute

from app.domain.permissions import Permission
from app.main import create_app
from app.tests.meta.test_permisos import _autorizaciones

ESPERADO = {
    ("GET", "/v1/jobs/{id}"): Permission.AGENDA_VER,
    ("GET", "/v1/jobs/{id}/events"): Permission.AGENDA_VER,
    ("POST", "/v1/jobs"): Permission.JOBS_OPERAR,
    ("POST", "/v1/jobs/{id}/start"): Permission.JOBS_OPERAR,
    ("POST", "/v1/jobs/{id}/finish"): Permission.JOBS_OPERAR,
    ("POST", "/v1/jobs/{id}/pick-up"): Permission.JOBS_OPERAR,
    ("POST", "/v1/jobs/{id}/cancel-delay"): Permission.JOBS_OPERAR,
    ("POST", "/v1/jobs/{id}/inspection"): Permission.JOBS_OPERAR,
    ("POST", "/v1/jobs/retrospective"): Permission.JOBS_OPERAR,
    ("POST", "/v1/jobs/{id}/payments"): Permission.COBROS_REGISTRAR,
    ("POST", "/v1/jobs/{id}/payments/{payment_id}/void"): Permission.COBROS_ANULAR,
    ("POST", "/v1/bookings/manual"): Permission.TURNOS_GESTIONAR,
    ("GET", "/v1/agenda/day"): Permission.AGENDA_VER,
}


def test_la_operacion_tiene_la_autorizacion_del_contrato():
    vistas = {}
    for ruta in create_app().routes:
        if not isinstance(ruta, APIRoute):
            continue
        for metodo in ruta.methods:
            clave = (metodo, ruta.path)
            if clave in ESPERADO:
                assert _autorizaciones(ruta.dependant) == {
                    ("permission", frozenset({ESPERADO[clave]}))
                }, clave
                vistas[clave] = ruta
    assert set(vistas) == set(ESPERADO), "una ruta de la operación no existe o cambió de ruta"
    # Todo `/v1/jobs` está en la tabla: una ruta nueva sin permiso declarado falla acá.
    todas = {
        (m, r.path)
        for r in create_app().routes
        if isinstance(r, APIRoute) and r.path.startswith("/v1/jobs")
        for m in r.methods
    }
    assert todas <= set(ESPERADO)


def test_toda_accion_de_la_operacion_exige_idempotency_key():
    """A2: cada acción POST de `/v1/jobs` declara la clave como header obligatorio. El `occurred_at`
    obligatorio lo exigen los cuerpos (`app/tests/api/test_operacion.py`)."""
    for ruta in create_app().routes:
        if not (isinstance(ruta, APIRoute) and ruta.path.startswith("/v1/jobs")):
            continue
        if "POST" not in ruta.methods:
            continue
        cabeceras = {h.alias: h for h in ruta.dependant.header_params}
        assert "Idempotency-Key" in cabeceras, ruta.path
        assert cabeceras["Idempotency-Key"].field_info.is_required(), ruta.path
