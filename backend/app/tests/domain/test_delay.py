"""`delay.py` — FASE-3-CONTRATO §1.4 (`arrival_delay_min`), R-O-013 corregido."""

from datetime import UTC, datetime, timedelta, timezone, tzinfo

import pytest

from app.domain.delay import arrival_delay_min, minutes_between
from app.domain.exceptions import InvalidDatetimeError

TURNO = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("llegada", "demora"),
    [
        (TURNO, 0),
        (TURNO + timedelta(minutes=20), 20),
        (TURNO + timedelta(minutes=20, seconds=59), 20),  # floor, no round (R-O-013 redondeaba)
        (TURNO + timedelta(seconds=59), 0),
        # Con signo: llegar antes se registra (el legacy colapsaba a 0).
        (TURNO - timedelta(minutes=40), -40),
        (TURNO - timedelta(seconds=1), -1),  # floor hacia -inf
        (TURNO - timedelta(minutes=5, seconds=30), -6),
    ],
)
def test_arrival_delay_min(llegada, demora):
    assert arrival_delay_min(TURNO, llegada) == demora


def test_zonas_horarias_distintas_se_comparan_por_instante():
    cordoba = timezone(timedelta(hours=-3))
    llegada = datetime(2026, 9, 18, 7, 15, tzinfo=cordoba)  # 10:15 UTC
    assert arrival_delay_min(TURNO, llegada) == 15


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (datetime(2026, 9, 18, 10, 0), TURNO),
        (TURNO, datetime(2026, 9, 18, 10, 0)),
    ],
)
def test_exige_datetimes_aware(a, b):
    with pytest.raises(InvalidDatetimeError):
        arrival_delay_min(a, b)
    with pytest.raises(InvalidDatetimeError):
        minutes_between(a, b)


def test_minutes_between_es_fin_menos_inicio():
    assert minutes_between(TURNO, TURNO + timedelta(hours=12)) == 720


class _ZonaSinOffset(tzinfo):
    def utcoffset(self, dt):
        return None


def test_tzinfo_sin_offset_cuenta_como_naive():
    with pytest.raises(InvalidDatetimeError):
        minutes_between(datetime(2026, 9, 18, 10, 0, tzinfo=_ZonaSinOffset()), TURNO)
