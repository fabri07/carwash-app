"""Máquina de estados del turno — FASE-3-CONTRATO §2.1.

Pura: dice si una transición existe y a qué estado lleva. Las guardas que miran la base
(el `EXCLUDE` para la confirmación tardía, `SELECT … FOR UPDATE` y `from == status
actual`, C-18) son del servicio de aplicación.
"""

from datetime import datetime
from enum import StrEnum

from app.domain._transitions import Transition, freeze, next_state
from app.domain.delay import minutes_between, require_aware
from app.domain.enums import BookingStatus, CancellationClassification
from app.domain.exceptions import InvalidParameterError

_S = BookingStatus

#: Estados que ocupan el intervalo del puesto: el `WHERE` del `EXCLUDE` de §1.3.
BLOCKING_BOOKING_STATUSES: frozenset[BookingStatus] = frozenset(
    {_S.PENDIENTE_SENA, _S.PENDIENTE_COTIZACION, _S.CONFIRMADO, _S.RECIBIDO}
)

#: Desde donde el cliente o el negocio pueden cancelar. **[corregir] R-T-036**: ni desde
#: `VENCIDO` ni desde estados cerrados; el negocio tampoco desde `RECIBIDO`.
_ACTIVE: frozenset[BookingStatus | None] = frozenset(
    {_S.PENDIENTE_SENA, _S.PENDIENTE_COTIZACION, _S.CONFIRMADO}
)

#: Umbral default de cancelación tardía (720 min = 12 h). Es configuración de F4: acá
#: solo es el valor por defecto del parámetro.
DEFAULT_LATE_THRESHOLD_MIN = 720


class BookingAction(StrEnum):
    """Disparadores de §2.1, uno por fila de la tabla."""

    CREATE_WITH_DEPOSIT = "CREATE_WITH_DEPOSIT"  # alta con seña requerida > 0
    CREATE_FOR_QUOTE = "CREATE_FOR_QUOTE"  # alta de un servicio A_COTIZAR
    CREATE_CONFIRMED = "CREATE_CONFIRMED"  # alta sin seña, o del panel con seña ya cobrada
    RECORD_DEPOSIT = "RECORD_DEPOSIT"  # se registra una SEÑA
    ACCEPT_QUOTE_CONFIRMED = "ACCEPT_QUOTE_CONFIRMED"  # cotización aceptada, sin seña
    ACCEPT_QUOTE_WITH_DEPOSIT = "ACCEPT_QUOTE_WITH_DEPOSIT"  # cotización aceptada, con seña
    EXPIRE_HOLD = "EXPIRE_HOLD"  # vence el hold
    LATE_CONFIRM = "LATE_CONFIRM"  # confirmación tardía de un VENCIDO
    CANCEL_BY_CLIENT = "CANCEL_BY_CLIENT"  # anticipación ≥ 0
    CANCEL_BY_CLIENT_AFTER_START = "CANCEL_BY_CLIENT_AFTER_START"  # anticipación < 0
    CANCEL_OPERATIONAL = "CANCEL_OPERATIONAL"  # el negocio cancela (un solo caso de uso, C-10)
    MARK_NO_SHOW = "MARK_NO_SHOW"  # el operador marca la inasistencia (C-04)
    RECEIVE = "RECEIVE"  # llega el auto: el job emite JOB_RECEIVED
    JOB_FINISHED = "JOB_FINISHED"  # el job pasa a FINALIZADO (misma transacción)
    JOB_CANCELLED_DELAY = "JOB_CANCELLED_DELAY"  # el job pasa a CANCELADO_DEMORA


_A = BookingAction

