"""Recurso dummy — existe SOLO para que el test cruzado de T3 tenga sobre qué correr.

Cero dominio de lavadero.
"""

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.db.base import TenantScopedModel


class DummyResource(TenantScopedModel):
    __tablename__ = "dummy_resources"

    name: Mapped[str] = mapped_column(Text, nullable=False)
