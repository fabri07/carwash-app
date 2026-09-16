"""T3b #1 — toda dependencia de sesión se cierra antes de responder (`scope="function"`)."""

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.main import create_app
from app.persistence.db.session import get_db_session


def _dependencias(dependant: Dependant):
    for sub in dependant.dependencies:
        yield sub
        yield from _dependencias(sub)


def test_toda_dependencia_de_sesion_tiene_scope_function():
    vistas = 0
    culpables = []
    for ruta in create_app().routes:
        if not isinstance(ruta, APIRoute):
            continue
        for dep in _dependencias(ruta.dependant):
            if dep.call is get_db_session:
                vistas += 1
                if dep.scope != "function":
                    culpables.append(f"{ruta.path} (scope={dep.scope!r})")
    assert vistas, "no se encontró ninguna dependencia de sesión: el barrido no mira nada"
    assert not culpables, f"sesión que commitea después de responder: {culpables}"


def test_ninguna_otra_dependencia_yield_de_la_app_corre_despues_de_responder():
    # Hoy la única dependencia con `yield` es la sesión. Si aparece otra (async gen)
    # sin scope explícito, este test obliga a decidir cuándo debe cerrarse.
    import inspect

    culpables = set()
    for ruta in create_app().routes:
        if not isinstance(ruta, APIRoute):
            continue
        for dep in _dependencias(ruta.dependant):
            es_generador = inspect.isasyncgenfunction(dep.call) or inspect.isgeneratorfunction(
                dep.call
            )
            if es_generador and dep.scope != "function":
                culpables.add(getattr(dep.call, "__qualname__", repr(dep.call)))
    assert not culpables, f"dependencias yield sin scope='function': {culpables}"
