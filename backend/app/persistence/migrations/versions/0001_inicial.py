"""Esquema inicial: tenants, users, dummy_resources, idempotency_keys + RLS.

Revision ID: 0001_inicial
Revises:
Create Date: 2026-09-16

Las políticas RLS se crean en la MISMA migración que crea cada tabla (ADR-0002),
con `ENABLE` + `FORCE` + `USING` + `WITH CHECK`, para toda tabla con `TenantMixin`.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.persistence.db.rls import (
    create_auth_lookup,
    disable_rls,
    drop_auth_lookup,
    enable_rls,
    grant_app_role,
)

revision: str = "0001_inicial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Congelados acá a propósito: una migración no importa los Enum vivos, o dejaría de
# describir el esquema que creó. `test_roles.py` compara Python contra la base.
ROLES = ("OWNER", "STAFF")
VOID_REASONS = ("ERROR_DE_CARGA", "PEDIDO_DEL_USUARIO", "DESACTIVADO")

role_enum = postgresql.ENUM(*ROLES, name="role", create_type=False)
void_reason_enum = postgresql.ENUM(*VOID_REASONS, name="void_reason", create_type=False)

#: Tablas con TenantMixin creadas por esta migración.
TENANT_TABLES = ["users", "dummy_resources", "idempotency_keys"]


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _voidable(table: str) -> list[Any]:
    return [
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", void_reason_enum, nullable=True),
        sa.CheckConstraint(
            "(voided_at IS NULL) = (void_reason IS NULL)", name=op.f(f"ck_{table}_void_coherente")
        ),
    ]


def _tenant_fk(table: str) -> list[Any]:
    return [
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=f"fk_{table}_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
    ]


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _create_type(name: str, values: tuple[str, ...]) -> None:
    joined = ", ".join(repr(v) for v in values)
    op.execute(
        f"DO $$ BEGIN CREATE TYPE {name} AS ENUM ({joined}); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )


def upgrade() -> None:
    # Idempotente a propósito (A6): el `preDeployCommand` corre en CADA deploy, y un
    # `alembic_version` atrasado respecto del esquema real no puede abortar el deploy
    # con `DuplicateTable` / `DuplicateObject` (Véktor, incidente del 2026-09-12).
    _create_type("role", ROLES)
    _create_type("void_reason", VOID_REASONS)

    if not _has_table("tenants"):
        op.create_table(
            "tenants",
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("id", sa.UUID(), nullable=False),
            *_timestamps(),
            *_voidable("tenants"),
            sa.PrimaryKeyConstraint("id", name="pk_tenants"),
        )

    if not _has_table("users"):
        op.create_table(
            "users",
            sa.Column("email", sa.Text(), nullable=False),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("role", role_enum, nullable=False),
            sa.Column("token_version", sa.Integer(), server_default="0", nullable=False),
            sa.Column("id", sa.UUID(), nullable=False),
            *_tenant_fk("users"),
            *_timestamps(),
            *_voidable("users"),
            sa.PrimaryKeyConstraint("id", name="pk_users"),
            sa.UniqueConstraint("email", name="uq_users_email"),
        )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"], if_not_exists=True)

    if not _has_table("dummy_resources"):
        op.create_table(
            "dummy_resources",
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("id", sa.UUID(), nullable=False),
            *_tenant_fk("dummy_resources"),
            *_timestamps(),
            *_voidable("dummy_resources"),
            sa.PrimaryKeyConstraint("id", name="pk_dummy_resources"),
        )
    op.create_index(
        "ix_dummy_resources_tenant_id", "dummy_resources", ["tenant_id"], if_not_exists=True
    )

    if not _has_table("idempotency_keys"):
        op.create_table(
            "idempotency_keys",
            sa.Column("key", sa.Text(), nullable=False),
            sa.Column("action", sa.Text(), nullable=False),
            sa.Column("id", sa.UUID(), nullable=False),
            *_tenant_fk("idempotency_keys"),
            *_timestamps(),
            sa.PrimaryKeyConstraint("id", name="pk_idempotency_keys"),
            sa.UniqueConstraint("tenant_id", "key", name="uq_idempotency_keys_tenant_id_key"),
        )
    op.create_index(
        "ix_idempotency_keys_tenant_id", "idempotency_keys", ["tenant_id"], if_not_exists=True
    )

    # ── RLS (ADR-0002) ────────────────────────────────────────────────────────
    # Misma política, sin excepciones, en toda tabla con tenant. `tenants` también
    # (L3): cada tenant solo se ve y se edita a sí mismo.
    for statement in enable_rls("tenants", column="id"):
        op.execute(statement)
    for table in TENANT_TABLES:
        for statement in enable_rls(table):
            op.execute(statement)

    # Búsqueda de identidad del login, sin abrir la política de `users` (BUG-1/M4).
    for statement in grant_app_role(["tenants", *TENANT_TABLES]):
        op.execute(statement)
    for statement in create_auth_lookup():
        op.execute(statement)


def downgrade() -> None:
    for statement in drop_auth_lookup():
        op.execute(statement)
    for table in [*reversed(TENANT_TABLES), "tenants"]:
        for statement in disable_rls(table):
            op.execute(statement)
    op.drop_index("ix_idempotency_keys_tenant_id", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_dummy_resources_tenant_id", table_name="dummy_resources")
    op.drop_table("dummy_resources")
    op.drop_index("ix_users_tenant_id", table_name="users")
    op.drop_table("users")
    op.drop_table("tenants")
    op.execute("DROP TYPE void_reason")
    op.execute("DROP TYPE role")
