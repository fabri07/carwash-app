"""Búsqueda sin extensiones de Postgres — FASE-4-CONTRATO Y8.

`normalize` arma la clave de búsqueda (`search_key`) de cada fila buscable: minúscula, sin
acentos ni signos, espacios colapsados. La tolerancia a errores de tipeo se calcula acá, en Python,
sobre el catálogo del tenant (decenas de filas, cientos de modelos): no hace falta `unaccent` ni
`pg_trgm`, que llegan en F6/F7 para clientes y patentes.

Pura: sin base, sin reloj.
"""

import re
import unicodedata
from collections.abc import Iterable

_NO_ALFANUM = re.compile(r"[^a-z0-9]+")

#: Una palabra de la consulta tolera 1 error desde 4 letras y 2 desde 8 ("detaling" → "detailing").
_MIN_LEN_TOLERANCIA_1 = 4
_MIN_LEN_TOLERANCIA_2 = 8


def normalize(text: str) -> str:
    """`"Tratamiento Cerámico"` → `"tratamiento ceramico"`: sin acentos, minúscula, un espacio."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return _NO_ALFANUM.sub(" ", folded.lower()).strip()


def edit_distance(a: str, b: str) -> int:
    """Levenshtein con transposición adyacente (Damerau, "óptima"): `ab` → `ba` cuesta 1.

    Un error de tipeo típico es girar dos letras, no solo cambiar una.
    """
    if a == b:
        return 0
    rows = [list(range(len(b) + 1))]
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            best = min(rows[i - 1][j] + 1, row[j - 1] + 1, rows[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                best = min(best, rows[i - 2][j - 2] + 1)
            row.append(best)
        rows.append(row)
    return rows[-1][-1]


def _tolerance(word: str) -> int:
    if len(word) >= _MIN_LEN_TOLERANCIA_2:
        return 2
    if len(word) >= _MIN_LEN_TOLERANCIA_1:
        return 1
    return 0


def _word_matches(word: str, key_words: list[str]) -> bool:
    """La palabra aparece como prefijo de alguna del texto, o a distancia tolerable."""
    tolerance = _tolerance(word)
    for candidate in key_words:
        if candidate.startswith(word):
            return True
        if tolerance and edit_distance(word, candidate[: len(word) + tolerance]) <= tolerance:
            return True
    return False


def matches(query: str, search_key: str, *, aliases: Iterable[str] = ()) -> bool:
    """¿`query` encuentra la fila cuya clave es `search_key`?

    Cada palabra de la consulta debe matchear una de la clave (prefijo o error de tipeo). Los
    `aliases` son sinónimos del glosario del rubro ("ppf", "sacar rayas"): se normalizan igual y
    la consulta puede coincidir con la clave **o** con cualquiera de ellos.
    """
    words = normalize(query).split()
    if not words:
        return False
    for key in (search_key, *(normalize(a) for a in aliases)):
        key_words = key.split()
        if key_words and all(_word_matches(w, key_words) for w in words):
            return True
    return False
