"""Teléfonos argentinos a E.164 — FASE-3-CONTRATO §3, R-C-005/006/007.

**[corregir] R-C-005.** El legacy tenía tres políticas de identidad telefónica en la
misma operación (dígitos completos, igualdad exacta, últimos 10) y una cuarta solo para
el link de WhatsApp (R-C-007). Acá hay **una**: el E.164 móvil (`+549…`) es la clave de
`customers.phone_e164`; lo tipeado se guarda aparte en `phone_raw`.

Lo que no se puede resolver sin adivinar queda **ambiguo** (`e164=None`,
`ambiguous=True`) para que lo decida una persona: un `54` sin el `9` de celular puede
ser un fijo o un celular mal tipeado, y el `15` local no dice a qué área pertenece.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

_NON_DIGITS = re.compile(r"[^0-9]")

#: Largo del número nacional argentino: código de área + abonado, sin `0` ni `15`.
NATIONAL_LENGTH = 10


class PhoneReason(StrEnum):
    """Por qué un teléfono no se normalizó. No se persiste: es diagnóstico."""

    EMPTY = "EMPTY"
    INVALID_LENGTH = "INVALID_LENGTH"
    INVALID_NATIONAL = "INVALID_NATIONAL"  # el número nacional arranca con 0
    MISSING_MOBILE_9 = "MISSING_MOBILE_9"  # 12 dígitos: `54` sin `9` (¿fijo o celular?)
    LOCAL_PREFIX_15 = "LOCAL_PREFIX_15"  # `15` local: sin área o con área + 15


@dataclass(frozen=True, slots=True)
class PhoneResult:
    """`e164` es `None` si el número es inválido o ambiguo; `reason` dice por qué."""

    e164: str | None
    ambiguous: bool
    reason: PhoneReason | None


def _invalid(reason: PhoneReason) -> PhoneResult:
    return PhoneResult(e164=None, ambiguous=False, reason=reason)


def _ambiguous(reason: PhoneReason) -> PhoneResult:
    return PhoneResult(e164=None, ambiguous=True, reason=reason)


def _from_national(national: str) -> PhoneResult:
    if national.startswith("0"):
        return _invalid(PhoneReason.INVALID_NATIONAL)
    if national.startswith("15"):
        # Ningún código de área argentino empieza con 15: es el `15` local sin área.
        return _ambiguous(PhoneReason.LOCAL_PREFIX_15)
    return PhoneResult(e164=f"+549{national}", ambiguous=False, reason=None)


def normalize_phone_ar(raw: str | None) -> PhoneResult:
    """Normaliza un teléfono argentino a E.164 móvil, o dice por qué no.

    Reglas, sobre los dígitos (se descarta todo lo demás, incluido el `+`):

    - 11 o 13 dígitos con `0` inicial: se saca el `0` (prefijo de larga distancia).
    - 10 dígitos → `+549` + los 10.
    - 13 dígitos que empiezan con `549` → `+549` + los 10 restantes.
    - 12 dígitos con `54` sin `9` → ambiguo (`MISSING_MOBILE_9`).
    - 12 dígitos con `15` después de un área de 2, 3 o 4 cifras → ambiguo
      (`LOCAL_PREFIX_15`); un nacional de 10 que empieza con `15` también.
    - Cualquier otro largo → inválido, no ambiguo. Solo cubre números argentinos.
    """
    digits = _NON_DIGITS.sub("", raw or "")
    if not digits:
        return _invalid(PhoneReason.EMPTY)
    if len(digits) in (11, 13) and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == NATIONAL_LENGTH:
        return _from_national(digits)
    if len(digits) == 13 and digits.startswith("549"):
        return _from_national(digits[3:])
    if len(digits) == 12 and not digits.startswith("549"):
        if digits.startswith("54"):
            return _ambiguous(PhoneReason.MISSING_MOBILE_9)
        if "15" in (digits[2:4], digits[3:5], digits[4:6]):
            return _ambiguous(PhoneReason.LOCAL_PREFIX_15)
    return _invalid(PhoneReason.INVALID_LENGTH)


def phones_equivalent(a: str | None, b: str | None) -> bool:
    """Semántica de `phonesEquivalent_` (R-C-006): últimos `min(10, len a, len b)` dígitos.

    Exige al menos 8 dígitos en común; vacío o `None` → `False`. **Solo para la migración
    de F8** (reconciliar los `CLI-…` duplicados del legacy): la identidad en el sistema
    nuevo es `phone_e164`, nunca esta comparación tolerante.

    A diferencia del legacy, dos números idénticos de menos de 8 dígitos **no** son
    equivalentes: el contrato pide sufijo ≥ 8 sin excepción.
    """
    left = _NON_DIGITS.sub("", a or "")
    right = _NON_DIGITS.sub("", b or "")
    n = min(10, len(left), len(right))
    return n >= 8 and left[-n:] == right[-n:]
