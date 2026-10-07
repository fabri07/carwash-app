"""Seña, total pactado y saldo derivado — FASE-3-CONTRATO §2.4 (D-005), X3 y X9.

Nada de esto se guarda como estado: el saldo y lo pagado se **derivan** de `payments`
cada vez (X9). El legacy guardaba dos columnas de estado que se desincronizaban
(R-O-007) y un segundo cobro pisaba al primero (R-O-026).
"""

from collections.abc import Iterable
from typing import NamedTuple

from app.domain.enums import CancellationClassification, DepositStatus, PaymentKind
from app.domain.exceptions import GuardFailedError, InvalidAmountError, InvalidTransition
from app.domain.job_state import require_reason
from app.domain.money import apply_bps

_C = CancellationClassification
_D = DepositStatus

# Qué caso abre la seña cobrada según la clasificación. `NORMAL` y `TARDIA` tienen el
# mismo efecto económico (C-14): una retención por tardía sería regla nueva, [abierto].
_OPEN_CASE_FOR: dict[CancellationClassification, DepositStatus] = {
    _C.NORMAL: _D.DEVOLUCION_PENDIENTE,
    _C.TARDIA: _D.DEVOLUCION_PENDIENTE,
    _C.POSTERIOR_AL_TURNO: _D.EN_REVISION,
    _C.OPERATIVA: _D.REPROGRAMACION_O_DEVOLUCION_PENDIENTE,
}

#: Casos abiertos que se resuelven. `SIN_PAGO` es abierto pero terminal de hecho: no
#: hay seña que resolver.
DEPOSIT_OPEN_CASE_STATUSES: frozenset[DepositStatus] = frozenset(_OPEN_CASE_FOR.values())

#: Cierres **[diseñar]** (C-02: cuatro caminos abrían un caso y ninguno lo cerraba).
DEPOSIT_CLOSED_STATUSES: frozenset[DepositStatus] = frozenset(
    {_D.DEVUELTA, _D.RETENIDA, _D.REPROGRAMADA}
)


def deposit_status_for(
    classification: CancellationClassification, paid_cents: int
) -> DepositStatus:
    """Estado inicial de la seña al cancelar: `SIN_PAGO` si no se cobró nada."""
    if paid_cents < 0:
        raise InvalidAmountError(f"paid deposit must be >= 0: {paid_cents}")
    if paid_cents == 0:
        return _D.SIN_PAGO
    return _OPEN_CASE_FOR[classification]


#: Qué cierre admite cada caso abierto. `RETENIDA` solo desde `EN_REVISION` (el cliente
#: canceló después de la hora del turno y decide una persona): retener en una
#: cancelación a tiempo o tardía sería la regla nueva que C-14 deja **[abierto]**, y
#: retener cuando canceló el negocio no tiene sentido. Devolver o reprogramar siempre
#: se puede: no perjudica al cliente.
DEPOSIT_RESOLUTIONS: dict[DepositStatus, frozenset[DepositStatus]] = {
    _D.DEVOLUCION_PENDIENTE: frozenset({_D.DEVUELTA, _D.REPROGRAMADA}),
    _D.EN_REVISION: frozenset({_D.DEVUELTA, _D.RETENIDA, _D.REPROGRAMADA}),
    _D.REPROGRAMACION_O_DEVOLUCION_PENDIENTE: frozenset({_D.DEVUELTA, _D.REPROGRAMADA}),
}


def resolve_deposit(current: DepositStatus, target: DepositStatus) -> DepositStatus:
    """Cierra un caso abierto según `DEPOSIT_RESOLUTIONS`.

    `SIN_PAGO` no se resuelve y un cierre no se reabre. Las guardas (actor, motivo, pago
    de devolución) están en `check_resolution_requirements` y en los `CHECK` de la base.
    """
    if target not in DEPOSIT_RESOLUTIONS.get(current, frozenset()):
        raise InvalidTransition("deposit", current, target)
    return target


def check_resolution_requirements(
    target: DepositStatus, reason: str | None, has_refund_payment: bool
) -> str:
    """Guardas del cierre: motivo obligatorio; `DEVUELTA` exige el pago `DEVOLUCION`.

    Devuelve el motivo limpio. El actor lo exige el servicio (viene de la sesión).
    """
    if target not in DEPOSIT_CLOSED_STATUSES:
        raise InvalidTransition("deposit", None, target)
    cleaned = require_reason(reason)
    if target == _D.DEVUELTA and not has_refund_payment:
        raise GuardFailedError("a refunded deposit needs its refund payment")
    return cleaned


def deposit_required(price_cents: int | None, deposit_bps: int) -> int:
    """Seña requerida: `round_half_up(price × bps / 10000)` (R-T-003). No se guarda.

    Sin precio (servicio a cotizar) no hay seña (**[corregir]** R-C-032).
    """
    if price_cents is None:
        return 0
    if price_cents <= 0:
        raise InvalidAmountError(f"price must be > 0: {price_cents}")
    return apply_bps(price_cents, deposit_bps)


def total_agreed(base_cents: int, surcharge_cents: int, discount_cents: int) -> int:
    """Total pactado del job: base + recargo − descuento, estrictamente positivo (R-O-003)."""
    if base_cents <= 0:
        raise InvalidAmountError(f"base price must be > 0: {base_cents}")
    if surcharge_cents < 0 or discount_cents < 0:
        raise InvalidAmountError("surcharge and discount must be >= 0")
    total = base_cents + surcharge_cents - discount_cents
    if total <= 0:
        raise InvalidAmountError(f"agreed total must be > 0: {total}")
    return total


class PaymentLine(NamedTuple):
    """Lo mínimo de un pago para derivar el saldo.

    Es una tupla: `(kind, amount_cents, voided)` sirve igual. El servicio la arma desde
    la fila de `payments` con `voided = voided_at is not None`.
    """

    kind: PaymentKind
    amount_cents: int
    voided: bool = False


def net_paid(payments: Iterable[tuple[PaymentKind, int, bool]]) -> int:
    """Seña + saldo − devoluciones, sin los anulados. Montos siempre `> 0` (el signo es `kind`)."""
    total = 0
    for kind, amount_cents, voided in payments:
        if amount_cents <= 0:
            raise InvalidAmountError(f"payment amount must be > 0: {amount_cents}")
        if voided:
            continue
        total += -amount_cents if kind == PaymentKind.DEVOLUCION else amount_cents
    return total


def balance_due(total_agreed_cents: int, payments: Iterable[tuple[PaymentKind, int, bool]]) -> int:
    """Saldo derivado: total pactado − lo pagado neto. Negativo = saldo a favor del cliente."""
    if total_agreed_cents <= 0:
        raise InvalidAmountError(f"agreed total must be > 0: {total_agreed_cents}")
    return total_agreed_cents - net_paid(payments)
