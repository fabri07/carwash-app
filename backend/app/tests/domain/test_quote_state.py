"""`quote_state.py` — FASE-3-CONTRATO §2.3 (diseñado de cero, R-C-027).

La tabla esperada está copiada **a mano** del contrato.
"""

import pytest

from app.domain.enums import QuoteStatus
from app.domain.exceptions import GuardFailedError, InvalidTransition
from app.domain.quote_state import (
    TERMINAL_QUOTE_STATUSES,
    QuoteAction,
    check_quote_terms,
    next_quote_status,
)

Q = QuoteStatus
A = QuoteAction

ESPERADO: dict[tuple[QuoteStatus | None, QuoteAction], QuoteStatus] = {
    # Nace PENDIENTE (§2.1: el alta A_COTIZAR "crea la cotización PENDIENTE").
    (None, A.CREATE): Q.PENDIENTE,
    (Q.PENDIENTE, A.QUOTE): Q.COTIZADO,
    (Q.COTIZADO, A.ACCEPT): Q.ACEPTADO,
    (Q.COTIZADO, A.REJECT): Q.RECHAZADO,
    (Q.PENDIENTE, A.CANCEL): Q.CANCELADO,
    (Q.COTIZADO, A.CANCEL): Q.CANCELADO,
    (Q.PENDIENTE, A.EXPIRE): Q.VENCIDO,
    (Q.COTIZADO, A.EXPIRE): Q.VENCIDO,
}

ORIGENES: list[QuoteStatus | None] = [None, *QuoteStatus]
PRODUCTO = [(o, a) for o in ORIGENES for a in QuoteAction]


@pytest.mark.parametrize(("origen", "accion"), PRODUCTO, ids=lambda x: str(x))
def test_transicion_pasa_si_y_solo_si_esta_en_el_contrato(origen, accion):
    if (origen, accion) in ESPERADO:
        assert next_quote_status(origen, accion) is ESPERADO[(origen, accion)]
    else:
        with pytest.raises(InvalidTransition) as exc:
            next_quote_status(origen, accion)
        assert exc.value.machine == "quote"


def test_terminales():
    assert {Q.ACEPTADO, Q.RECHAZADO, Q.CANCELADO, Q.VENCIDO} == TERMINAL_QUOTE_STATUSES
    assert not {o for (o, _a) in ESPERADO if o in TERMINAL_QUOTE_STATUSES}


def test_cotizar_exige_precio_y_duracion():
    check_quote_terms(2_000_000, 120)
    for precio, duracion in [(None, 120), (2_000_000, None), (0, 120), (2_000_000, 0), (-1, 60)]:
        with pytest.raises(GuardFailedError):
            check_quote_terms(precio, duracion)
