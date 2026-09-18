"""Schemas compartidos y envoltorios de respuesta.

Adaptado de Véktor (`schemas/common.py`): se conservan `PaginatedResponse[T]` y
`CamelModel`. **Se activan** `ErrorDetail` y `ErrorResponse` —definidos y sin un
solo uso en Véktor— con un `ErrorCode` cerrado (ADR-0013). `has_more` pasa de
`@property` (que Pydantic no serializa) a `computed_field`: es parte del contrato
(ADR-0006).
"""

from pydantic import BaseModel, ConfigDict, computed_field

from app.domain.errors import ErrorCode


class CamelModel(BaseModel):
    """Base de los schemas de API."""

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
    )


class PaginatedResponse[T](BaseModel):
    """El único envelope de colección (ADR-0006)."""

    items: list[T]
    total: int
    limit: int
    offset: int

    @computed_field  # type: ignore[prop-decorator]  # mypy no admite decoradores sobre @property; es el uso documentado de pydantic
    @property
    def has_more(self) -> bool:
        return (self.offset + self.limit) < self.total


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    field: str | None = None


class ErrorResponse(BaseModel):
    """Forma única de todo error que emite la API."""

    detail: ErrorDetail
