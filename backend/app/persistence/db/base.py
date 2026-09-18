"""Declarative base de SQLAlchemy y mixins compartidos.

Portado de Véktor (`app/persistence/db/base.py`). Se conservan `PGJSONB` /
`PGTEXTARRAY` con `.with_variant()`, `TimestampMixin` y `UUIDPrimaryKeyMixin`.
**Se agrega** la `naming_convention` de la `MetaData`, que Véktor no tiene
(`grep -rn naming_convention app/` → cero) y sin la cual los nombres de CHECK y
UNIQUE no son deterministas y los tests de ADR-0003 / ADR-0004 no pueden
afirmarlos.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, MetaData, Text, func
from sqlalchemy.dialects.postgresql import ARRAY as _ARRAY
from sqlalchemy.dialects.postgresql import JSONB as _JSONB
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSONB en PostgreSQL, JSON en el resto (SQLite en tests).
PGJSONB = JSON().with_variant(_JSONB(), "postgresql")

# TEXT[] en PostgreSQL, JSON en el resto (SQLite en tests).
PGTEXTARRAY = JSON().with_variant(_ARRAY(Text()), "postgresql")

#: Nombres deterministas para índices y constraints. Sin esto, un CHECK sin
#: nombre explícito queda anónimo en PostgreSQL y `ck_<tabla>_void_coherente`
#: (ADR-0003) no se puede afirmar desde un test ni revertir desde un downgrade.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base — todos los modelos ORM heredan de acá."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: dict[Any, Any] = {}


class TimestampMixin:
    """Agrega created_at / updated_at a cualquier modelo.

    Los defaults del lado de Python garantizan el timestamp en SQLite, donde
    `server_default=func.now()` puede no dispararse.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        # Python-side, no `func.now()` como en Véktor: un `onupdate` del lado del
        # servidor deja el atributo expirado después del flush, y leerlo en async
        # (al serializar la respuesta) revienta con `MissingGreenlet`.
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class UUIDPrimaryKeyMixin:
    """PK UUID llamada `id` (ADR-0004).

    El valor lo genera Python (`uuid.uuid4`), no la base: así el código conoce el
    `id` antes del flush, que es lo que hace posible el patrón de `_savepoint.py`.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


# Import al final: `mixins` importa `domain`, y `TenantScopedModel` necesita `Base`.
from app.persistence.db.mixins import TenantMixin, VoidableMixin  # noqa: E402


class TenantScopedModel(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, VoidableMixin):
    """Base abstracta de toda entidad aislada por tenant y anulable.

    No crea tabla. Existe para que `BaseRepository[ModelT]` tipe `id`, `tenant_id`
    y `voided_at` sin `type: ignore`.
    """

    __abstract__ = True
