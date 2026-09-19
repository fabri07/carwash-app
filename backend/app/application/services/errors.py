"""Excepciones de los servicios de aplicación — FASE-3-CONTRATO §4.

Heredan de `DomainError` (o de `GuardFailedError`) para que la capa HTTP de F4+ las traduzca
con un solo `except`. Nunca son HTTP: el código de estado lo decide el endpoint.
"""

from app.domain.exceptions import DomainError, GuardFailedError


class NotFoundError(DomainError):
    """La fila no existe **en este tenant** (o está anulada). F4+ la traduce a 404, nunca 403:
    un recurso ajeno no se distingue de uno inexistente (no filtrar existencia)."""

    def __init__(self, entity: str, id: object) -> None:
        self.entity = entity
        self.id = id
        super().__init__(f"{entity} not found: {id}")


class IdempotencyKeyReusedError(DomainError):
    """La clave ya se usó en este tenant para **otra** operación (otro job, otro tipo de
    evento, otro pago). No es un reenvío: devolver el resultado anterior sería mentir."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"idempotency key already used for a different operation: {key!r}")


class SlotTakenError(DomainError):
    """El `EXCLUDE` de `bookings` rechazó el intervalo: el puesto está ocupado (§1.3)."""


class AlreadyExistsError(DomainError):
    """Choque con un único "entre vivos" (código, nombre, patente, teléfono)."""


class CatalogIncoherentError(GuardFailedError):
    """Precio y modalidad no concuerdan (§1.1): `PRECIO_FIJO` ⇒ precio; `A_COTIZAR` ⇒ sin
    precio y seña 0. La regla cruza tablas y por eso vive en la aplicación, no en un CHECK."""


class QuoteAlreadyUsedError(GuardFailedError):
    """La cotización ya la usa otro job vivo: una cotización se usa en **un** job (índice único
    parcial `ux_jobs_tenant_id_quote_id`). También es la traducción de ese único ante la carrera."""
