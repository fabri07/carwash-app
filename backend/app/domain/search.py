"""Normalización de texto para buscar — FASE-4-CONTRATO Y8.

Cada fila buscable guarda `search_key = normalize(texto)` y la consulta se normaliza igual:
sin acentos, en minúsculas, con todo lo que no sea letra o dígito colapsado a un espacio.
Sin extensiones de Postgres (`unaccent`, `pg_trgm`): la tolerancia a errores de tipeo se
calcula en Python sobre el catálogo del tenant (PR 4.3), que es chico.
"""

import re
import unicodedata

_NO_ALNUM = re.compile(r"[^a-z0-9]+")


def strip_accents(text: str) -> str:
    """`"Cerámico ñandú"` → `"Ceramico nandu"`: descompone (NFKD) y saca las marcas."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize(text: str) -> str:
    """`"  Tratamiento  CERÁMICO (9H)"` → `"tratamiento ceramico 9h"`."""
    return _NO_ALNUM.sub(" ", strip_accents(text).lower()).strip()
