"""Excepciones del dominio puro — FASE-3-CONTRATO §2 y §3.

Distintas de `errors.py`, que es el catálogo cerrado de códigos HTTP (ADR-0013):
estas nacen en funciones sin I/O y es la capa de aplicación (F4+) la que decide a
qué código las traduce. Los mensajes van en inglés (son de sistema); la
documentación, en castellano.
"""


class DomainError(Exception):
    """Base de todo error del dominio puro."""


class InvalidAmountError(DomainError):
    """Importe inválido o ambiguo. Nunca se devuelve `0` en su lugar (R-O-029)."""


class InvalidPlateError(DomainError):
    """Patente que no se puede normalizar sin perder información."""


class InvalidDatetimeError(DomainError):
    """Fecha sin zona horaria: la demora y la anticipación se calculan entre instantes."""


class InvalidParameterError(DomainError):
    """Parámetro de negocio fuera de rango (tolerancia, umbral negativos)."""


class GuardFailedError(DomainError):
    """La transición existe en la tabla, pero su guarda no se cumple."""


# Sin sufijo `Error` (convención del repo, N818) a propósito: es el nombre que fija
# FASE-3-CONTRATO §2 y con el que lo buscan los servicios de aplicación.
class InvalidTransition(DomainError):  # noqa: N818
    """La transición no está en la tabla (whitelist, [corregir] R-O-018)."""

    def __init__(self, machine: str, current: object, action: object) -> None:
        self.machine = machine
        self.current = current
        self.action = action
        super().__init__(f"{machine}: {action} is not allowed from {current}")
