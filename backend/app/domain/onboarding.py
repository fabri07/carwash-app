"""Wizard de alta — FASE-4-CONTRATO §6 y §11.9 (PR 4.6).

Vocabulario y reglas puras: los pasos del wizard en orden, los requisitos que exige
`POST /onboarding/complete` y en qué paso se resuelve cada uno, y cómo se elige el paso donde se
reanuda. Quien mira la base es `application/services/onboarding_service.py`.

Hay dos clases de paso:

- **con requisito** (tamaños, servicios, puestos y horarios, medios de pago): está hecho cuando se
  cumple lo que pide el contrato, nada más;
- **opcional** (negocio, personalización, seña, empleados): no tiene requisito, pero el wizard tiene
  que reanudar donde se cortó. Sin una columna de progreso (el contrato no la prevé y no hace falta
  una migración), un paso opcional cuenta como hecho si guardó algo propio **o** si algún paso
  posterior ya tiene datos: quien cargó tamaños ya pasó por el negocio, aunque lo haya dejado como
  venía del registro. Así el progreso es monótono y un negocio recién creado arranca en el primero.
"""

from collections.abc import Mapping
from enum import StrEnum


class OnboardingStep(StrEnum):
    """Pasos del wizard, en el orden de §6."""

    BUSINESS = "BUSINESS"
    BRANDING = "BRANDING"
    SIZES = "SIZES"
    SERVICES = "SERVICES"
    SCHEDULE = "SCHEDULE"
    PAYMENTS = "PAYMENTS"
    DEPOSIT = "DEPOSIT"
    STAFF = "STAFF"


STEPS: tuple[OnboardingStep, ...] = tuple(OnboardingStep)


class OnboardingRequirement(StrEnum):
    """Lo que exige `POST /onboarding/complete` (§6). Cada uno se resuelve en un paso."""

    VEHICLE_SIZE = "VEHICLE_SIZE"
    SERVICE = "SERVICE"
    SERVICE_PRICES = "SERVICE_PRICES"
    RESOURCE = "RESOURCE"
    BUSINESS_HOURS = "BUSINESS_HOURS"
    PAYMENT_METHOD = "PAYMENT_METHOD"


#: En qué paso se carga cada requisito: el front lleva ahí al recibir el 409.
REQUIREMENT_STEP: Mapping[OnboardingRequirement, OnboardingStep] = {
    OnboardingRequirement.VEHICLE_SIZE: OnboardingStep.SIZES,
    OnboardingRequirement.SERVICE: OnboardingStep.SERVICES,
    OnboardingRequirement.SERVICE_PRICES: OnboardingStep.SERVICES,
    OnboardingRequirement.RESOURCE: OnboardingStep.SCHEDULE,
    OnboardingRequirement.BUSINESS_HOURS: OnboardingStep.SCHEDULE,
    OnboardingRequirement.PAYMENT_METHOD: OnboardingStep.PAYMENTS,
}

#: Pasos con requisito; los demás son opcionales.
REQUIRED_STEPS: frozenset[OnboardingStep] = frozenset(REQUIREMENT_STEP.values())


def steps_done(
    has_data: Mapping[OnboardingStep, bool],
    missing: frozenset[OnboardingRequirement] | set[OnboardingRequirement],
) -> dict[OnboardingStep, bool]:
    """Qué pasos cuentan como hechos.

    `has_data[step]`: el paso tiene algo guardado (para los opcionales, algo distinto de lo que
    trae el registro). `missing`: los requisitos que faltan. Un paso con requisito está hecho si
    no le falta ninguno; uno opcional, si tiene datos o algún paso posterior los tiene.
    """
    blocked = {REQUIREMENT_STEP[r] for r in missing}
    done: dict[OnboardingStep, bool] = {}
    later_has_data = False
    for step in reversed(STEPS):
        own = bool(has_data.get(step, False))
        if step in REQUIRED_STEPS:
            done[step] = step not in blocked
        else:
            done[step] = own or later_has_data
        later_has_data = later_has_data or own
    return {step: done[step] for step in STEPS}


def resume_step(done: Mapping[OnboardingStep, bool]) -> OnboardingStep:
    """El primer paso sin hacer; con todos hechos, el último (desde ahí se termina)."""
    for step in STEPS:
        if not done.get(step, False):
            return step
    return STEPS[-1]
