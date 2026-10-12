"""Slug del negocio — FASE-4-CONTRATO Y10.

La URL pública de F5 identifica al tenant por `tenants.slug`: único global entre vivos,
`^[a-z0-9-]{3,40}$`, generado del nombre al darse de alta y editable después.
"""

import re

from app.domain.search import normalize

SLUG_PATTERN = "^[a-z0-9-]{3,40}$"
SLUG_RE = re.compile(SLUG_PATTERN)
SLUG_MAX = 40
#: Lugar que se deja para el sufijo `-xxxxxx` cuando el slug del nombre ya está tomado.
SUFFIX_LEN = 7
FALLBACK = "negocio"


def slugify(name: str) -> str:
    """`"Lavadero Ñandú & Cía."` → `"lavadero-nandu-cia"`. Nunca devuelve menos de 3 caracteres."""
    base = normalize(name).replace(" ", "-")[: SLUG_MAX - SUFFIX_LEN].strip("-")
    return base if len(base) >= 3 else FALLBACK


def with_suffix(slug: str, suffix: str) -> str:
    """Agrega `-suffix` sin pasarse del largo máximo."""
    return f"{slug[: SLUG_MAX - len(suffix) - 1].strip('-')}-{suffix}"


def is_valid_slug(value: str) -> bool:
    return SLUG_RE.fullmatch(value) is not None
