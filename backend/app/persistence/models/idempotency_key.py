"""Claves de idempotencia — tabla propia.

En Véktor se reusaba `operation_fingerprints` con prefijo `idem:` para compartir
tabla con el dedup del agente LLM. Acá no hay agente: tabla propia.
"""

from sqlalchemy import Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.persistence.db.mixins import TenantMixin

IDEMPOTENCY_UNIQUE = "uq_idempotency_keys_tenant_id_key"


class IdempotencyKey(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)

    key: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
