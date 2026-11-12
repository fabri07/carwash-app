"""Slug del negocio — FASE-4-CONTRATO Y10 y adenda §11.

El slug identifica al tenant en la URL pública del turnero (F5): `/<slug>`. Es único global entre
negocios vivos y tiene el formato `^[a-z0-9-]{3,40}$`. Lo genera el sistema desde el nombre y el
dueño lo puede editar.

Pura (sin base de datos): la unicidad la garantiza el índice único parcial de `tenants`; acá solo
se decide qué es un slug válido y cómo se arma uno.
"""

import re
import unicodedata

SLUG_MIN = 3
SLUG_MAX = 40
#: Mismo patrón que el CHECK `ck_tenants_slug_formato` de la base.
SLUG_PATTERN = r"^[a-z0-9-]{3,40}$"
_SLUG_RE = re.compile(SLUG_PATTERN)

#: Lo que el slug no puede ser: chocaría con una ruta del propio sistema, o confundiría al
#: cliente ("admin", "api"…). Se guarda en minúscula. F5 (FASE-5-CONTRATO §3.8) suma `catalogo`,
#: `register` e `icons`, que eran rutas o carpetas públicas reales del front: cada carpeta de primer
#: nivel de `frontend/src/app/(grupo)/` y de `frontend/public/` tiene que estar acá (lo verifica
#: `tests/meta/test_slugs_reservados.py`). La migración 0010 congela esta lista.
RESERVED_SLUGS: frozenset[str] = frozenset(
    {
        "admin",
        "api",
        "app",
        "auth",
        "cambiar-clave",
        "catalogo",
        "configuracion",
        "dashboard",
        "debug",
        "health",
        "icons",
        "login",
        "logout",
        "onboarding",
        "ready",
        "register",
        "registro",
        "reservas",
        "static",
        "turnos",
        "v1",
        "www",
    }
)

#: Si el nombre no deja nada usable (solo símbolos, o muy corto).
_FALLBACK = "negocio"


def is_valid_slug(slug: str) -> bool:
    """Formato correcto y no reservado."""
    return bool(_SLUG_RE.fullmatch(slug)) and slug not in RESERVED_SLUGS


def slugify(name: str) -> str:
    """`"Lavadero Demo Norte"` → `"lavadero-demo-norte"`: sin acentos, minúscula, guiones."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug[:SLUG_MAX].strip("-")


def candidate_slug(name: str, *, suffix: str | None = None) -> str:
    """Un slug válido derivado del nombre, con `suffix` opcional para desempatar.

    Sin sufijo es el nombre "limpio"; con sufijo, `<base>-<suffix>` recortando la base para no
    pasar de 40. Un nombre vacío, demasiado corto o reservado cae en `negocio`.
    """
    base = slugify(name)
    if len(base) < SLUG_MIN or base in RESERVED_SLUGS:
        base = _FALLBACK
    if suffix is None:
        return base
    tail = f"-{suffix}"
    return f"{base[: SLUG_MAX - len(tail)].rstrip('-')}{tail}"
