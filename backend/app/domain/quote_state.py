"""Máquina de estados de la cotización — FASE-3-CONTRATO §2.3.

**[diseñar]**, a validar con el dueño: en el legacy la cotización es insert-only y nunca
se vuelve a leer (R-C-027). `VENCIDO` es nuevo; el vencimiento usa el mismo comparador
que el hold del turno (`booking_state.hold_expired`).
"""

from enum import StrEnum

from app.domain._transitions import Transition, freeze, next_state
from app.domain.enums import QuoteStatus
from app.domain.exceptions import GuardFailedError

_Q = QuoteStatus

#: Cierres: no salen a ningún lado.
TERMINAL_QUOTE_STATUSES: frozenset[QuoteStatus] = frozenset(
    {_Q.ACEPTADO, _Q.RECHAZADO, _Q.CANCELADO, _Q.VENCIDO}
)

_OPEN: frozenset[QuoteStatus | None] = frozenset({_Q.PENDIENTE, _Q.COTIZADO})


class QuoteAction(StrEnum):
    CREATE = "CREATE"  # nace PENDIENTE (alta de un turno A_COTIZAR o sin turno)
    QUOTE = "QUOTE"  # se cotiza: exige precio y duración (`check_quote_terms`)
    ACCEPT = "ACCEPT"  # precio y duración pasan al turno y al job (R-C-029)
    REJECT = "REJECT"
    CANCEL = "CANCEL"
    EXPIRE = "EXPIRE"  # por `expires_at`


_A = QuoteAction

QUOTE_TRANSITIONS = freeze(
    {
        _A.CREATE: Transition(frozenset({None}), _Q.PENDIENTE),
        _A.QUOTE: Transition(frozenset({_Q.PENDIENTE}), _Q.COTIZADO),
        _A.ACCEPT: Transition(frozenset({_Q.COTIZADO}), _Q.ACEPTADO),
        _A.REJECT: Transition(frozenset({_Q.COTIZADO}), _Q.RECHAZADO),
        _A.CANCEL: Transition(_OPEN, _Q.CANCELADO),
        _A.EXPIRE: Transition(_OPEN, _Q.VENCIDO),
    }
)


def next_quote_status(current: QuoteStatus | None, action: QuoteAction) -> QuoteStatus:
    """Estado al que lleva `action` desde `current` (`None` = alta), o `InvalidTransition`."""
    return next_state("quote", QUOTE_TRANSITIONS, current, action)


def check_quote_terms(agreed_price_cents: int | None, agreed_duration_min: int | None) -> None:
    """Guarda de `QUOTE`: precio y duración presentes y positivos.

    **[corregir]** R-C-024: el legacy usaba `0` como "sin cotizar".
    """
    if agreed_price_cents is None or agreed_price_cents <= 0:
        raise GuardFailedError("a quote needs a positive agreed price")
    if agreed_duration_min is None or agreed_duration_min <= 0:
        raise GuardFailedError("a quote needs a positive agreed duration")
