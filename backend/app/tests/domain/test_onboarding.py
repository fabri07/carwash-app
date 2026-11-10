"""Reglas puras del wizard de alta (PR 4.6): pasos, requisitos y dónde se reanuda."""

from app.domain.onboarding import (
    REQUIRED_STEPS,
    REQUIREMENT_STEP,
    STEPS,
    OnboardingRequirement,
    OnboardingStep,
    resume_step,
    steps_done,
)

S = OnboardingStep
R = OnboardingRequirement
TODO_FALTA = set(R)


def test_el_orden_es_el_del_contrato():
    assert [s.value for s in STEPS] == [
        "BUSINESS",
        "BRANDING",
        "SIZES",
        "SERVICES",
        "SCHEDULE",
        "PAYMENTS",
        "DEPOSIT",
        "STAFF",
    ]


def test_cada_requisito_tiene_su_paso_y_solo_esos_son_obligatorios():
    assert set(REQUIREMENT_STEP) == set(R)
    assert {S.SIZES, S.SERVICES, S.SCHEDULE, S.PAYMENTS} == REQUIRED_STEPS


def test_negocio_nuevo_arranca_en_el_primero():
    done = steps_done({}, TODO_FALTA)
    assert not any(done.values())
    assert resume_step(done) == S.BUSINESS


def test_un_opcional_con_datos_esta_hecho_aunque_no_haya_nada_despues():
    done = steps_done({S.BUSINESS: True}, TODO_FALTA)
    assert done[S.BUSINESS] and not done[S.BRANDING]
    assert resume_step(done) == S.BRANDING


def test_datos_en_un_paso_posterior_dan_por_pasados_a_los_opcionales_anteriores():
    done = steps_done({S.SIZES: True}, TODO_FALTA - {R.VEHICLE_SIZE})
    assert done[S.BUSINESS] and done[S.BRANDING] and done[S.SIZES]
    assert resume_step(done) == S.SERVICES


def test_un_obligatorio_no_se_salta_aunque_haya_datos_despues():
    # hay puestos y medios de pago pero falta el precio de un servicio
    done = steps_done(
        {S.SIZES: True, S.SERVICES: True, S.SCHEDULE: True, S.PAYMENTS: True},
        {R.SERVICE_PRICES},
    )
    assert not done[S.SERVICES] and done[S.SCHEDULE]
    assert resume_step(done) == S.SERVICES


def test_con_todo_hecho_se_reanuda_en_el_ultimo():
    done = steps_done({S.STAFF: True}, set())
    assert all(done.values())
    assert resume_step(done) == S.STAFF


def test_sin_sena_ni_empleados_se_reanuda_en_la_sena():
    done = steps_done({S.SIZES: True, S.SERVICES: True, S.SCHEDULE: True, S.PAYMENTS: True}, set())
    assert not done[S.DEPOSIT] and not done[S.STAFF]
    assert resume_step(done) == S.DEPOSIT
