"""Máquina de estados del job y sus eventos — FASE-3-CONTRATO §2.2.

`job_events` es la fuente de verdad y `jobs.status` la caché de su último `to_status`.
Hay dos clases de evento:

- **con transición** (`JOB_*`): cambian el estado; `from_status`/`to_status` NN.
- **sin transición** (seña, cobros, inspección, ajuste): el estado no cambia y el evento
  se guarda con `from_status`/`to_status` NULL, pero **igual** tienen estados desde los
  que son válidos (un cobro no entra en un job `CANCELADO_DEMORA`).

**[corregir]**, no se reproduce: cancelar por demora un `FINALIZADO` cobrado (R-O-016,
C-7), finalizar sin iniciar o refinalizar pisando el cobro (R-O-022), elegir el estado
inicial a mano (R-O-001).
"""

from collections.abc import Iterable, Mapping
from datetime import datetime
from types import MappingProxyType

from app.domain._transitions import Transition, freeze, next_state
from app.domain.delay import arrival_delay_min
from app.domain.enums import JobEventType, JobStatus
from app.domain.exceptions import GuardFailedError, InvalidParameterError, InvalidTransition

_J = JobStatus
_E = JobEventType

JOB_TRANSITIONS = freeze(
    {
        # Estado inicial **siempre** PRESENTE; la idempotencia (turno con job previo) es
        # del servicio.
        _E.JOB_RECEIVED: Transition(frozenset({None}), _J.PRESENTE),
        # No exige inspección (D-007).
        _E.JOB_STARTED: Transition(frozenset({_J.PRESENTE}), _J.EN_PROCESO),
        _E.JOB_FINISHED: Transition(frozenset({_J.EN_PROCESO}), _J.FINALIZADO),
        # Guarda (saldo derivado == 0) en el servicio: el saldo sale de `payments`.
        _E.JOB_SETTLED: Transition(frozenset({_J.FINALIZADO}), _J.COBRADO),
        # No obligatorio: quedarse en COBRADO es "terminado sin retirar" (D-003).
        _E.JOB_PICKED_UP: Transition(frozenset({_J.COBRADO}), _J.RETIRADO),
        # Guardas en `check_delay_cancellation`.
        _E.JOB_CANCELLED_DELAY: Transition(frozenset({_J.PRESENTE}), _J.CANCELADO_DEMORA),
    }
)

#: Eventos que cambian el estado del job.
TRANSITION_EVENTS: frozenset[JobEventType] = frozenset(JOB_TRANSITIONS)

_ALL: frozenset[JobStatus] = frozenset(JobStatus)

#: Eventos sin transición → estados desde los que son válidos.
NON_TRANSITION_EVENTS: Mapping[JobEventType, frozenset[JobStatus]] = MappingProxyType(
    {
        # "Solo junto a JOB_CANCELLED_DELAY": se emite con el job ya cancelado.
        _E.DEPOSIT_RETAINED: frozenset({_J.CANCELADO_DEMORA}),
        # Solo un job cancelado por demora tiene retención; que esté vigente lo dice
        # `retention_is_active`, y el motivo `require_reason`.
        _E.DEPOSIT_RETENTION_REVERSED: frozenset({_J.CANCELADO_DEMORA}),
        # "Job no CANCELADO_DEMORA".
        _E.PAYMENT_RECORDED: _ALL - {_J.CANCELADO_DEMORA},
        _E.PAYMENT_VOIDED: _ALL - {_J.CANCELADO_DEMORA},
        # Sin guarda de estado: la inspección es informativa y opcional (D-007).
        _E.INSPECTION_RECORDED: _ALL,
        # El contrato no fija estados: interpretación conservadora, solo antes del cobro.
        # Ajustar el precio de un COBRADO/RETIRADO dejaría la caché `status` mintiendo
        # sobre el saldo, y un CANCELADO_DEMORA no tiene precio que cobrar.
        _E.PRICE_ADJUSTED: frozenset({_J.PRESENTE, _J.EN_PROCESO, _J.FINALIZADO}),
    }
)


def apply_event(current: JobStatus | None, event: JobEventType) -> JobStatus | None:
    """Valida `event` contra el estado `current` (`None` = el job todavía no existe).

    Devuelve el estado nuevo si el evento tiene transición, o `None` si es válido pero
    no cambia el estado (el `job_events` lleva `from_status`/`to_status` NULL). Levanta
    `InvalidTransition` si la combinación no está en las tablas.
    """
    if event in TRANSITION_EVENTS:
        return next_state("job", JOB_TRANSITIONS, current, event)
    if current is None or current not in NON_TRANSITION_EVENTS[event]:
        raise InvalidTransition("job", current, event)
    return None


def check_delay_cancellation(
    scheduled_at: datetime | None, occurred_at: datetime, tolerance_min: int
) -> int:
    """Guardas de `JOB_CANCELLED_DELAY` (D-001.2). Devuelve la demora aplicada.

    - Un walk-in (`scheduled_at` NULL) no se cancela por demora.
    - La demora se **recalcula** con el `occurred_at` del evento y tiene que ser
      **mayor** que la tolerancia: igual no alcanza.

    `tolerance_min` es configuración del negocio (default 20 en F4); acá, parámetro.
    """
    if tolerance_min < 0:
        raise InvalidParameterError(f"tolerance must be >= 0: {tolerance_min}")
    if scheduled_at is None:
        raise GuardFailedError("a walk-in job cannot be cancelled for delay")
    delay = arrival_delay_min(scheduled_at, occurred_at)
    if delay <= tolerance_min:
        raise GuardFailedError(f"delay {delay} min does not exceed tolerance {tolerance_min} min")
    return delay


def retention_is_active(events: Iterable[JobEventType]) -> bool:
    """¿Hay un `DEPOSIT_RETAINED` sin `DEPOSIT_RETENTION_REVERSED` posterior? (X9, C-12).

    `events` va en el orden de la fuente de verdad (el que fije el repositorio sobre
    `job_events`); los demás tipos de evento se ignoran.
    """
    active = False
    for event in events:
        if event == _E.DEPOSIT_RETAINED:
            active = True
        elif event == _E.DEPOSIT_RETENTION_REVERSED:
            active = False
    return active


def require_reason(reason: str | None) -> str:
    """Motivo obligatorio (D-001.4): devuelve el texto sin espacios de borde o levanta."""
    cleaned = (reason or "").strip()
    if not cleaned:
        raise GuardFailedError("a non-empty reason is required")
    return cleaned
