"""Nombres de las cookies de sesión (ADR-0009).

El nombre real es `COOKIE_NAME_PREFIX` + nombre base (contrato con `frontend` y
`deploy`). Las constantes son los nombres base; con el prefijo por defecto (`""`)
coinciden con el nombre real. El código de la app usa siempre las funciones.
"""

from app.config.settings import get_settings

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
#: El refresh solo viaja al router que lo usa.
REFRESH_COOKIE_PATH = "/v1/auth"


def access_cookie_name() -> str:
    return get_settings().COOKIE_NAME_PREFIX + ACCESS_COOKIE


def refresh_cookie_name() -> str:
    return get_settings().COOKIE_NAME_PREFIX + REFRESH_COOKIE
