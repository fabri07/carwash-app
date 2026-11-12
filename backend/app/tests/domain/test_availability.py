"""Motor de disponibilidad — FASE-5-CONTRATO §5, casos borde E1–E16 (criterio B3).

Reloj fijo: `now` se pasa explícito (Z4), sin freezegun. La zona es la del negocio de referencia
(`America/Argentina/Cordoba`, UTC−3): las franjas son hora local y los instantes UTC con zona. Los
servicios, puestos y horarios son sintéticos.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.domain.availability import BusinessHour, FreeStart, RuleRow, Span, free_starts
from app.domain.exceptions import InvalidDatetimeError, InvalidParameterError

TZ = ZoneInfo("America/Argentina/Cordoba")
MARTES = date(2026, 11, 17)
VIERNES = date(2026, 11, 20)
DOMINGO = date(2026, 11, 22)
#: Lunes 16/11/2026, 08:00 en el lavadero: el "ahora" de casi todos los casos.
AHORA = datetime(2026, 11, 16, 8, 0, tzinfo=TZ)

P1 = uuid.UUID(int=1)
P2 = uuid.UUID(int=2)


def hora(h: int, m: int = 0, dia: date = MARTES, tz: ZoneInfo = TZ) -> datetime:
    """Instante local del lavadero, como lo devuelve el motor (UTC)."""
    return datetime.combine(dia, time(h, m), tzinfo=tz).astimezone(UTC)


def franjas(*rangos: tuple[int, int, int]) -> list[BusinessHour]:
    """`(día ISO, abre, cierra)` en horas enteras."""
    return [BusinessHour(w, time(a), time(c)) for w, a, c in rangos]


def correr(**pisar: Any) -> list[FreeStart]:
    datos: dict[str, Any] = {
        "day": MARTES,
        "tz": TZ,
        "now": AHORA,
        "business_hours": franjas((2, 9, 13)),
        "rules": [[]],
        "openings": [],
        "blocks": [],
        "occupied": {},
        "resources": [P1],
        "duration_min": 60,
        "min_advance_min": 5,
        "horizon_days": 14,
        "step_min": 30,
    }
    return free_starts(**{**datos, **pisar})


def inicios(resultado: list[FreeStart]) -> list[datetime]:
    return [f.start_at for f in resultado]


def grilla(desde: datetime, hasta: datetime, paso: int = 30) -> list[datetime]:
    """Inicios de `desde` a `hasta` inclusive, cada `paso` minutos."""
    salida, t = [], desde
    while t <= hasta:
        salida.append(t)
        t += timedelta(minutes=paso)
    return salida


# ── E1–E16 ────────────────────────────────────────────────────────────────────


def test_e1_un_turno_de_120_en_franja_9_13_arranca_a_mas_tardar_11():
    resultado = inicios(correr(duration_min=120))
    assert resultado == grilla(hora(9), hora(11))
    assert resultado[-1] == hora(11)


def test_e2_no_cruza_el_corte_del_mediodia():
    resultado = correr(business_hours=franjas((2, 9, 13), (2, 14, 18)), duration_min=240)
    assert inicios(resultado) == [hora(9), hora(14)]


def test_e3_semiabierto_un_puesto_ocupado_10_a_11():
    resultado = inicios(correr(occupied={P1: [(hora(10), hora(11))]}))
    assert hora(10) not in resultado
    assert hora(9, 30) not in resultado  # [9:30, 10:30) pisa el turno
    assert hora(9) in resultado and hora(11) in resultado  # tocarse en el borde no es pisarse


def test_e4_dos_puestos_uno_ocupado_ofrece_el_libre():
    resultado = correr(resources=[P1, P2], occupied={P1: [(hora(10), hora(11))]})
    por_inicio = {f.start_at: f.resource_ids for f in resultado}
    assert por_inicio[hora(10)] == (P2,)
    assert por_inicio[hora(9)] == (P1, P2)  # en sort_order


def test_e5_regla_viernes_sabado_un_martes_no_ofrece_nada():
    regla = [RuleRow(5, time(9), time(18)), RuleRow(6, time(9), time(18))]
    assert correr(rules=[regla]) == []


def test_e6_regla_de_manana_y_tarde():
    regla = [RuleRow(2, time(9), time(11)), RuleRow(2, time(14), time(16))]
    resultado = inicios(correr(business_hours=franjas((2, 9, 18)), rules=[regla]))
    assert resultado == grilla(hora(9), hora(11)) + grilla(hora(14), hora(16))
    assert hora(12) not in resultado


def test_e7_dos_servicios_con_reglas_distintas_se_intersectan():
    temprano = [RuleRow(2, time(9), time(11))]
    tarde = [RuleRow(2, time(10), time(12))]
    sin_regla: list[RuleRow] = []
    resultado = inicios(correr(rules=[temprano, tarde, sin_regla]))
    assert resultado == grilla(hora(10), hora(11))
    disjunta = [RuleRow(2, time(12), time(12, 30))]
    assert correr(rules=[temprano, disjunta]) == []


def test_e8_bloqueo_global_de_dia_completo():
    dia = Span(None, hora(0), hora(0, dia=MARTES + timedelta(days=1)))
    assert correr(resources=[P1, P2], blocks=[dia]) == []


def test_e9_bloqueo_de_un_puesto_ofrece_el_otro():
    bloqueo = Span(P1, hora(0), hora(0, dia=MARTES + timedelta(days=1)))
    resultado = correr(resources=[P1, P2], blocks=[bloqueo])
    assert resultado and all(f.resource_ids == (P2,) for f in resultado)


def test_e10_apertura_especial_un_domingo_sin_horario():
    apertura = Span(None, hora(10, dia=DOMINGO), hora(12, dia=DOMINGO))
    sin_apertura = correr(day=DOMINGO)
    assert sin_apertura == []
    resultado = inicios(correr(day=DOMINGO, openings=[apertura]))
    assert resultado == [hora(10, dia=DOMINGO), hora(10, 30, dia=DOMINGO), hora(11, dia=DOMINGO)]


def test_e10_apertura_de_un_puesto_solo_ofrece_ese_puesto():
    apertura = Span(P2, hora(10, dia=DOMINGO), hora(12, dia=DOMINGO))
    resultado = correr(day=DOMINGO, openings=[apertura], resources=[P1, P2])
    assert resultado and all(f.resource_ids == (P2,) for f in resultado)


def test_e10_en_una_apertura_la_regla_solo_mira_el_dia():
    """§3.3: dentro de una apertura especial la regla no recorta horas, solo exige el día."""
    apertura = Span(None, hora(18, dia=DOMINGO), hora(20, dia=DOMINGO))
    regla_domingo_temprano = [RuleRow(7, time(9), time(10))]
    regla_sabado = [RuleRow(6, time(9), time(18))]
    con_dia = correr(day=DOMINGO, openings=[apertura], rules=[regla_domingo_temprano])
    assert inicios(con_dia)[0] == hora(18, dia=DOMINGO)
    assert correr(day=DOMINGO, openings=[apertura], rules=[regla_sabado]) == []


def test_e11_un_bloqueo_gana_sobre_una_apertura():
    apertura = Span(None, hora(10, dia=DOMINGO), hora(12, dia=DOMINGO))
    bloqueo = Span(None, hora(10, dia=DOMINGO), hora(11, dia=DOMINGO))
    resultado = inicios(correr(day=DOMINGO, openings=[apertura], blocks=[bloqueo]))
    assert resultado == [hora(11, dia=DOMINGO)]


def test_e12_anticipacion_minima_con_el_borde_incluido():
    hoy = MARTES
    tres_minutos = correr(now=hora(9, 57, dia=hoy), min_advance_min=5)
    assert hora(10) not in inicios(tres_minutos)
    cinco_minutos = correr(now=hora(9, 55, dia=hoy), min_advance_min=5)
    assert inicios(cinco_minutos)[0] == hora(10)


def test_e13_el_horizonte_incluye_su_ultimo_dia():
    hoy = AHORA.date()
    ultimo = hoy + timedelta(days=14)
    regla_todos: list[list[RuleRow]] = [[]]
    hs = [BusinessHour(ultimo.isoweekday(), time(9), time(13))]
    assert correr(day=ultimo, business_hours=hs, rules=regla_todos) != []
    pasado = ultimo + timedelta(days=1)
    hs_pasado = [BusinessHour(pasado.isoweekday(), time(9), time(13))]
    assert correr(day=pasado, business_hours=hs_pasado) == []


def test_e13_un_dia_anterior_a_hoy_no_ofrece_nada():
    ayer = AHORA.date() - timedelta(days=1)
    hs = [BusinessHour(ayer.isoweekday(), time(9), time(13))]
    assert correr(day=ayer, business_hours=hs, now=AHORA) == []


def test_e14_un_hold_vencido_no_ocupa():
    """El repositorio no manda los holds vencidos en `occupied`: con el puesto vacío, se ofrece."""
    vigente = correr(occupied={P1: [(hora(10), hora(11))]})
    vencido = correr(occupied={P1: []})
    assert hora(10) not in inicios(vigente)
    assert hora(10) in inicios(vencido)


def test_e15_zona_distinta_de_utc_y_franja_hasta_las_23():
    resultado = inicios(correr(business_hours=franjas((2, 20, 23))))
    assert resultado == grilla(hora(20), hora(22))
    # 22:00 en Córdoba es 01:00 UTC del miércoles: sigue siendo un inicio del martes local.
    assert resultado[-1] == datetime(2026, 11, 18, 1, 0, tzinfo=UTC)
    assert all(r.astimezone(TZ).date() == MARTES for r in resultado)
    # Y el miércoles no hereda nada del martes.
    assert correr(day=MARTES + timedelta(days=1), business_hours=franjas((2, 20, 23))) == []


def test_e16_la_grilla_arranca_en_la_apertura_de_la_franja():
    hs = [BusinessHour(2, time(9, 15), time(12))]
    assert inicios(correr(business_hours=hs))[:3] == [hora(9, 15), hora(9, 45), hora(10, 15)]


# ── Bordes del motor ──────────────────────────────────────────────────────────


def test_una_apertura_que_cruza_la_medianoche_se_recorta_al_dia():
    apertura = Span(None, hora(22, dia=DOMINGO - timedelta(days=1)), hora(2, dia=DOMINGO))
    resultado = inicios(correr(day=DOMINGO, openings=[apertura]))
    assert resultado == [hora(0, dia=DOMINGO), hora(0, 30, dia=DOMINGO), hora(1, dia=DOMINGO)]


def test_un_inicio_repetido_por_franja_y_apertura_sale_una_vez():
    apertura = Span(None, hora(9), hora(11))
    resultado = correr(openings=[apertura], resources=[P1, P2])
    assert len(inicios(resultado)) == len(set(inicios(resultado)))
    assert {f.start_at: f.resource_ids for f in resultado}[hora(9)] == (P1, P2)


def test_sin_puestos_no_hay_inicios():
    assert correr(resources=[]) == []


@pytest.mark.parametrize(
    "pisar",
    [
        {"duration_min": 0},
        {"step_min": 0},
        {"horizon_days": -1},
        {"min_advance_min": -1},
    ],
)
def test_parametros_fuera_de_rango(pisar):
    with pytest.raises(InvalidParameterError):
        correr(**pisar)


def test_now_sin_zona_se_rechaza():
    with pytest.raises(InvalidDatetimeError):
        correr(now=datetime(2026, 11, 16, 8, 0))
