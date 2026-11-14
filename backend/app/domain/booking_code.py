"""Código público del turno — FASE-5-CONTRATO §4.2 (paso 7) y §6.

`TUR-` + 6 caracteres de un alfabeto sin los que se confunden al dictarlos o copiarlos de una
captura (`0/O`, `1/I/L`): 31 símbolos, ~887 M combinaciones. Con el WhatsApp del cliente es la
llave para ver y cancelar el turno desde la web (D5-8), así que sale de `secrets` y no de
`random`.

El código es único **entre los turnos vivos del negocio** (`ux_bookings_tenant_id_code`); el
servicio reintenta con otro si choca (máx. `MAX_CODE_ATTEMPTS`).
"""

import secrets
from collections.abc import Callable

#: Sin 0/O/1/I/L.
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_PREFIX = "TUR-"
CODE_LENGTH = 6
#: Reintentos ante un choque con el único vivo (con 887 M combinaciones, el segundo ya sobra).
MAX_CODE_ATTEMPTS = 5

_ALLOWED = frozenset(CODE_ALPHABET)


def new_booking_code(choice: Callable[[str], str] = secrets.choice) -> str:
    """Un código nuevo. `choice` se inyecta en los tests para forzar un choque."""
    return CODE_PREFIX + "".join(choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_booking_code(raw: str | None) -> str | None:
    """Lo que tipeó el cliente → `TUR-XXXXXX`, o `None` si no puede ser un código.

    Acepta minúsculas, espacios, sin prefijo (`k7m2qx`) o con el guion cambiado por espacio.
    No adivina: un carácter fuera del alfabeto (un `0` por una `O`) no es un código.
    """
    cleaned = "".join((raw or "").split()).upper()
    # El prefijo se saca solo si lo que queda tiene el largo de un código: `TURABC` sin prefijo
    # es un código posible (T, U y R están en el alfabeto).
    for prefix in (CODE_PREFIX, CODE_PREFIX[:-1]):
        if cleaned.startswith(prefix) and len(cleaned) == len(prefix) + CODE_LENGTH:
            cleaned = cleaned[len(prefix) :]
            break
    if len(cleaned) != CODE_LENGTH or not set(cleaned) <= _ALLOWED:
        return None
    return CODE_PREFIX + cleaned