BOOKING_TRANSITIONS = freeze(
    {
        _A.CREATE_WITH_DEPOSIT: Transition(frozenset({None}), _S.PENDIENTE_SENA),
        _A.CREATE_FOR_QUOTE: Transition(frozenset({None}), _S.PENDIENTE_COTIZACION),
        _A.CREATE_CONFIRMED: Transition(frozenset({None}), _S.CONFIRMADO),
        _A.RECORD_DEPOSIT: Transition(frozenset({_S.PENDIENTE_SENA}), _S.CONFIRMADO),
        _A.ACCEPT_QUOTE_CONFIRMED: Transition(frozenset({_S.PENDIENTE_COTIZACION}), _S.CONFIRMADO),
        _A.ACCEPT_QUOTE_WITH_DEPOSIT: Transition(
            frozenset({_S.PENDIENTE_COTIZACION}), _S.PENDIENTE_SENA
        ),
        _A.EXPIRE_HOLD: Transition(
            frozenset({_S.PENDIENTE_SENA, _S.PENDIENTE_COTIZACION}), _S.VENCIDO
        ),
        _A.LATE_CONFIRM: Transition(frozenset({_S.VENCIDO}), _S.CONFIRMADO),
        _A.CANCEL_BY_CLIENT: Transition(_ACTIVE, _S.CANCELADO_CLIENTE),
        _A.CANCEL_BY_CLIENT_AFTER_START: Transition(_ACTIVE, _S.AUSENTE_CON_AVISO_POSTERIOR),
        _A.CANCEL_OPERATIONAL: Transition(_ACTIVE, _S.CANCELADO_OPERATIVO),
        _A.MARK_NO_SHOW: Transition(frozenset({_S.CONFIRMADO}), _S.NO_ASISTIO),
        # [abierto] recibir con la seña sin pagar: la tabla lo permite, la guarda es de F6.
        _A.RECEIVE: Transition(frozenset({_S.CONFIRMADO, _S.PENDIENTE_SENA}), _S.RECIBIDO),
        _A.JOB_FINISHED: Transition(frozenset({_S.RECIBIDO}), _S.ATENDIDO),
        _A.JOB_CANCELLED_DELAY: Transition(frozenset({_S.RECIBIDO}), _S.CANCELADO_DEMORA),
    }
)


def next_booking_status(current: BookingStatus | None, action: BookingAction) -> BookingStatus:
    """Estado al que lleva `action` desde `current` (`None` = alta), o `InvalidTransition`."""
    return next_state("booking", BOOKING_TRANSITIONS, current, action)


def can_client_cancel(status: BookingStatus) -> bool:
    return status in BOOKING_TRANSITIONS[_A.CANCEL_BY_CLIENT].sources


def hold_expired(hold_expires_at: datetime | None, now: datetime) -> bool:
    """`hold_expires_at <= now`: un solo comparador en todo el sistema (**[corregir]** C-16).

    Sin hold (`None`) no hay nada que vencer.
    """
    require_aware(now)
    return hold_expires_at is not None and require_aware(hold_expires_at) <= now


def anticipation_min(start_at: datetime, requested_at: datetime) -> int:
    """`floor((start − requested) / 1 min)`: negativa si se pidió después del inicio."""
    return minutes_between(requested_at, start_at)


def classify_cancellation(
    anticipation_min: int, late_threshold_min: int = DEFAULT_LATE_THRESHOLD_MIN
) -> CancellationClassification:
    """Clasifica una cancelación **del cliente** (R-T-037, C-06).

    `≥ umbral` → `NORMAL`; `0 ≤ x < umbral` → `TARDIA`; `< 0` → `POSTERIOR_AL_TURNO`.
    `OPERATIVA` no sale de acá: es la del negocio, que no depende de la anticipación.
    """
    if late_threshold_min < 0:
        raise InvalidParameterError(f"late threshold must be >= 0: {late_threshold_min}")
    if anticipation_min >= late_threshold_min:
        return CancellationClassification.NORMAL
    if anticipation_min >= 0:
        return CancellationClassification.TARDIA
    return CancellationClassification.POSTERIOR_AL_TURNO


def client_cancellation_result(anticipation_min: int) -> BookingStatus:
    """Estado final del turno que cancela el cliente: el que alcanza su acción en la tabla.

    Anticipación `≥ 0` → `CANCELADO_CLIENTE` (`CANCEL_BY_CLIENT`); `< 0` →
    `AUSENTE_CON_AVISO_POSTERIOR` (`CANCEL_BY_CLIENT_AFTER_START`).
    """
    if anticipation_min >= 0:
        return _S.CANCELADO_CLIENTE
    return _S.AUSENTE_CON_AVISO_POSTERIOR
