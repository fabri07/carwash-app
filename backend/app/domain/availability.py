"""Motor de disponibilidad — FASE-5-CONTRATO §5 (PR 5.1).

Dominio puro (Z4): recibe los datos ya leídos y `now`, devuelve los inicios libres del día con los
puestos que quedan libres en cada uno. Sin I/O y sin reloj propio: se testea con reloj fijo.

El motor decide qué se **ofrece**; el `EXCLUDE` de `bookings` decide qué **entra** (Z5). Por eso
lo que ocupa (`occupied`) lo arma el repositorio, no el motor: turnos vivos en `CONFIRMADO`,
`RECIBIDO` y `PENDIENTE_SEÑA` con el hold vigente (mismo comparador que `hold_expired`).

Las franjas y reglas son hora local del negocio (`tz`, Z9); los instantes son UTC con zona. La
grilla arranca en la apertura de cada franja y avanza de a `step_min` (D5-7, E16).
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

from app.domain.booking_state import anticipation_min
from app.domain.delay import require_aware
from app.domain.exceptions import InvalidParameterError


class BusinessHour(NamedTuple):
    """Franja de atención: `[opens_at, closes_at)` hora local, día ISO (1 = lunes)."""

    weekday: int
    opens_at: time
    closes_at: time


class RuleRow(NamedTuple):
    """Fila de una regla horaria de servicio: puede **arrancar** en `[window_start, last_start]`."""

    weekday: int
    window_start: time
    last_start: time


class Span(NamedTuple):
    """Bloqueo o apertura: `[starts_at, ends_at)`; `resource_id=None` = todos los puestos."""

    resource_id: uuid.UUID | None
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class FreeStart:
    """Un inicio ofrecible y los puestos libres en él, en `sort_order` (D5-6: el primero gana)."""

    start_at: datetime
    resource_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class _Window:
    """Franja del día ya en UTC. `resources=None`: todos los puestos (franja habitual o apertura
    global); si no, solo esos. `opening`: las reglas del servicio solo miran el día (§3.3)."""

    start: datetime
    end: datetime
    resources: frozenset[uuid.UUID] | None
    opening: bool


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """`[a) && [b)` semiabierto, como el `tstzrange(…, '[)')` del `EXCLUDE`."""
    return a_start < b_end and b_start < a_end


def _local_midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC)


def _windows(
    day: date, tz: ZoneInfo, business_hours: Iterable[BusinessHour], openings: Iterable[Span]
) -> list[_Window]:
    weekday = day.isoweekday()
    windows = [
        _Window(
            start=datetime.combine(day, h.opens_at, tzinfo=tz).astimezone(UTC),
            end=datetime.combine(day, h.closes_at, tzinfo=tz).astimezone(UTC),
            resources=None,
            opening=False,
        )
        for h in business_hours
        if h.weekday == weekday and h.closes_at > h.opens_at
    ]
    day_start = _local_midnight(day, tz)
    day_end = _local_midnight(day + timedelta(days=1), tz)
    for opening in openings:
        start = max(require_aware(opening.starts_at).astimezone(UTC), day_start)
        end = min(require_aware(opening.ends_at).astimezone(UTC), day_end)
        if start >= end:
            continue
        only = None if opening.resource_id is None else frozenset({opening.resource_id})
        windows.append(_Window(start=start, end=end, resources=only, opening=True))
    return windows


def _meets_rules(
    start: datetime, tz: ZoneInfo, rules: Sequence[Sequence[RuleRow]], *, opening: bool
) -> bool:
    """Todas las reglas no vacías (intersección, E7). En una apertura especial solo el día."""
    local = start.astimezone(tz)
    weekday, clock = local.isoweekday(), local.time().replace(tzinfo=None)
    for rule in rules:
        if not rule:
            continue
        rows = [r for r in rule if r.weekday == weekday]
        if not rows:
            return False
        if not opening and not any(r.window_start <= clock <= r.last_start for r in rows):
            return False
    return True


def _check(
    *, duration_min: int, min_advance_min: int, horizon_days: int, step_min: int, now: datetime
) -> None:
    require_aware(now)
    if duration_min <= 0:
        raise InvalidParameterError(f"duration must be > 0: {duration_min}")
    if step_min <= 0:
        raise InvalidParameterError(f"step must be > 0: {step_min}")
    if horizon_days < 0:
        raise InvalidParameterError(f"horizon must be >= 0: {horizon_days}")
    if min_advance_min < 0:
        raise InvalidParameterError(f"minimum advance must be >= 0: {min_advance_min}")


def free_starts(
    *,
    day: date,
    tz: ZoneInfo,
    now: datetime,
    business_hours: Sequence[BusinessHour],
    rules: Sequence[Sequence[RuleRow]],
    openings: Sequence[Span],
    blocks: Sequence[Span],
    occupied: Mapping[uuid.UUID, Sequence[tuple[datetime, datetime]]],
    resources: Sequence[uuid.UUID],
    duration_min: int,
    min_advance_min: int,
    horizon_days: int,
    step_min: int,
) -> list[FreeStart]:
    """Los inicios libres de `day` con sus puestos libres, ordenados (§5, pasos 1 a 7).

    `rules`: una lista por servicio pedido; vacía = el servicio no tiene regla. `occupied`: por
    puesto, los intervalos que ya ocupan (el repositorio filtró los holds vencidos). `resources`:
    los puestos vivos en `sort_order`.
    """
    _check(
        duration_min=duration_min,
        min_advance_min=min_advance_min,
        horizon_days=horizon_days,
        step_min=step_min,
        now=now,
    )
    today = now.astimezone(tz).date()
    if day < today or day > today + timedelta(days=horizon_days):
        return []

    duration = timedelta(minutes=duration_min)
    step = timedelta(minutes=step_min)
    global_blocks = [b for b in blocks if b.resource_id is None]
    free: dict[datetime, set[uuid.UUID]] = {}
    for window in _windows(day, tz, business_hours, openings):
        start = window.start
        while start + duration <= window.end:
            end = start + duration
            if (
                anticipation_min(start, now) >= min_advance_min
                and _meets_rules(start, tz, rules, opening=window.opening)
                and not any(_overlaps(start, end, b.starts_at, b.ends_at) for b in global_blocks)
            ):
                for resource in resources:
                    if window.resources is not None and resource not in window.resources:
                        continue
                    if _resource_free(resource, start, end, blocks, occupied):
                        free.setdefault(start, set()).add(resource)
            start += step
    order = {resource: i for i, resource in enumerate(resources)}
    return [
        FreeStart(start, tuple(sorted(ids, key=order.__getitem__)))
        for start, ids in sorted(free.items())
        if ids
    ]


def _resource_free(
    resource: uuid.UUID,
    start: datetime,
    end: datetime,
    blocks: Sequence[Span],
    occupied: Mapping[uuid.UUID, Sequence[tuple[datetime, datetime]]],
) -> bool:
    if any(
        b.resource_id == resource and _overlaps(start, end, b.starts_at, b.ends_at) for b in blocks
    ):
        return False
    return not any(_overlaps(start, end, s, e) for s, e in occupied.get(resource, ()))


def fits_schedule(
    *,
    start_at: datetime,
    duration_min: int,
    resource_id: uuid.UUID,
    tz: ZoneInfo,
    business_hours: Sequence[BusinessHour],
    rules: Sequence[Sequence[RuleRow]],
    openings: Sequence[Span],
) -> bool:
    """¿Un turno `[start_at, start_at + duración)` en ese puesto cae en el horario y las reglas?

    Lo usa la bandeja al reprogramar (D5-10, PR 5.3): el dueño **puede** salirse del horario y de
    las reglas del servicio, y la respuesta lo marca (`outside_rules`) para que la pantalla avise.
    Mismas reglas que `free_starts` (pasos 2 y 4), sin la grilla (`step_min`: el dueño elige
    cualquier minuto), sin anticipación ni horizonte y sin mirar ocupación ni bloqueos (de eso se
    encargan el `EXCLUDE` y la guarda de bloqueos del servicio).
    """
    start = require_aware(start_at).astimezone(UTC)
    if duration_min <= 0:
        raise InvalidParameterError(f"duration must be > 0: {duration_min}")
    end = start + timedelta(minutes=duration_min)
    day = start.astimezone(tz).date()
    for window in _windows(day, tz, business_hours, openings):
        if window.resources is not None and resource_id not in window.resources:
            continue
        if (
            window.start <= start
            and end <= window.end
            and _meets_rules(start, tz, rules, opening=window.opening)
        ):
            return True
    return False
