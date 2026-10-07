"""`deposit.py` — FASE-3-CONTRATO §2.4 (D-005), X3 y X9.

La tabla esperada está copiada **a mano** del contrato.
"""

import pytest

from app.domain.deposit import (
    DEPOSIT_CLOSED_STATUSES,
    DEPOSIT_OPEN_CASE_STATUSES,
    PaymentLine,
    balance_due,
    check_resolution_requirements,
    deposit_required,
    deposit_status_for,
    net_paid,
    resolve_deposit,
    total_agreed,
)
from app.domain.enums import CancellationClassification, DepositStatus, PaymentKind
from app.domain.exceptions import GuardFailedError, InvalidAmountError, InvalidTransition

C = CancellationClassification
D = DepositStatus
K = PaymentKind

# --- estado inicial de la seña al cancelar -----------------------------------

CON_SENA = {
    C.NORMAL: D.DEVOLUCION_PENDIENTE,
    C.TARDIA: D.DEVOLUCION_PENDIENTE,  # C-14: mismo efecto económico que NORMAL
    C.POSTERIOR_AL_TURNO: D.EN_REVISION,
    C.OPERATIVA: D.REPROGRAMACION_O_DEVOLUCION_PENDIENTE,
}


@pytest.mark.parametrize("clasificacion", list(CancellationClassification))
def test_deposit_status_for_con_sena(clasificacion):
    assert deposit_status_for(clasificacion, 1) is CON_SENA[clasificacion]
    assert deposit_status_for(clasificacion, 600_000) is CON_SENA[clasificacion]


@pytest.mark.parametrize("clasificacion", list(CancellationClassification))
def test_deposit_status_for_sin_sena(clasificacion):
    assert deposit_status_for(clasificacion, 0) is D.SIN_PAGO


def test_deposit_status_for_rechaza_negativo():
    with pytest.raises(InvalidAmountError):
        deposit_status_for(C.NORMAL, -1)


# --- resolución: exhaustivo sobre origen × destino ---------------------------

ABIERTOS_CON_CASO = {D.DEVOLUCION_PENDIENTE, D.EN_REVISION, D.REPROGRAMACION_O_DEVOLUCION_PENDIENTE}
CIERRES = {D.DEVUELTA, D.RETENIDA, D.REPROGRAMADA}
# Contrato §2.4 + adenda A2: `RETENIDA` solo desde `EN_REVISION` (C-14 deja abierta la
# retención en cancelaciones a tiempo o tardías; en las del negocio no aplica).
RESOLUCIONES_ESPERADAS = {(o, d) for o in ABIERTOS_CON_CASO for d in CIERRES} - {
    (D.DEVOLUCION_PENDIENTE, D.RETENIDA),
    (D.REPROGRAMACION_O_DEVOLUCION_PENDIENTE, D.RETENIDA),
}
PRODUCTO = [(o, d) for o in DepositStatus for d in DepositStatus]


def test_conjuntos_publicos():
    assert DEPOSIT_OPEN_CASE_STATUSES == ABIERTOS_CON_CASO
    assert DEPOSIT_CLOSED_STATUSES == CIERRES


@pytest.mark.parametrize(("origen", "destino"), PRODUCTO, ids=lambda x: str(x))
def test_resolucion_pasa_si_y_solo_si_esta_en_el_contrato(origen, destino):
    if (origen, destino) in RESOLUCIONES_ESPERADAS:
        assert resolve_deposit(origen, destino) is destino
    else:
        with pytest.raises(InvalidTransition) as exc:
            resolve_deposit(origen, destino)
        assert exc.value.machine == "deposit"


def test_sin_pago_no_se_resuelve():
    for cierre in CIERRES:
        with pytest.raises(InvalidTransition):
            resolve_deposit(D.SIN_PAGO, cierre)


@pytest.mark.parametrize("cierre", [D.RETENIDA, D.REPROGRAMADA])
def test_cerrar_exige_motivo(cierre):
    assert check_resolution_requirements(cierre, " seña retenida ", False) == "seña retenida"
    with pytest.raises(GuardFailedError):
        check_resolution_requirements(cierre, "  ", False)


