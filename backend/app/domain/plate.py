"""Patentes — FASE-3-CONTRATO §1.2 y §3, R-C-008/009.

La patente es el PII fuerte de este dominio (ver `_PATENTE_VALUE_RE` en el scrubbing).
`vehicles.plate` guarda lo que se tipeó; `plate_normalized` es la clave de búsqueda
(`CHECK (plate_normalized ~ '^[A-Z0-9]{1,10}$')`), y NULL cuando no hay patente.
"""

import re

from app.domain.enums import PlateFormat
from app.domain.exceptions import InvalidPlateError

#: Largo máximo de `plate_normalized` (el del legacy, `normalizePlate_`).
MAX_PLATE_LENGTH = 10

_NON_PLATE = re.compile(r"[^A-Z0-9]")

# Centinelas humanos de "sin patente" (R-C-009: `SIN000` se tipeaba para forzar el alta).
_SENTINEL = re.compile(r"0+|SIN0*|SINPATENTE")

_AR_1994 = re.compile(r"[A-Z]{3}[0-9]{3}")
_MERCOSUR = re.compile(r"[A-Z]{2}[0-9]{3}[A-Z]{2}")


def normalize_plate(raw: str | None) -> str | None:
    """Mayúsculas y solo `A-Z0-9` ASCII; vacío o centinela (`SIN000`, `000000`) → `None`.

    **[corregir]** el legacy truncaba a 10 caracteres en silencio: dos patentes distintas
    podían colisionar. Acá más de 10 levanta `InvalidPlateError`.
    """
    normalized = _NON_PLATE.sub("", (raw or "").upper())
    if not normalized or _SENTINEL.fullmatch(normalized):
        return None
    if len(normalized) > MAX_PLATE_LENGTH:
        raise InvalidPlateError(f"plate longer than {MAX_PLATE_LENGTH} characters")
    return normalized


def classify_plate(normalized: str) -> PlateFormat:
    """`AAA000` → `AR_1994`; `AA000AA` → `MERCOSUR`; el resto `OTRO` (se acepta con aviso)."""
    if _AR_1994.fullmatch(normalized):
        return PlateFormat.AR_1994
    if _MERCOSUR.fullmatch(normalized):
        return PlateFormat.MERCOSUR
    return PlateFormat.OTRO
