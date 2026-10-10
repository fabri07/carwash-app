"""Cuentas de empleados y perfiles de permisos; sale `dummy_resources`.

Revision ID: 0004_cuentas_permisos
Revises: 0003_dominio
Create Date: 2026-10-09

Por qué (FASE-4-CONTRATO §2, §3.1, Y3, Y12):

- **`permission_profiles`** (nueva, con RLS): nombre + `permissions text[]` con CHECK de
  subconjunto contra el enum `Permission`, congelado acá.
- **`users`**: `email` pasa a nullable (el empleado entra con usuario, D4-3); suman
  `username` (único global), `permission_profile_id` (FK compuesta, NN si es `STAFF`, NULL si
  es `OWNER`) y `must_change_password`.
- **Procedimiento sobre datos existentes**, en este orden: se crean los 3 perfiles por
  defecto en cada tenant, los `STAFF` que ya existen pasan a **Encargado** (no pierden
  acceso) y recién después se aplica el CHECK `OWNER ↔ perfil`. Las tablas tienen
  `FORCE ROW LEVEL SECURITY` y el dueño de la migración no ve filas sin tenant en contexto:
  el relleno se hace con `NO FORCE` dentro de esta misma transacción y se vuelve a forzar
  antes de terminar (si algo falla, el rollback deja todo como estaba).
- **`auth_lookup_user(identifier)`**: el login busca por email o por usuario.
- **Sale `dummy_resources`** (X13 de F3): su lugar en los tests de aislamiento lo toma
  `permission_profiles`.

Idempotente (A6), como 0003: tablas con `_has_table`, columnas e índices con
`if_not_exists`, constraints con `DO $$ … IF NOT EXISTS`, relleno con `NOT EXISTS`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.persistence.db.rls import (
    create_auth_lookup,
    create_auth_lookup_by_identifier,
    enable_rls,
    grant_app_role,
)

revision: str = "0004_cuentas_permisos"
down_revision: str | None = "0003_dominio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Congelados acá (como `ROLES` en 0001): la migración describe el esquema que creó, no el
# enum vivo. `tests/meta/test_permisos.py` compara contra `app/domain/permissions.py`.
PERMISSIONS = (
    "AGENDA_VER",
    "TURNOS_GESTIONAR",
    "JOBS_OPERAR",
    "COBROS_REGISTRAR",
    "COBROS_ANULAR",
    "CAJA_VER",
    "CATALOGO_EDITAR",
    "PRECIOS_EDITAR",
    "CLIENTES_VER",
    "CLIENTES_EDITAR",
    "GASTOS_REGISTRAR",
    "REPORTES_VER",
)
DEFAULT_PROFILES: dict[str, tuple[str, ...]] = {
    "Encargado": PERMISSIONS,
    "Cajero": (
        "AGENDA_VER",
        "TURNOS_GESTIONAR",
        "JOBS_OPERAR",
        "COBROS_REGISTRAR",
        "CAJA_VER",
        "CLIENTES_VER",
        "CLIENTES_EDITAR",
    ),
    "Lavador": ("AGENDA_VER", "JOBS_OPERAR", "CLIENTES_VER"),
}
LEGACY_STAFF_PROFILE = "Encargado"
USERNAME_PATTERN = "^[a-z0-9._-]{3,40}$"

ALIVE = "voided_at IS NULL"
VOID_REASONS = ("ERROR_DE_CARGA", "PEDIDO_DEL_USUARIO", "DESACTIVADO")
LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"

PROFILES = "permission_profiles"
PROFILE_FK = "fk_users_tenant_id_permission_profile_id_permission_profiles"


def _array(values: Sequence[str]) -> str:
    return "ARRAY[" + ", ".join(f"'{v}'" for v in values) + "]::text[]"


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _add_constraint(table: str, name: str, definition: str) -> None:
    """`ADD CONSTRAINT` idempotente: Postgres no tiene `ADD CONSTRAINT IF NOT EXISTS`."""
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_constraint "
        f"WHERE conname = '{name}' AND conrelid = 'public.{table}'::regclass) THEN "
        f"ALTER TABLE {table} ADD CONSTRAINT {name} {definition}; "
        "END IF; END $$"
    )


def _create_profiles_table() -> None:
    if not _has_table(PROFILES):
        op.create_table(
            PROFILES,
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column(
                "permissions",
                postgresql.ARRAY(sa.Text()),
                server_default=sa.text("'{}'"),
                nullable=False,
            ),
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("tenant_id", sa.UUID(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "void_reason",
                postgresql.ENUM(*VOID_REASONS, name="void_reason", create_type=False),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("id", name=f"pk_{PROFILES}"),
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["tenants.id"],
                name=f"fk_{PROFILES}_tenant_id_tenants",
                ondelete="RESTRICT",
            ),
            sa.UniqueConstraint("tenant_id", "id", name=f"uq_{PROFILES}_tenant_id_id"),
            sa.CheckConstraint(
                "(voided_at IS NULL) = (void_reason IS NULL)",
                name=op.f(f"ck_{PROFILES}_void_coherente"),
            ),
            sa.CheckConstraint(
                "length(name) BETWEEN 1 AND 40", name=op.f(f"ck_{PROFILES}_nombre_largo")
            ),
            sa.CheckConstraint(
                f"permissions <@ {_array(PERMISSIONS)}",
                name=op.f(f"ck_{PROFILES}_permisos_conocidos"),
            ),
        )
    op.create_index(f"ix_{PROFILES}_tenant_id", PROFILES, ["tenant_id"], if_not_exists=True)
    op.create_index(
        f"ux_{PROFILES}_tenant_id_name",
        PROFILES,
        ["tenant_id", "name"],
        unique=True,
        postgresql_where=sa.text(ALIVE),
        if_not_exists=True,
    )
    for statement in [*enable_rls(PROFILES), *grant_app_role([PROFILES])]:
        op.execute(statement)


def _alter_users() -> None:
    op.alter_column("users", "email", existing_type=sa.Text(), nullable=True)
    op.add_column("users", sa.Column("username", sa.Text(), nullable=True), if_not_exists=True)
    op.add_column(
        "users", sa.Column("permission_profile_id", sa.UUID(), nullable=True), if_not_exists=True
    )
    op.add_column(
        "users",
        sa.Column(
            "must_change_password", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        if_not_exists=True,
    )
    _add_constraint("users", "uq_users_username", "UNIQUE (username)")
    _add_constraint(
        "users",
        PROFILE_FK,
        "FOREIGN KEY (tenant_id, permission_profile_id) "
        f"REFERENCES {PROFILES} (tenant_id, id) ON DELETE RESTRICT",
    )
    op.create_index(
        "ix_users_tenant_id_permission_profile_id",
        "users",
        ["tenant_id", "permission_profile_id"],
        if_not_exists=True,
    )
    _add_constraint(
        "users", "ck_users_username_formato", f"CHECK (username ~ '{USERNAME_PATTERN}')"
    )
    _add_constraint(
        "users",
        "ck_users_con_identificador",
        "CHECK (email IS NOT NULL OR username IS NOT NULL)",
    )
    _add_constraint(
        "users", "ck_users_owner_con_email", "CHECK (role <> 'OWNER' OR email IS NOT NULL)"
    )


def _backfill() -> None:
    """Perfiles por defecto en cada tenant y Encargado a los `STAFF` existentes.

    Con `FORCE` el dueño también pasa por RLS y, sin tenant en contexto, no ve filas: el
    relleno corre con `NO FORCE` y se vuelve a forzar en la misma transacción.
    """
    tables = ("tenants", PROFILES, "users")
    for table in tables:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    for name, permissions in DEFAULT_PROFILES.items():
        op.execute(
            f"INSERT INTO {PROFILES} (id, tenant_id, name, permissions) "
            f"SELECT gen_random_uuid(), t.id, '{name}', {_array(permissions)} "
            "FROM tenants AS t "
            f"WHERE NOT EXISTS (SELECT 1 FROM {PROFILES} AS p "
            f"WHERE p.tenant_id = t.id AND p.name = '{name}' AND p.{ALIVE})"
        )
    op.execute(
        f"UPDATE users AS u SET permission_profile_id = p.id FROM {PROFILES} AS p "
        f"WHERE u.role = 'STAFF' AND u.permission_profile_id IS NULL "
        f"AND p.tenant_id = u.tenant_id AND p.name = '{LEGACY_STAFF_PROFILE}' AND p.{ALIVE}"
    )
    for table in tables:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.execute(LOCK_TIMEOUT)
    _create_profiles_table()
    _alter_users()
    _backfill()
    # Recién con los STAFF ya asignados (si no, los existentes lo violarían).
    _add_constraint(
        "users",
        "ck_users_perfil_segun_rol",
        "CHECK ((role = 'OWNER') = (permission_profile_id IS NULL))",
    )
    for statement in create_auth_lookup_by_identifier():
        op.execute(statement)
    op.execute("DROP TABLE IF EXISTS dummy_resources")


def downgrade() -> None:
    """Vuelve al esquema de 0003. Corta, con un mensaje que dice por qué, si:

    - hay usuarios sin email (empleados con solo usuario): bajar no puede inventarles uno
      ni borrarlos en silencio;
    - hay usuarios vivos con `must_change_password`: 0003 no tiene la compuerta y entrarían
      con la clave que eligió el dueño, sin cambiarla.

    El `SELECT` ve todas las filas: el dueño tiene la política `users_owner_auth_lookup`.
    """
    op.execute(LOCK_TIMEOUT)
    op.execute(
        """
        DO $$
        DECLARE
            sin_email integer;
            clave_pendiente integer;
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'users'
                  AND column_name = 'must_change_password'
            ) THEN
                SELECT count(*) INTO sin_email FROM users WHERE email IS NULL;
                SELECT count(*) INTO clave_pendiente FROM users
                    WHERE must_change_password AND voided_at IS NULL;
                IF sin_email > 0 THEN
                    RAISE EXCEPTION
                        'downgrade 0004: % usuarios sin email (solo usuario); 0003 exige email',
                        sin_email;
                END IF;
                IF clave_pendiente > 0 THEN
                    RAISE EXCEPTION
                        'downgrade 0004: % usuarios vivos con cambio de clave pendiente; '
                        '0003 no tiene la compuerta', clave_pendiente;
                END IF;
            END IF;
        END $$
        """
    )
    op.execute("DROP FUNCTION IF EXISTS auth_lookup_user(text)")
    for statement in create_auth_lookup():
        op.execute(statement)

    for name in (
        "ck_users_perfil_segun_rol",
        "ck_users_owner_con_email",
        "ck_users_con_identificador",
        "ck_users_username_formato",
        PROFILE_FK,
        "uq_users_username",
    ):
        op.execute(f"ALTER TABLE users DROP CONSTRAINT IF EXISTS {name}")
    op.drop_index("ix_users_tenant_id_permission_profile_id", "users", if_exists=True)
    for column in ("must_change_password", "permission_profile_id", "username"):
        op.execute(f"ALTER TABLE users DROP COLUMN IF EXISTS {column}")
    op.alter_column("users", "email", existing_type=sa.Text(), nullable=False)
    op.drop_table(PROFILES, if_exists=True)

    if not _has_table("dummy_resources"):
        op.create_table(
            "dummy_resources",
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("tenant_id", sa.UUID(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "void_reason",
                postgresql.ENUM(*VOID_REASONS, name="void_reason", create_type=False),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("id", name="pk_dummy_resources"),
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["tenants.id"],
                name="fk_dummy_resources_tenant_id_tenants",
                ondelete="RESTRICT",
            ),
            sa.CheckConstraint(
                "(voided_at IS NULL) = (void_reason IS NULL)",
                name=op.f("ck_dummy_resources_void_coherente"),
            ),
        )
    op.create_index(
        "ix_dummy_resources_tenant_id", "dummy_resources", ["tenant_id"], if_not_exists=True
    )
    for statement in [*enable_rls("dummy_resources"), *grant_app_role(["dummy_resources"])]:
        op.execute(statement)
