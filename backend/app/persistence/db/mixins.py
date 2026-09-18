"""`TenantMixin` (ADR-0001) y `VoidableMixin` (ADR-0003).

Es el archivo que Véktor no tiene y por eso repite `tenant_id` 51 veces en 37
archivos, con cuatro formas distintas de la misma columna.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.domain.void import VOID_REASON_ENUM_NAME, VoidReason


def enum_values(enum_cls: type[Any]) -> list[str]:
    """Persistir el `.value` del StrEnum, no el nombre del miembro."""
    return [member.value for member in enum_cls]


class TenantMixin:
    """La ÚNICA forma de tener `tenant_id`. Redeclararlo en un modelo está prohibido.

    `ondelete="RESTRICT"`: borrar un tenant no puede vaciar media base sin que
    nadie lo haya pedido. La política RLS (ADR-0002) se genera para toda tabla que
    use este mixin.
    """

    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805 — declared_attr recibe la clase
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )


class VoidableMixin:
    """Un solo flavor de soft-delete: `voided_at` + `void_reason` + CHECK.

    El CHECK `ck_<tabla>_void_coherente` (nombre vía `naming_convention`) hace
    imposible el estado "alguien anuló esto y no sabemos por qué". Anular es una
    operación del repositorio (`BaseRepository.void`), no un `UPDATE` suelto.
    """

    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    void_reason: Mapped[VoidReason | None] = mapped_column(
        Enum(
            VoidReason,
            name=VOID_REASON_ENUM_NAME,
            native_enum=True,
            validate_strings=True,
            values_callable=enum_values,
        ),
        nullable=True,
    )

    @declared_attr.directive
    def __table_args__(cls) -> tuple[Any, ...]:  # noqa: N805
        return (
            CheckConstraint(
                "(voided_at IS NULL) = (void_reason IS NULL)",
                name="void_coherente",
            ),
        )
