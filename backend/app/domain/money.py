"""Dinero en centavos — FASE-3-CONTRATO X3 y §3.

Enteros de punta a punta: pesos × 100 en `BIGINT`, porcentajes en puntos básicos
(0..10000) y un único redondeo, half-up al centavo.

**[corregir] R-O-029.** `parseMoney_` leía `"20.000"` como `20` (el punto de miles
pasaba como decimal), `"1,234,567"` como `1.234567` y un texto inválido como `-1`
(o como `0` en `adminFinishService_`). Acá el locale es explícito (`es-AR`: `.` miles,
`,` decimales) y **lo ambiguo se rechaza** con `InvalidAmountError`: nunca se adivina ni se
devuelve `0` por un inválido.
"""

import re

from app.domain.exceptions import InvalidAmountError

#: Máximo de una columna `BIGINT` de Postgres.
BIGINT_MAX = 2**63 - 1

#: Denominador de los puntos básicos: 10000 bps = 100 %.
BPS_DENOMINATOR = 10_000

# Parte entera: `0`, o miles agrupados de a tres con punto (el primer grupo no arranca
# en 0), o dígitos corridos sin cero inicial. Decimales: una o dos cifras tras la coma.
# `[0-9]` y no `\d`: `\d` acepta dígitos Unicode (`٣`).
_ARS_RE = re.compile(
    r"(?P<int>0|[1-9][0-9]{0,2}(?:\.[0-9]{3})+|[1-9][0-9]*)(?:,(?P<dec>[0-9]{1,2}))?"
)


def parse_ars(text: str) -> int:
    """Parsea un importe tipeado en pesos argentinos y lo devuelve en centavos.

    Acepta un `$` inicial y espacios alrededor (`"$ 20.000,50"`). Rechaza vacío,
    negativos, espacios internos, más de dos decimales y miles mal agrupados:
    `"20.00"` (¿veinte o dos mil?) y `"20,000"` (¿formato en-US?) son ambiguos.
    `"0"` es un importe válido y devuelve `0`.
    """
    cleaned = text.strip()
    if cleaned.startswith("$"):
        cleaned = cleaned[1:].lstrip()
    match = _ARS_RE.fullmatch(cleaned)
    if match is None:
        raise InvalidAmountError(f"not an unambiguous es-AR amount: {text!r}")
    pesos = int(match["int"].replace(".", ""))
    cents = pesos * 100 + int((match["dec"] or "0").ljust(2, "0"))
    if cents > BIGINT_MAX:
        raise InvalidAmountError(f"amount does not fit in BIGINT: {text!r}")
    return cents


def format_ars(cents: int) -> str:
    """Formatea centavos como `"$ 20.000"` o `"$ 20.000,50"` (los decimales solo si hay).

    Un negativo (saldo a favor del cliente) sale como `"-$ 1.500"`.
    """
    sign = "-" if cents < 0 else ""
    pesos, rest = divmod(abs(cents), 100)
    thousands = f"{pesos:,}".replace(",", ".")
    decimals = f",{rest:02d}" if rest else ""
    return f"{sign}$ {thousands}{decimals}"


def apply_bps(cents: int, bps: int) -> int:
    """`cents × bps / 10000` redondeado half-up al centavo (seña, comisión).

    Exige `cents >= 0` y `0 <= bps <= 10000`, como los `CHECK` de las columnas `*_bps`:
    con negativos "half-up" deja de ser una sola regla.
    """
    if cents < 0:
        raise InvalidAmountError(f"amount must be >= 0: {cents}")
    if not 0 <= bps <= BPS_DENOMINATOR:
        raise InvalidAmountError(f"basis points must be in 0..{BPS_DENOMINATOR}: {bps}")
    return (cents * bps + BPS_DENOMINATOR // 2) // BPS_DENOMINATOR
