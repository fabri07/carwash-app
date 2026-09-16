"""L2 — toda ruta que abre sesión de base depende de `CurrentUser`.

`get_db_session` fija `app.tenant_id` a partir de la FIRMA del token, sin mirar
`users` (no puede: todavía no hay contexto). Quien valida que el usuario exista y
que `ver` coincida es `get_current_user`. Una ruta con sesión y sin usuario leería
datos con un token revocado. Las excepciones son las de auth, que manejan su propio
contexto, y van con nombre.
"""

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.api.v1.deps import get_current_user
from app.main import create_app
from app.persistence.db.session import get_db_session

SIN_USUARIO_PERMITIDAS = {
    "/v1/auth/register",
    "/v1/auth/login",
    "/v1/auth/refresh",
    "/v1/auth/logout",
}


def _llamadas(dependant: Dependant) -> set:
    encontradas = set()
    for sub in dependant.dependencies:
        encontradas.add(sub.call)
        encontradas |= _llamadas(sub)
    return encontradas


def test_toda_ruta_con_sesion_depende_de_current_user():
    culpables = []
    for ruta in create_app().routes:
        if not isinstance(ruta, APIRoute):
            continue
        llamadas = _llamadas(ruta.dependant)
        if get_db_session in llamadas and get_current_user not in llamadas:
            culpables.append(ruta.path)
    assert (
        set(culpables) == SIN_USUARIO_PERMITIDAS
    ), f"usan la sesión sin CurrentUser: {set(culpables) - SIN_USUARIO_PERMITIDAS}"


def test_el_detector_ve_las_dependencias_anidadas():
    rutas = {r.path: r for r in create_app().routes if isinstance(r, APIRoute)}
    assert get_current_user in _llamadas(rutas["/v1/dummy-resources/{id}"].dependant)
