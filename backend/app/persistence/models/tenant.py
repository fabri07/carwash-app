"""Tenant — la raíz del aislamiento. No lleva `tenant_id` (ADR-0001, lista blanca)."""

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.persistence.db.mixins import VoidableMixin


class Tenant(Base, UUIDPrimaryKeyMixin, TimestampMixin, VoidableMixin):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(Text, nullable=False)
