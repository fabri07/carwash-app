"""Piezas de `__table_args__` compartidas por los modelos del dominio — FASE-3-CONTRATO §1.

Existe para que las reglas transversales del contrato se escriban UNA vez:

- **X4** `tenant_fk`: toda FK entre tablas de tenant es `(tenant_id, x_id) → padre(tenant_id, id)`,
  `RESTRICT`. `parent_key` es el `UNIQUE (tenant_id, id)` que Postgres exige en el padre.
- **X5** `tenant_index`: el índice que cubre esa FK, con nombre explícito
  `ix_<tabla>_tenant_id_<col>` (la `naming_convention` `ix_%(column_0_label)s` repetiría
  `ix_<tabla>_tenant_id` en todos los compuestos). Cada FK compuesta tiene uno propio, no
  parcial: un índice único parcial (`WHERE voided_at IS NULL`) no sirve para el chequeo de
  `RESTRICT` ni para un `JOIN` que incluya filas anuladas.
- **X7** `unique_alive`: único "entre vivos" como índice parcial, con la MISMA condición para
  Postgres y SQLite.
- `pg_check`: CHECKs que usan sintaxis de Postgres (`~`, `btrim`, `->>`). Van con
  `.ddl_if(dialect="postgresql")`: la suite rápida los saltea al hacer `create_all` en SQLite, y
  la migración los crea siempre (solo corre contra Postgres).

`voidable_table_args` resuelve el choque con `VoidableMixin`: el mixin declara su CHECK
`ck_<tabla>_void_coherente` (ADR-0003) en un `__table_args__` propio, y un modelo que define el
suyo lo pisaría. En vez de copiar el CHECK a mano en cada modelo, se evalúa el del mixin (cada
acceso devuelve un CHECK nuevo, porque un constraint se ata a una sola tabla) y se le suman los
propios. Si el mixin cambia, cambia en todas las tablas.

**Límite de 63 caracteres:** Postgres trunca en silencio los identificadores más largos y el
nombre real deja de coincidir con el del ORM. `test_esquema_dominio.py` lo verifica sobre toda
la metadata.
"""

from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, Enum, ForeignKeyConstraint, Index, UniqueConstraint, text

from app.persistence.db.mixins import VoidableMixin, enum_values

#: Condición de "fila viva" (ADR-0003). Anular libera los únicos parciales (X7).
ALIVE = "voided_at IS NULL"


def voidable_table_args(*items: Any) -> tuple[Any, ...]:
    """`__table_args__` de un modelo anulable: el CHECK del mixin más los propios."""
    return (*VoidableMixin.__table_args__, *items)


def pg_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Enum nativo, igual que `role` (ADR-0005): persiste el `.value`, no el nombre del miembro."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=True,
        validate_strings=True,
        values_callable=enum_values,
    )


def parent_key() -> UniqueConstraint:
    """`UNIQUE (tenant_id, id)`: lo que una FK compuesta necesita en el padre (X4)."""
    return UniqueConstraint("tenant_id", "id")


def tenant_fk(column: str, parent: str, *, use_alter: bool = False) -> ForeignKeyConstraint:
    """FK compuesta `(tenant_id, column) → parent(tenant_id, id)`, `RESTRICT` (X4).

    El chequeo de FK no pasa por RLS: con una FK simple, el lavadero B podría apuntar al
    cliente de A conociendo su UUID. Con esta, la base lo rechaza.

    `use_alter`: solo para romper el ciclo `bookings.quote_id` ↔ `quotes.booking_id`.
    """
    return ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{parent}.tenant_id", f"{parent}.id"],
        ondelete="RESTRICT",
        use_alter=use_alter,
    )


def tenant_index(table: str, *columns: str) -> Index:
    """Índice `(tenant_id, *columns)` con nombre explícito (X5)."""
    return Index(f"ix_{table}_tenant_id_{'_'.join(columns)}", "tenant_id", *columns)


def unique_alive(table: str, *columns: str, where: str | None = None) -> Index:
    """Único `(tenant_id, *columns)` entre filas vivas (X7), con condición extra opcional."""
    condition = ALIVE if where is None else f"{where} AND {ALIVE}"
    return Index(
        f"ux_{table}_tenant_id_{'_'.join(columns)}",
        "tenant_id",
        *columns,
        unique=True,
        postgresql_where=text(condition),
        sqlite_where=text(condition),
    )


def check(sql: str, name: str) -> CheckConstraint:
    """CHECK portable (Postgres y SQLite). El nombre final es `ck_<tabla>_<name>`."""
    return CheckConstraint(sql, name=name)


def pg_check(sql: str, name: str) -> CheckConstraint:
    """CHECK con sintaxis de Postgres: no se emite en SQLite (suite rápida)."""
    return CheckConstraint(sql, name=name).ddl_if(dialect="postgresql")
