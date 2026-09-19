"""Minutos entre instantes — FASE-3-CONTRATO §1.4 (`jobs.arrival_delay_min`,
`cancellations.anticipation_min`).

**[corregir] R-O-013.** `calculateDelay_` redondeaba y truncaba en 0 por abajo
(`Math.max(0, Math.round(…))`): llegar 40 minutos antes quedaba igual que llegar
puntual. Acá la demora lleva **signo** y es `floor` de minutos.
"""

from datetime import datetime, timedelta

from app.domain.exceptions import InvalidDatetimeError

_MINUTE = timedelta(minutes=1)


def require_aware(value: datetime) -> datetime:
    """Exige zona horaria: comparar un instante con una hora de pared es un bug silencioso."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidDatetimeError("datetime must be timezone-aware")
    return value


def minutes_between(start: datetime, end: datetime) -> int:
    """`floor((end − start) / 1 min)`, con signo (floor hacia −∞: −1 s son −1 min)."""
    return (require_aware(end) - require_aware(start)) // _MINUTE


def arrival_delay_min(scheduled_at: datetime, arrived_at: datetime) -> int:
    """Demora de llegada con signo: positiva si llegó tarde, negativa si llegó antes."""
    return minutes_between(scheduled_at, arrived_at)
