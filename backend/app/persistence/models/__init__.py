"""Importa todos los modelos para que `Base.metadata` (y el autogenerate) los vea."""

from app.persistence.models.dummy_resource import DummyResource
from app.persistence.models.idempotency_key import IdempotencyKey
from app.persistence.models.tenant import Tenant
from app.persistence.models.user import User

__all__ = ["DummyResource", "IdempotencyKey", "Tenant", "User"]
