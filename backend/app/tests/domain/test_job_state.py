"""`job_state.py` — FASE-3-CONTRATO §2.2, criterios B12 y B13.

La tabla esperada está copiada **a mano** del contrato.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.enums import JobEventType, JobStatus
from app.domain.exceptions import (
    GuardFailedError,
    InvalidDatetimeError,
    InvalidParameterError,
    InvalidTransition,
)
from app.domain.job_state import (
    TRANSITION_EVENTS,
    apply_event,
    check_delay_cancellation,
    require_reason,
    retention_is_active,
)

J = JobStatus
E = JobEventType
TODOS = frozenset(JobStatus)

# Eventos con transición: (origen, evento) → destino.
TRANSICIONES: dict[tuple[JobStatus | None, JobEventType], JobStatus] = {
    (None, E.JOB_RECEIVED): J.PRESENTE,
    (J.PRESENTE, E.JOB_STARTED): J.EN_PROCESO,
    (J.EN_PROCESO, E.JOB_FINISHED): J.FINALIZADO,
    (J.FINALIZADO, E.JOB_SETTLED): J.COBRADO,
    (J.COBRADO, E.JOB_PICKED_UP): J.RETIRADO,
    (J.PRESENTE, E.JOB_CANCELLED_DELAY): J.CANCELADO_DEMORA,
}

# Eventos sin transición: estados desde los que son válidos (el estado no cambia).
SIN_TRANSICION: dict[JobEventType, frozenset[JobStatus]] = {
    # "solo junto a JOB_CANCELLED_DELAY": se emite con el job ya en CANCELADO_DEMORA.
    E.DEPOSIT_RETAINED: frozenset({J.CANCELADO_DEMORA}),
    # Solo hay retención que reversar en un job cancelado por demora.
    E.DEPOSIT_RETENTION_REVERSED: frozenset({J.CANCELADO_DEMORA}),
    # "job no CANCELADO_DEMORA".
    E.PAYMENT_RECORDED: TODOS - {J.CANCELADO_DEMORA},
    E.PAYMENT_VOIDED: TODOS - {J.CANCELADO_DEMORA},
    # Sin guarda de estado en el contrato: la inspección es informativa (D-007).
    E.INSPECTION_RECORDED: TODOS,
    # Interpretación conservadora (ver reporte): no se ajusta el precio de un job ya
    # cobrado, retirado o cancelado.
    E.PRICE_ADJUSTED: frozenset({J.PRESENTE, J.EN_PROCESO, J.FINALIZADO}),
}

ORIGENES: list[JobStatus | None] = [None, *JobStatus]
PRODUCTO = [(o, e) for o in ORIGENES for e in JobEventType]


def test_toda_accion_esta_clasificada_una_sola_vez():
    con = {e for (_o, e) in TRANSICIONES}
    assert con.isdisjoint(SIN_TRANSICION)
    assert con | set(SIN_TRANSICION) == set(JobEventType)
    assert con == TRANSITION_EVENTS


@pytest.mark.parametrize(("origen", "evento"), PRODUCTO, ids=lambda x: str(x))
def test_evento_pasa_si_y_solo_si_esta_en_el_contrato(origen, evento):
    if (origen, evento) in TRANSICIONES:
        assert apply_event(origen, evento) is TRANSICIONES[(origen, evento)]
    elif evento in SIN_TRANSICION and origen in SIN_TRANSICION[evento]:
        # Válido y sin transición: `None` = el job conserva su estado.
        assert apply_event(origen, evento) is None
    else:
        with pytest.raises(InvalidTransition) as exc:
            apply_event(origen, evento)
        assert exc.value.current == origen
        assert exc.value.action == evento
        assert exc.value.machine == "job"


# --- B13: casos del legacy corregidos ----------------------------------------


def test_b13_cancelar_por_demora_un_finalizado_falla():
    # R-O-016 / C-7: el legacy cancelaba sin guarda un FINALIZADO cobrado.
    with pytest.raises(InvalidTransition):
        apply_event(J.FINALIZADO, E.JOB_CANCELLED_DELAY)


def test_b13_finalizar_sin_iniciar_falla():
    # R-O-022: PRESENTE → FINALIZADO no existe.
    with pytest.raises(InvalidTransition):
        apply_event(J.PRESENTE, E.JOB_FINISHED)


def test_b13_refinalizar_falla():
    with pytest.raises(InvalidTransition):
        apply_event(J.FINALIZADO, E.JOB_FINISHED)


def test_b13_el_ingreso_siempre_nace_presente():
    # R-O-001: el operador elegía el estado inicial.
    for estado in JobStatus:
        with pytest.raises(InvalidTransition):
            apply_event(estado, E.JOB_RECEIVED)


TURNO = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)


def test_b13_walk_in_no_se_cancela_por_demora():
    with pytest.raises(GuardFailedError, match="walk-in"):
        check_delay_cancellation(None, TURNO + timedelta(hours=1), tolerance_min=20)


def test_b13_demora_igual_a_la_tolerancia_no_alcanza():
    with pytest.raises(GuardFailedError, match="tolerance"):
        check_delay_cancellation(TURNO, TURNO + timedelta(minutes=20), tolerance_min=20)


def test_demora_mayor_a_la_tolerancia_devuelve_la_demora_aplicada():
    assert check_delay_cancellation(TURNO, TURNO + timedelta(minutes=21), tolerance_min=20) == 21


def test_la_demora_se_recalcula_con_el_occurred_at_y_hace_floor():
    # 20 min 59 s son 20 minutos: no supera la tolerancia de 20.
    with pytest.raises(GuardFailedError):
        check_delay_cancellation(TURNO, TURNO + timedelta(minutes=20, seconds=59), 20)


def test_la_tolerancia_es_parametro():
    assert check_delay_cancellation(TURNO, TURNO + timedelta(minutes=6), tolerance_min=5) == 6
    assert check_delay_cancellation(TURNO, TURNO + timedelta(minutes=1), tolerance_min=0) == 1


def test_tolerancia_negativa_se_rechaza():
    with pytest.raises(InvalidParameterError):
        check_delay_cancellation(TURNO, TURNO + timedelta(hours=1), tolerance_min=-1)


def test_check_delay_cancellation_exige_aware():
    with pytest.raises(InvalidDatetimeError):
        check_delay_cancellation(TURNO, datetime(2026, 9, 18, 11, 0), tolerance_min=20)


# --- retención vigente (C-12: sin columna escalar) ---------------------------


@pytest.mark.parametrize(
    ("eventos", "vigente"),
    [
        ([], False),
        ([E.JOB_RECEIVED, E.JOB_CANCELLED_DELAY], False),
        ([E.JOB_RECEIVED, E.JOB_CANCELLED_DELAY, E.DEPOSIT_RETAINED], True),
        ([E.DEPOSIT_RETAINED, E.DEPOSIT_RETENTION_REVERSED], False),
        ([E.DEPOSIT_RETENTION_REVERSED, E.DEPOSIT_RETAINED], True),  # la reversión es anterior
        ([E.DEPOSIT_RETAINED, E.DEPOSIT_RETENTION_REVERSED, E.DEPOSIT_RETAINED], True),
        ([E.DEPOSIT_RETAINED, E.PAYMENT_RECORDED], True),
    ],
)
def test_retention_is_active(eventos, vigente):
    assert retention_is_active(eventos) is vigente
    assert retention_is_active(iter(eventos)) is vigente  # acepta cualquier iterable


# --- motivo obligatorio (D-001.4) --------------------------------------------


@pytest.mark.parametrize("motivo", [None, "", "   ", "\t\n"])
def test_require_reason_rechaza_vacio(motivo):
    with pytest.raises(GuardFailedError, match="reason"):
        require_reason(motivo)


def test_require_reason_devuelve_el_motivo_limpio():
    assert require_reason("  el cliente avisó  ") == "el cliente avisó"
