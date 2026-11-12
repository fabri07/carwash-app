"""Agenda de la F5: reglas horarias, aperturas, lista de espera, incidencias y slug público.

Revision ID: 0010_agenda_f5
Revises: 0009_onboarding_existentes
Create Date: 2026-11-11

Por qué (FASE-5-CONTRATO §3, Z2): **una sola migración** con todo el esquema de la fase, para que
los PRs 5.2 y 5.3 (paralelos) no migren ni dejen dos heads.

- **`services.booking_mode`** (enum `service_booking_mode`: `TURNERO`, `SOLO_A_MANO`; default
  `TURNERO`). Los servicios existentes quedan en `TURNERO`, como hasta ahora.
- **`service_schedule_rules`** (§3.2): en qué ventana puede arrancar un servicio cada día.
- **`schedule_openings`** (§3.3): disponibilidad especial, espejo de `schedule_blocks`.
- **`tenant_settings`** suma `booking_horizon_days` (14, 1–90), `slot_step_min` (30, 5–120) y
  `deposit_instructions` (≤ 500, NULL).
- **`booking_status`** suma `PENDIENTE_REVISION` y `RECHAZADO` (§3.5). `ALTER TYPE … ADD VALUE`
  corre en un bloque **autocommit** propio: Postgres no deja usar un valor nuevo en la misma
  transacción que lo agrega, y así ningún paso posterior (de esta migración o de otra) lo
  tropieza. Ninguno entra al `WHERE` del `EXCLUDE`, que no se recrea.
- **`customer_incident_marks`** (§3.6): marca manual de incidencia (D-014).
- **`public_tenant_by_slug(text)`** (§3.7, Z7): `SECURITY DEFINER`, como `auth_lookup_user`, con
  la lectura del dueño sobre `tenants` y `tenant_settings` (ver `rls.py`).

**Guarda de slugs (§3.8):** `RESERVED_SLUGS` suma `catalogo`, `register` e `icons`. Si un negocio
**vivo** ya usa un slug que pasó a reservado, la migración **corta y lo dice** antes de tocar nada:
hay que cambiarle el slug a mano y reintentar. La lista se congela acá (`RESERVED_SLUGS`): la
migración describe lo que reservó en su momento.

Las tablas tienen `FORCE ROW LEVEL SECURITY` y el dueño de la migración no ve filas sin tenant en
contexto: las guardas corren con `NO FORCE` y se vuelve a forzar al terminar (como 0007 a 0009).

Idempotente (A6), como 0003 a 0009.

**Downgrade con corte explícito:** si hay datos que bajar perdería (reglas, aperturas o marcas
vivas; servicios "solo a mano"; horizonte, paso o instrucciones distintos del default; turnos en
lista de espera o rechazados), **corta y lo dice**. Los dos valores de `booking_status` **quedan**
en el tipo: Postgres no puede sacar un valor de un enum sin recrearlo (y con él el `EXCLUDE` y los
CHECK que lo usan). Sin filas que los usen son inertes, y volver a subir los reusa
(`ADD VALUE IF NOT EXISTS`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import SchemaItem

from app.persistence.db.rls import (
    create_public_tenant_lookup,
    drop_public_tenant_lookup,
    enable_rls,
    grant_app_role,
)

revision: str = "0010_agenda_f5"
down_revision: str | None = "0009_onboarding_existentes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALIVE = "voided_at IS NULL"
LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"
VOID_REASONS = ("ERROR_DE_CARGA", "PEDIDO_DEL_USUARIO", "DESACTIVADO")

# Congelados acá: la migración describe lo que creó. `test_migracion_0010_pg.py` los compara con
# `app/domain/enums.py` y `app/domain/slug.py`.
BOOKING_MODE_ENUM = "service_booking_mode"
BOOKING_MODES = ("TURNERO", "SOLO_A_MANO")
BOOKING_STATUS_ENUM = "booking_status"
#: Se agregan **en este orden** al final del tipo (los mismos que `BookingStatus`, al final).
BOOKING_STATUS_ADDED = ("PENDIENTE_REVISION", "RECHAZADO")
RESERVED_SLUGS = (
    "admin",
    "api",
    "app",
    "auth",
    "cambiar-clave",
    "catalogo",
    "configuracion",
    "dashboard",
    "debug",
    "health",
    "icons",
    "login",
    "logout",
    "onboarding",
    "ready",
    "register",
    "registro",
    "reservas",
    "static",
    "turnos",
    "v1",
    "www",
)

DEFAULT_HORIZON_DAYS = 14
DEFAULT_STEP_MIN = 30
REASON_MAX = 160
DEPOSIT_INSTRUCTIONS_MAX = 500
DEFAULT_OPENING_REASON = "Apertura especial"

RULES = "service_schedule_rules"
OPENINGS = "schedule_openings"
MARKS = "customer_incident_marks"
NEW_TABLES = (RULES, OPENINGS, MARKS)

#: `tenant_settings.<columna>` → (tipo, default del server, CHECK, nombre del CHECK).
SETTINGS_CHECKS = {
    "ck_tenant_settings_horizonte_rango": "booking_horizon_days BETWEEN 1 AND 90",
    "ck_tenant_settings_paso_rango": "slot_step_min BETWEEN 5 AND 120",
    "ck_tenant_settings_instrucciones_sena_largo": (
        "deposit_instructions IS NULL OR length(deposit_instructions) "
        f"BETWEEN 1 AND {DEPOSIT_INSTRUCTIONS_MAX}"
    ),
}


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    return bool(
        op.get_bind().scalar(
            sa.text(
                "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        )
    )


def _add_constraint(table: str, name: str, definition: str) -> None:
    """`ADD CONSTRAINT` idempotente: Postgres no tiene `ADD CONSTRAINT IF NOT EXISTS`."""
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_constraint "
        f"WHERE conname = '{name}' AND conrelid = 'public.{table}'::regclass) THEN "
        f"ALTER TABLE {table} ADD CONSTRAINT {name} {definition}; "
        "END IF; END $$"
    )


def _base_columns(table: str) -> list[SchemaItem]:
    """Lo que trae `TenantScopedModel`: id, tenant, marcas de tiempo y anulación (ADR-0003)."""
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "void_reason",
            postgresql.ENUM(*VOID_REASONS, name="void_reason", create_type=False),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{table}"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=f"fk_{table}_tenant_id_tenants", ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "(voided_at IS NULL) = (void_reason IS NULL)", name=op.f(f"ck_{table}_void_coherente")
        ),
    ]


def _tenant_fk(table: str, column: str, parent: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{parent}.tenant_id", f"{parent}.id"],
        name=f"fk_{table}_tenant_id_{column}_{parent}",
        ondelete="RESTRICT",
    )


def _check(table: str, name: str, sql: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(sql, name=op.f(f"ck_{table}_{name}"))


def _index(table: str, column: str) -> None:
    op.create_index(
        f"ix_{table}_tenant_id_{column}", table, ["tenant_id", column], if_not_exists=True
    )


# ── Upgrade ───────────────────────────────────────────────────────────────────


def _guard_reserved_slugs() -> None:
    """Corta si un negocio vivo usa un slug que pasó a reservado (§3.8). Antes de tocar nada."""
    op.execute("ALTER TABLE tenants NO FORCE ROW LEVEL SECURITY")
    taken = [
        row[0]
        for row in op.get_bind().execute(
            sa.text(
                f"SELECT slug FROM tenants WHERE {ALIVE} AND slug = ANY(:reserved) ORDER BY slug"
            ),
            {"reserved": list(RESERVED_SLUGS)},
        )
    ]
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    if taken:
        raise RuntimeError(
            "upgrade 0010: hay negocios vivos con un slug que ahora es reservado "
            f"({', '.join(taken)}). Cambiarles el slug a mano "
            "(UPDATE tenants SET slug = '<otro>' WHERE slug = '<reservado>') y reintentar."
        )


def _add_booking_statuses() -> None:
    """`ADD VALUE` en autocommit: el valor nuevo no se puede usar en la transacción que lo crea."""
    with op.get_context().autocommit_block():
        for value in BOOKING_STATUS_ADDED:
            op.execute(
                f"ALTER TYPE {BOOKING_STATUS_ENUM} ADD VALUE IF NOT EXISTS {_literal(value)}"
            )
    op.execute(LOCK_TIMEOUT)  # la transacción nueva no hereda el `SET LOCAL`


def _alter_services() -> None:
    op.execute(
        f"DO $$ BEGIN CREATE TYPE {BOOKING_MODE_ENUM} AS ENUM "
        f"({', '.join(_literal(v) for v in BOOKING_MODES)}); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    op.add_column(
        "services",
        sa.Column(
            "booking_mode",
            postgresql.ENUM(*BOOKING_MODES, name=BOOKING_MODE_ENUM, create_type=False),
            server_default=BOOKING_MODES[0],
            nullable=False,
        ),
        if_not_exists=True,
    )


def _alter_settings() -> None:
    op.add_column(
        "tenant_settings",
        sa.Column(
            "booking_horizon_days",
            sa.SmallInteger(),
            server_default=str(DEFAULT_HORIZON_DAYS),
            nullable=False,
        ),
        if_not_exists=True,
    )
    op.add_column(
        "tenant_settings",
        sa.Column(
            "slot_step_min", sa.SmallInteger(), server_default=str(DEFAULT_STEP_MIN), nullable=False
        ),
        if_not_exists=True,
    )
    op.add_column(
        "tenant_settings",
        sa.Column("deposit_instructions", sa.Text(), nullable=True),
        if_not_exists=True,
    )
    for name, sql in SETTINGS_CHECKS.items():
        _add_constraint("tenant_settings", name, f"CHECK ({sql})")


def _create_tables() -> None:
    if not _has_table(RULES):
        op.create_table(
            RULES,
            *_base_columns(RULES),
            sa.Column("service_id", sa.UUID(), nullable=False),
            sa.Column("weekday", sa.SmallInteger(), nullable=False),
            sa.Column("window_start", sa.Time(), nullable=False),
            sa.Column("last_start", sa.Time(), nullable=False),
            _tenant_fk(RULES, "service_id", "services"),
            _check(RULES, "weekday_iso", "weekday BETWEEN 1 AND 7"),
            _check(RULES, "ventana_valida", "last_start >= window_start"),
        )
    if not _has_table(OPENINGS):
        op.create_table(
            OPENINGS,
            *_base_columns(OPENINGS),
            sa.Column("resource_id", sa.UUID(), nullable=True),
            sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("reason", sa.Text(), server_default=DEFAULT_OPENING_REASON, nullable=False),
            sa.Column("created_by_user_id", sa.UUID(), nullable=False),
            _tenant_fk(OPENINGS, "resource_id", "resources"),
            _tenant_fk(OPENINGS, "created_by_user_id", "users"),
            _check(OPENINGS, "rango_valido", "ends_at > starts_at"),
            _check(OPENINGS, "motivo_largo", f"length(reason) BETWEEN 1 AND {REASON_MAX}"),
        )
    if not _has_table(MARKS):
        op.create_table(
            MARKS,
            *_base_columns(MARKS),
            sa.Column("customer_id", sa.UUID(), nullable=False),
            sa.Column("booking_id", sa.UUID(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("created_by_user_id", sa.UUID(), nullable=False),
            _tenant_fk(MARKS, "customer_id", "customers"),
            _tenant_fk(MARKS, "booking_id", "bookings"),
            _tenant_fk(MARKS, "created_by_user_id", "users"),
            _check(MARKS, "motivo_largo", f"length(reason) BETWEEN 1 AND {REASON_MAX}"),
        )
    for table in NEW_TABLES:
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"], if_not_exists=True)
    _index(RULES, "service_id")
    _index(OPENINGS, "resource_id")
    _index(OPENINGS, "created_by_user_id")
    _index(MARKS, "customer_id")
    _index(MARKS, "booking_id")
    _index(MARKS, "created_by_user_id")
    for statement in [
        *(s for table in NEW_TABLES for s in enable_rls(table)),
        *grant_app_role(list(NEW_TABLES)),
    ]:
        op.execute(statement)


def upgrade() -> None:
    op.execute(LOCK_TIMEOUT)
    _guard_reserved_slugs()
    _add_booking_statuses()
    _alter_services()
    _alter_settings()
    _create_tables()
    for statement in create_public_tenant_lookup():
        op.execute(statement)


# ── Downgrade ─────────────────────────────────────────────────────────────────


def _business_data() -> dict[str, int]:
    """Lo que el negocio cargó con la F5 y bajar perdería. Vacío si no hay nada."""
    bind = op.get_bind()
    counts: dict[str, int] = {}

    def count(label: str, sql: str) -> None:
        counts[label] = int(bind.scalar(sa.text(sql)) or 0)

    # Solo filas vivas: lo anulado ya lo descartó el negocio y bajar no le quita nada.
    if _has_table(RULES):
        count("reglas horarias de servicios", f"SELECT count(*) FROM {RULES} WHERE {ALIVE}")
    if _has_table(OPENINGS):
        count("aperturas especiales", f"SELECT count(*) FROM {OPENINGS} WHERE {ALIVE}")
    if _has_table(MARKS):
        count("marcas de incidencia", f"SELECT count(*) FROM {MARKS} WHERE {ALIVE}")
    if _has_column("services", "booking_mode"):
        count(
            "servicios solo a mano",
            f"SELECT count(*) FROM services WHERE {ALIVE} AND booking_mode <> 'TURNERO'",
        )
    if _has_column("tenant_settings", "booking_horizon_days"):
        count(
            "negocios con horizonte, paso o instrucciones de sena propios",
            "SELECT count(*) FROM tenant_settings WHERE "
            f"booking_horizon_days <> {DEFAULT_HORIZON_DAYS} "
            f"OR slot_step_min <> {DEFAULT_STEP_MIN} OR deposit_instructions IS NOT NULL",
        )
    added = ", ".join(_literal(v) for v in BOOKING_STATUS_ADDED)
    count(
        "turnos en lista de espera o rechazados",
        f"SELECT count(*) FROM bookings WHERE {ALIVE} AND status::text IN ({added})",
    )
    return {label: n for label, n in counts.items() if n}


def downgrade() -> None:
    """Vuelve al esquema de 0009. Corta, diciendo por qué, si perdería datos del negocio."""
    op.execute(LOCK_TIMEOUT)
    # `NO FORCE` para que el chequeo vea todas las filas (con FORCE y sin tenant en contexto: 0).
    tables = ("services", "tenant_settings", "bookings", *NEW_TABLES)
    for table in tables:
        if _has_table(table):
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    lost = _business_data()
    if lost:
        detail = ", ".join(f"{n} {label}" for label, n in lost.items())
        raise RuntimeError(
            f"downgrade 0010: se perderian datos de la agenda ({detail}). Vaciarlos a mano "
            "(anular reglas, aperturas y marcas; volver los servicios a TURNERO; horizonte 14, "
            "paso 30 e instrucciones NULL; resolver los turnos en lista de espera o rechazados) "
            "y reintentar."
        )
    for statement in drop_public_tenant_lookup():
        op.execute(statement)
    for table in (MARKS, OPENINGS, RULES):
        op.drop_table(table, if_exists=True)
    for name in SETTINGS_CHECKS:
        op.execute(f"ALTER TABLE tenant_settings DROP CONSTRAINT IF EXISTS {name}")
    for column in ("deposit_instructions", "slot_step_min", "booking_horizon_days"):
        op.execute(f"ALTER TABLE tenant_settings DROP COLUMN IF EXISTS {column}")
    op.execute("ALTER TABLE services DROP COLUMN IF EXISTS booking_mode")
    op.execute(f"DROP TYPE IF EXISTS {BOOKING_MODE_ENUM}")
    for table in ("services", "tenant_settings", "bookings"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