def test_devuelta_exige_el_pago_de_devolucion():
    assert check_resolution_requirements(D.DEVUELTA, "transferida", True) == "transferida"
    with pytest.raises(GuardFailedError, match="refund"):
        check_resolution_requirements(D.DEVUELTA, "transferida", False)
    with pytest.raises(GuardFailedError, match="reason"):
        check_resolution_requirements(D.DEVUELTA, None, True)


def test_check_resolution_requirements_solo_para_cierres():
    with pytest.raises(InvalidTransition):
        check_resolution_requirements(D.EN_REVISION, "motivo", False)


# --- total pactado y saldo derivado (X9) -------------------------------------


def test_total_agreed():
    assert total_agreed(2_000_000, 300_000, 500_000) == 1_800_000
    assert total_agreed(2_000_000, 0, 0) == 2_000_000
    assert total_agreed(1, 0, 0) == 1


@pytest.mark.parametrize(
    ("base", "recargo", "descuento"),
    [
        (0, 0, 0),  # base > 0
        (-1, 10, 0),
        (100, -1, 0),
        (100, 0, -1),
        (100, 0, 100),  # total 0: R-O-003
        (100, 50, 200),  # total negativo
    ],
)
def test_total_agreed_rechaza(base, recargo, descuento):
    with pytest.raises(InvalidAmountError):
        total_agreed(base, recargo, descuento)


def test_b13_segundo_cobro_suma():
    # R-O-026: un segundo cobro pisaba al primero.
    pagos = [PaymentLine(K.SALDO, 1_000_000), PaymentLine(K.SALDO, 1_000_000)]
    assert balance_due(2_000_000, pagos) == 0


def test_balance_due_suma_senas_y_saldos_y_la_devolucion_resta():
    pagos = [
        PaymentLine(K.SENA, 600_000),
        PaymentLine(K.SALDO, 1_400_000),
        PaymentLine(K.DEVOLUCION, 100_000),
    ]
    assert net_paid(pagos) == 1_900_000
    assert balance_due(2_000_000, pagos) == 100_000


def test_balance_due_ignora_anulados():
    pagos = [
        PaymentLine(K.SENA, 600_000),
        PaymentLine(K.SALDO, 1_400_000, voided=True),
        PaymentLine(K.DEVOLUCION, 600_000, voided=True),
    ]
    assert balance_due(2_000_000, pagos) == 1_400_000


def test_balance_due_sin_pagos_y_con_saldo_a_favor():
    assert balance_due(2_000_000, []) == 2_000_000
    assert balance_due(2_000_000, [PaymentLine(K.SALDO, 2_500_000)]) == -500_000


def test_balance_due_acepta_tuplas_simples():
    assert balance_due(100, [(K.SALDO, 40, False), (K.SALDO, 60, True)]) == 60
    # Un `kind` que llega como texto (sin pasar por el enum) igual resta.
    assert balance_due(100, [("SALDO", 100, False), ("DEVOLUCION", 30, False)]) == 30


@pytest.mark.parametrize("monto", [0, -1])
def test_balance_due_rechaza_montos_no_positivos(monto):
    with pytest.raises(InvalidAmountError):
        balance_due(100, [PaymentLine(K.SALDO, monto)])


def test_balance_due_rechaza_total_no_positivo():
    with pytest.raises(InvalidAmountError):
        balance_due(0, [])


# --- seña requerida (R-T-003, R-C-032) ---------------------------------------


def test_deposit_required():
    assert deposit_required(2_000_000, 3000) == 600_000
    assert deposit_required(333, 5000) == 167  # 166,5 → 167 half-up
    assert deposit_required(2_000_000, 0) == 0
    assert deposit_required(None, 3000) == 0  # sin precio no hay seña


def test_deposit_required_rechaza_precio_no_positivo():
    with pytest.raises(InvalidAmountError):
        deposit_required(0, 3000)
