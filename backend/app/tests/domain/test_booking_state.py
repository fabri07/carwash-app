"""`booking_state.py` — FASE-3-CONTRATO §2.1, criterio B12.

La tabla esperada está copiada **a mano** del contrato: no se importa la de
producción para compararla consigo misma.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.booking_state import (
    BLOCKING_BOOKING_STATUSES,
    BookingAction,
    anticipation_min,
    can_client_cancel,
    classify_cancellation,
    client_cancellation_result,
    hold_expired,
    next_booking_status,
)
from app.domain.enums import BookingStatus, CancellationClassification
from app.domain.exceptions import InvalidDatetimeError, InvalidParameterError, InvalidTransition

S = BookingStatus
A = BookingAction
ACTIVOS = (S.PENDIENTE_SENA, S.PENDIENTE_COTIZACION, S.CONFIRMADO)

# (estado origen, acción) → estado destino. §2.1, fila por fila.
ESPERADO: dict[tuple[BookingStatus | None, BookingAction], BookingStatus] = {
    (None, A.CREATE_WITH_DEPOSIT): S.PENDIENTE_SENA,
    (None, A.CREATE_FOR_QUOTE): S.PENDIENTE_COTIZACION,
    (None, A.CREATE_CONFIRMED): S.CONFIRMADO,
    (S.PENDIENTE_SENA, A.RECORD_DEPOSIT): S.CONFIRMADO,
    (S.PENDIENTE_COTIZACION, A.ACCEPT_QUOTE_CONFIRMED): S.CONFIRMADO,
    (S.PENDIENTE_COTIZACION, A.ACCEPT_QUOTE_WITH_DEPOSIT): S.PENDIENTE_SENA,
    (S.PENDIENTE_SENA, A.EXPIRE_HOLD): S.VENCIDO,
    (S.PENDIENTE_COTIZACION, A.EXPIRE_HOLD): S.VENCIDO,
    (S.VENCIDO, A.LATE_CONFIRM): S.CONFIRMADO,
    **{(s, A.CANCEL_BY_CLIENT): S.CANCELADO_CLIENTE for s in ACTIVOS},
    **{(s, A.CANCEL_BY_CLIENT_AFTER_START): S.AUSENTE_CON_AVISO_POSTERIOR for s in ACTIVOS},
    **{(s, A.CANCEL_OPERATIONAL): S.CANCELADO_OPERATIVO for s in ACTIVOS},
    (S.CONFIRMADO, A.MARK_NO_SHOW): S.NO_ASISTIO,
    (S.CONFIRMADO, A.RECEIVE): S.RECIBIDO,
    (S.PENDIENTE_SENA, A.RECEIVE): S.RECIBIDO,
    (S.RECIBIDO, A.JOB_FINISHED): S.ATENDIDO,
    (S.RECIBIDO, A.JOB_CANCELLED_DELAY): S.CANCELADO_DEMORA,
}

ORIGENES: list[BookingStatus | None] = [None, *BookingStatus]
PRODUCTO = [(o, a) for o in ORIGENES for a in BookingAction]


def test_el_producto_cubre_toda_la_tabla_esperada():
    assert set(ESPERADO) <= set(PRODUCTO)
    assert len(PRODUCTO) == 12 * len(BookingAction)


@pytest.mark.parametrize(("origen", "accion"), PRODUCTO, ids=lambda x: str(x))
def test_transicion_pasa_si_y_solo_si_esta_en_el_contrato(origen, accion):
    if (origen, accion) in ESPERADO:
        assert next_booking_status(origen, accion) is ESPERADO[(origen, accion)]
    else:
        with pytest.raises(InvalidTransition) as exc:
            next_booking_status(origen, accion)
        assert exc.value.current == origen
        assert exc.value.action == accion
        assert exc.value.machine == "booking"


def test_los_terminales_no_salen():
    terminales = {
        S.CANCELADO_CLIENTE,
        S.AUSENTE_CON_AVISO_POSTERIOR,
        S.CANCELADO_OPERATIVO,
        S.CANCELADO_DEMORA,
        S.NO_ASISTIO,
    }
    assert not {o for (o, _a) in ESPERADO if o in terminales}


def test_vencido_no_es_terminal():
    # R-T-030: una confirmación tardía lo revive.
    assert next_booking_status(S.VENCIDO, A.LATE_CONFIRM) is S.CONFIRMADO


def test_estados_que_bloquean_el_intervalo_coinciden_con_el_exclude():
    # El WHERE del EXCLUDE de §1.3, textual.
    assert {s.value for s in BLOCKING_BOOKING_STATUSES} == {
        "PENDIENTE_SEÑA",
        "PENDIENTE_COTIZACION",
        "CONFIRMADO",
        "RECIBIDO",
    }
    assert isinstance(BLOCKING_BOOKING_STATUSES, frozenset)


@pytest.mark.parametrize("estado", list(BookingStatus))
def test_can_client_cancel_solo_desde_activos(estado):
    # R-T-036 corregido: ni desde VENCIDO ni desde estados cerrados.
    assert can_client_cancel(estado) is (estado in ACTIVOS)


# --- clasificación de la cancelación (§2.4, R-T-037) ------------------------


@pytest.mark.parametrize(
    ("anticipacion", "clasificacion"),
    [
        (10_000, CancellationClassification.NORMAL),
        (720, CancellationClassification.NORMAL),  # borde: ≥ umbral
        (719, CancellationClassification.TARDIA),
        (1, CancellationClassification.TARDIA),
        (0, CancellationClassification.TARDIA),  # borde: 0 es tardía, no posterior
        (-1, CancellationClassification.POSTERIOR_AL_TURNO),
        (-600, CancellationClassification.POSTERIOR_AL_TURNO),
    ],
)
def test_classify_cancellation_con_umbral_default(anticipacion, clasificacion):
    assert classify_cancellation(anticipacion) is clasificacion


def test_classify_cancellation_con_umbral_parametrizado():
    assert classify_cancellation(60, late_threshold_min=60) is CancellationClassification.NORMAL
    assert classify_cancellation(59, late_threshold_min=60) is CancellationClassification.TARDIA
    assert classify_cancellation(0, late_threshold_min=0) is CancellationClassification.NORMAL
    assert classify_cancellation(-1, late_threshold_min=0) is (
        CancellationClassification.POSTERIOR_AL_TURNO
    )


def test_classify_cancellation_rechaza_umbral_negativo():
    with pytest.raises(InvalidParameterError):
        classify_cancellation(10, late_threshold_min=-1)


@pytest.mark.parametrize(
    ("anticipacion", "estado"),
    [
        (720, S.CANCELADO_CLIENTE),
        (0, S.CANCELADO_CLIENTE),
        (-1, S.AUSENTE_CON_AVISO_POSTERIOR),
    ],
)
def test_client_cancellation_result(anticipacion, estado):
    assert client_cancellation_result(anticipacion) is estado


def test_client_cancellation_result_es_alcanzable_por_la_accion_correspondiente():
    for anticipacion in (0, -1):
        destino = client_cancellation_result(anticipacion)
        accion = A.CANCEL_BY_CLIENT if anticipacion >= 0 else A.CANCEL_BY_CLIENT_AFTER_START
        assert next_booking_status(S.CONFIRMADO, accion) is destino


TURNO = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("pedido", "minutos"),
    [
        (TURNO - timedelta(hours=12), 720),
        (TURNO - timedelta(hours=12) + timedelta(seconds=1), 719),  # floor
        (TURNO, 0),
        (TURNO + timedelta(seconds=1), -1),  # floor hacia -inf: ya es posterior
        (TURNO + timedelta(minutes=30), -30),
    ],
)
def test_anticipation_min(pedido, minutos):
    assert anticipation_min(TURNO, pedido) == minutos


def test_anticipation_min_exige_aware():
    with pytest.raises(InvalidDatetimeError):
        anticipation_min(TURNO, datetime(2026, 9, 18, 9, 0))


# --- vencimiento del hold (C-16: un solo comparador) ------------------------


@pytest.mark.parametrize(
    ("vence", "vencido"),
    [
        (TURNO - timedelta(seconds=1), True),
        (TURNO, True),  # `<=`: en el instante exacto ya venció
        (TURNO + timedelta(seconds=1), False),
        (None, False),  # sin hold no hay nada que vencer
    ],
)
def test_hold_expired(vence, vencido):
    assert hold_expired(vence, TURNO) is vencido


def test_hold_expired_exige_aware():
    with pytest.raises(InvalidDatetimeError):
        hold_expired(datetime(2026, 9, 18, 9, 0), TURNO)
    with pytest.raises(InvalidDatetimeError):
        hold_expired(TURNO, datetime(2026, 9, 18, 9, 0))
