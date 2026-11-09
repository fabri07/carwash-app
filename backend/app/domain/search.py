"""Búsqueda sin extensiones de Postgres — FASE-4-CONTRATO Y8.

`normalize` arma la clave de búsqueda (`search_key`) de cada fila buscable: minúscula, sin
acentos ni signos, espacios colapsados. La tolerancia a errores de tipeo se calcula acá, en Python,
sobre el catálogo del tenant (decenas de filas, cientos de modelos): no hace falta `unaccent` ni
`pg_trgm`, que llegan en F6/F7 para clientes y patentes.

Pura: sin base, sin reloj.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence

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


def _key_matches(words: list[str], key: str) -> bool:
    key_words = key.split()
    return bool(key_words) and all(_word_matches(w, key_words) for w in words)


def matches(query: str, search_key: str, *, aliases: Iterable[str] = ()) -> bool:
    """¿`query` encuentra la fila cuya clave es `search_key`?

    Cada palabra de la consulta debe matchear una de la clave (prefijo o error de tipeo). Los
    `aliases` son sinónimos del glosario del rubro ("ppf", "sacar rayas"): se normalizan igual y
    la consulta puede coincidir con la clave **o** con cualquiera de ellos.
    """
    words = normalize(query).split()
    if not words:
        return False
    return any(_key_matches(words, key) for key in (search_key, *(normalize(a) for a in aliases)))


#: Calidad de una coincidencia, de mejor a peor (`match_tier`).
TIER_EXACT_KEY = 0
TIER_EXACT_ALIAS = 1
TIER_KEY = 2
TIER_ALIAS = 3


def match_tier(wanted: str, search_key: str, *, aliases: Sequence[str] = ()) -> int | None:
    """Qué tan bien encuentra la consulta a la fila, o `None` si no la encuentra.

    **`wanted`, `search_key` y `aliases` ya vienen normalizados** (`normalize`): el llamador
    normaliza la consulta una vez y los alias los guarda normalizados el glosario, así recorrer
    un catálogo no renormaliza por fila. Sirve para ordenar: "ceramico" matchea *Tratamiento
    cerámico* y también *Cerámico de llantas*, pero el primero tiene "ceramico" como sinónimo
    exacto y va antes. Orden: clave idéntica, sinónimo idéntico, coincidencia con la clave,
    coincidencia con un sinónimo.
    """
    words = wanted.split()
    if not words:
        return None
    if wanted == search_key:
        return TIER_EXACT_KEY
    if wanted in aliases:
        return TIER_EXACT_ALIAS
    if _key_matches(words, search_key):
        return TIER_KEY
    if any(_key_matches(words, alias) for alias in aliases):
        return TIER_ALIAS
    return None
