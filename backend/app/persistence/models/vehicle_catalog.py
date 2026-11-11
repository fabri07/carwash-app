"""Catálogo de vehículos del negocio — FASE-4-CONTRATO §3.4, Y7 y A10 (PR 4.4).

Marcas, modelos y colores **copiados por tenant** desde la base del mercado argentino
(`app/data/vehiculos_ar.json`): cada negocio renombra, oculta y suma los suyos sin tablas de
plataforma ni FKs polimórficas (Y7). `body_type_sizes` dice qué tamaño del negocio sugiere cada
carrocería.

- **Ocultar no es anular.** `hidden_at` saca la fila de los selectores; sigue viva, conserva su
  nombre en el único y se vuelve a mostrar. La baja (`voided_at`, ADR-0003) no tiene endpoint en
  este PR: un vehículo cargado (F6) puede apuntar a la fila.
- **`is_custom`**: la sumó el negocio (no viene de la base). La `0008` la usa para no bajar
  borrando datos propios.
- **`search_key`** = `normalize(name)`, como en `services` (Y8): default de Python y lo fija el
  servicio al crear y renombrar. Únicos entre vivos sobre la clave, no sobre el nombre: "Citroen" y
  "Citroën" son la misma marca.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.engine.default import DefaultExecutionContext
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.search import normalize
from app.domain.vehicle_catalog import (
    COLOR_NAME_MAX,
    MAKE_NAME_MAX,
    MODEL_NAME_MAX,
    VEHICLE_BODY_TYPE_ENUM,
    VehicleBodyType,
)
from app.persistence.db.base import TenantScopedModel
from app.persistence.models._constraints import (
    check,
    parent_key,
    pg_enum,
    tenant_fk,
    tenant_index,
    unique_alive,
    voidable_table_args,
)


def _search_key_of_name(context: DefaultExecutionContext) -> str:
    """Default de Python de `search_key`: `normalize(name)` de la misma fila."""
    params = context.get_current_parameters()  # type: ignore[no-untyped-call]
    return normalize(str(params["name"]))


def _is_custom() -> Mapped[bool]:
    return mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))


def _hidden_at() -> Mapped[datetime | None]:
    return mapped_column(DateTime(timezone=True), nullable=True)


def _search_key() -> Mapped[str]:
    return mapped_column(Text, nullable=False, default=_search_key_of_name)


class VehicleMake(TenantScopedModel):
    """Marca (`Toyota`, `Volkswagen`…)."""

    __tablename__ = "vehicle_makes"
    __table_args__ = voidable_table_args(
        parent_key(),
        unique_alive("vehicle_makes", "search_key"),
        check(f"length(name) BETWEEN 1 AND {MAKE_NAME_MAX}", "nombre_largo"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    search_key: Mapped[str] = _search_key()
    is_custom: Mapped[bool] = _is_custom()
    hidden_at: Mapped[datetime | None] = _hidden_at()


class VehicleModel(TenantScopedModel):
    """Modelo de una marca, con su carrocería (`Hilux` → `PICKUP`)."""

    __tablename__ = "vehicle_models"
    __table_args__ = voidable_table_args(
        parent_key(),
        tenant_fk("make_id", "vehicle_makes"),
        tenant_index("vehicle_models", "make_id"),
        unique_alive("vehicle_models", "make_id", "search_key"),
        check(f"length(name) BETWEEN 1 AND {MODEL_NAME_MAX}", "nombre_largo"),
    )

    make_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    body_type: Mapped[VehicleBodyType] = mapped_column(
        pg_enum(VehicleBodyType, VEHICLE_BODY_TYPE_ENUM), nullable=False
    )
    search_key: Mapped[str] = _search_key()
    is_custom: Mapped[bool] = _is_custom()
    hidden_at: Mapped[datetime | None] = _hidden_at()


class VehicleColor(TenantScopedModel):
    """Color (`Blanco`, `Gris oscuro`…)."""

    __tablename__ = "vehicle_colors"
    __table_args__ = voidable_table_args(
        parent_key(),
        unique_alive("vehicle_colors", "search_key"),
        check(f"length(name) BETWEEN 1 AND {COLOR_NAME_MAX}", "nombre_largo"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    search_key: Mapped[str] = _search_key()
    is_custom: Mapped[bool] = _is_custom()
    hidden_at: Mapped[datetime | None] = _hidden_at()


class BodyTypeSize(TenantScopedModel):
    """Qué tamaño del negocio sugiere cada carrocería (A10). Una fila viva por carrocería;
    sin fila, el modelo no sugiere tamaño."""

    __tablename__ = "body_type_sizes"
    __table_args__ = voidable_table_args(
        tenant_fk("vehicle_size_id", "vehicle_sizes"),
        tenant_index("body_type_sizes", "vehicle_size_id"),
        unique_alive("body_type_sizes", "body_type"),
    )

    body_type: Mapped[VehicleBodyType] = mapped_column(
        pg_enum(VehicleBodyType, VEHICLE_BODY_TYPE_ENUM), nullable=False
    )
    vehicle_size_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
