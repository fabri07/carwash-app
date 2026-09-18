"""Dominio del lavadero: catálogo, clientes, agenda, operación y dinero + RLS.

Revision ID: 0002_dominio
Revises: 0001_inicial
Create Date: 2026-09-18

Por qué (FASE-3-CONTRATO §1 y §5):

- **18 tablas nuevas**, todas con RLS (`ENABLE` + `FORCE` + `USING` = `WITH CHECK`) en esta misma
  migración (ADR-0002), y `tenants` suma `timezone` y `currency` (X1).
- **FKs compuestas** `(tenant_id, x_id) → padre(tenant_id, id)` (X4): el chequeo de FK no pasa
  por RLS, así que una FK simple dejaría a un lavadero apuntar a filas de otro conociendo el
  UUID. Por eso todo padre lleva `UNIQUE (tenant_id, id)`, incluido `users` (aditivo sobre F2).
- **Toda FK con índice** (X5): Postgres no las indexa solo.
- **`xc_bookings_sin_solapamiento`**: la garantía de no solapamiento de turnos vive en la base
  (`EXCLUDE USING gist`). Necesita `btree_gist` para el `=` sobre `uuid`; la extensión la crea
  el superusuario en `create_roles.sh` (X11), porque `carwash_owner` no tiene `CREATE` sobre la
  base. Esta migración solo verifica que exista y, si no, corta con un mensaje accionable.
- **`job_events` append-only** (X8): el runtime solo `SELECT, INSERT`, y un trigger rechaza
  `UPDATE`/`DELETE` aun al dueño.

Idempotente (A6): el `preDeployCommand` corre en cada deploy y un `alembic_version` atrasado no
puede abortarlo. Tablas con `_has_table`, índices y columnas con `if_not_exists`, tipos con
`duplicate_object`, y lo que Postgres no sabe crear con `IF NOT EXISTS` (EXCLUDE, CHECKs y FKs
sobre tablas existentes, el `UNIQUE` de `users`) con `DO $$ … IF NOT EXISTS (pg_constraint)`.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

from app.persistence.db.rls import (
    append_only_trigger,
    drop_append_only_trigger,
    enable_rls,
    grant_app_role,
    grant_app_role_append_only,
)

revision: str = "0002_dominio"
down_revision: str | None = "0001_inicial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BTREE_GIST_MISSING = (
    "falta la extensión btree_gist: correr backend/scripts/create_roles.sh "
    "como superusuario (ver railway.toml)"
)

# Congelados acá a propósito (como `role` en 0001): la migración describe el esquema que
# creó, no el Enum vivo. `test_esquema_dominio_pg.py` compara estos tipos contra
# `app/domain/enums.py`. Agregar un valor es una migración nueva.
VOID_REASONS = ("ERROR_DE_CARGA", "PEDIDO_DEL_USUARIO", "DESACTIVADO")
ENUMS: dict[str, tuple[str, ...]] = {
    "pricing_mode": ("PRECIO_FIJO", "A_COTIZAR"),
    "channel": (
        "TURNERO_WEB",
        "WHATSAPP",
        "INSTAGRAM",
        "REFERIDO",
        "CALLE",
        "CLIENTE_ANTERIOR",
        "CARGA_MANUAL",
        "OTRO",
    ),
    "plate_format": ("AR_1994", "MERCOSUR", "OTRO"),
    "booking_source": ("TURNERO_WEB", "PANEL", "AGENDA_INTERNA", "LISTA_ESPERA"),
    "booking_status": (
        "PENDIENTE_SEÑA",
        "PENDIENTE_COTIZACION",
        "CONFIRMADO",
        "RECIBIDO",
        "ATENDIDO",
        "VENCIDO",
        "CANCELADO_CLIENTE",
        "AUSENTE_CON_AVISO_POSTERIOR",
        "CANCELADO_OPERATIVO",
        "CANCELADO_DEMORA",
        "NO_ASISTIO",
    ),
    "job_status": (
        "PRESENTE",
        "EN_PROCESO",
        "FINALIZADO",
        "COBRADO",
        "RETIRADO",
        "CANCELADO_DEMORA",
    ),
    "job_event_type": (
        "JOB_RECEIVED",
        "JOB_STARTED",
        "JOB_FINISHED",
        "JOB_SETTLED",
        "JOB_PICKED_UP",
        "JOB_CANCELLED_DELAY",
        "DEPOSIT_RETAINED",
        "DEPOSIT_RETENTION_REVERSED",
        "PAYMENT_RECORDED",
        "PAYMENT_VOIDED",
        "INSPECTION_RECORDED",
        "PRICE_ADJUSTED",
    ),
    "dirt_level": ("NORMAL", "INTENSA", "BARRO", "ARENA", "PELOS_DE_MASCOTA", "TRABAJO_ESPECIAL"),
    "quote_status": ("PENDIENTE", "COTIZADO", "ACEPTADO", "RECHAZADO", "CANCELADO", "VENCIDO"),
    "supplies_purchaser": ("A_DEFINIR", "CLIENTE", "NEGOCIO", "NO_APLICA"),
    "cancellation_initiator": ("CLIENTE", "NEGOCIO"),
    "cancellation_classification": ("NORMAL", "TARDIA", "POSTERIOR_AL_TURNO", "OPERATIVA"),
    "deposit_status": (
        "SIN_PAGO",
        "DEVOLUCION_PENDIENTE",
        "EN_REVISION",
        "REPROGRAMACION_O_DEVOLUCION_PENDIENTE",
        "DEVUELTA",
        "RETENIDA",
        "REPROGRAMADA",
    ),
    "payment_kind": ("SEÑA", "SALDO", "DEVOLUCION"),
    "cash_direction": ("ENTRADA", "SALIDA"),
    "cash_movement_kind": ("COBRO", "DEVOLUCION", "AJUSTE"),
}

#: Tablas de tenant creadas acá, en orden de dependencia (el downgrade las borra al revés).
TENANT_TABLES = [
    "vehicle_sizes",
    "services",
    "service_prices",
    "resources",
    "business_hours",
    "payment_methods",
    "customers",
    "vehicles",
    "customer_vehicles",
    "bookings",
    "schedule_blocks",
    "quotes",
    "jobs",
    "job_events",
    "job_inspections",
    "payments",
    "cancellations",
    "cash_movements",
]
APPEND_ONLY_TABLES = ["job_events"]

ALIVE = "voided_at IS NULL"
CODE_PATTERN = "^[A-Z0-9_]{1,30}$"
BLOCKING_BOOKING = "'PENDIENTE_SEÑA', 'PENDIENTE_COTIZACION', 'CONFIRMADO', 'RECIBIDO'"
HOLD_BOOKING = "'PENDIENTE_SEÑA', 'PENDIENTE_COTIZACION'"
OPEN_QUOTE = "'PENDIENTE', 'COTIZADO', 'ACEPTADO'"
PRICED_QUOTE = "'COTIZADO', 'ACEPTADO'"
CLOSED_DEPOSIT = "'DEVUELTA', 'RETENIDA', 'REPROGRAMADA'"

BOOKING_OVERLAP = "xc_bookings_sin_solapamiento"
BOOKING_OVERLAP_SQL = (
    "EXCLUDE USING gist (tenant_id WITH =, resource_id WITH =, "
    "tstzrange(start_at, end_at, '[)') WITH &&) "
    f"WHERE (status IN ({BLOCKING_BOOKING}) AND {ALIVE})"
)


# ── Helpers congelados (no importan los del ORM: la migración no cambia si el ORM cambia) ──


def require_btree_gist(bind: Connection) -> None:
    """X11: la extensión la instala el superusuario. Sin ella el EXCLUDE no se puede crear."""
    found = bind.scalar(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'"))
    if found is None:
        raise RuntimeError(BTREE_GIST_MISSING)


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _create_type(name: str, values: tuple[str, ...]) -> None:
    joined = ", ".join(repr(v) for v in values)
    op.execute(
        f"DO $$ BEGIN CREATE TYPE {name} AS ENUM ({joined}); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
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


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def _uuid(name: str, *, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(name, sa.UUID(), nullable=nullable)


def _ts(name: str, *, nullable: bool = True) -> sa.Column[Any]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _text(name: str, *, nullable: bool = True, default: str | None = None) -> sa.Column[Any]:
    return sa.Column(name, sa.Text(), nullable=nullable, server_default=default)


def _int(name: str, *, nullable: bool = False, default: int | None = None) -> sa.Column[Any]:
    server_default = None if default is None else str(default)
    return sa.Column(name, sa.Integer(), nullable=nullable, server_default=server_default)


def _cents(name: str, *, nullable: bool = False, default: int | None = None) -> sa.Column[Any]:
    server_default = None if default is None else str(default)
    return sa.Column(name, sa.BigInteger(), nullable=nullable, server_default=server_default)


def _check(table: str, name: str, sql: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(sql, name=op.f(f"ck_{table}_{name}"))


def _fk(table: str, column: str, parent: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{parent}.tenant_id", f"{parent}.id"],
        name=op.f(f"fk_{table}_tenant_id_{column}_{parent}"),
        ondelete="RESTRICT",
    )


def _parent_key(table: str) -> sa.UniqueConstraint:
    return sa.UniqueConstraint("tenant_id", "id", name=op.f(f"uq_{table}_tenant_id_id"))


def _base(table: str, *, voidable: bool = True) -> list[Any]:
    """`id`, `tenant_id` (+ FK a tenants) y timestamps; anulación con su CHECK si `voidable`."""
    items: list[Any] = [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f(f"fk_{table}_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
    ]
    if voidable:
        items += [
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
            _check(table, "void_coherente", "(voided_at IS NULL) = (void_reason IS NULL)"),
        ]
    return items


def _create_table(table: str, *items: Any, voidable: bool = True) -> None:
    if not _has_table(table):
        op.create_table(table, *items, *_base(table, voidable=voidable))
    op.create_index(op.f(f"ix_{table}_tenant_id"), table, ["tenant_id"], if_not_exists=True)


def _index(table: str, *columns: str) -> None:
    op.create_index(
        f"ix_{table}_tenant_id_{'_'.join(columns)}",
        table,
        ["tenant_id", *columns],
        if_not_exists=True,
    )


def _unique_alive(table: str, *columns: str, where: str | None = None) -> None:
    condition = ALIVE if where is None else f"{where} AND {ALIVE}"
    op.create_index(
        f"ux_{table}_tenant_id_{'_'.join(columns)}",
        table,
        ["tenant_id", *columns],
        unique=True,
        postgresql_where=sa.text(condition),
        if_not_exists=True,
    )


# ── Upgrade ───────────────────────────────────────────────────────────────────


def upgrade() -> None:
    require_btree_gist(op.get_bind())

    for name, values in ENUMS.items():
        _create_type(name, values)

    # ── Cambios aditivos sobre F2 ────────────────────────────────────────────
    op.add_column(
        "tenants",
        sa.Column(
            "timezone", sa.Text(), server_default="America/Argentina/Cordoba", nullable=False
        ),
        if_not_exists=True,
    )
    op.add_column(
        "tenants",
        sa.Column("currency", sa.Text(), server_default="ARS", nullable=False),
        if_not_exists=True,
    )
    _add_constraint("tenants", "ck_tenants_currency_iso", "CHECK (currency ~ '^[A-Z]{3}$')")
    _add_constraint("users", "uq_users_tenant_id_id", "UNIQUE (tenant_id, id)")

    # ── 1.1 Catálogo ─────────────────────────────────────────────────────────
    t = "vehicle_sizes"
    _create_table(
        t,
        _text("code", nullable=False),
        _text("label", nullable=False),
        _int("sort_order", default=0),
        _parent_key(t),
        _check(t, "code_formato", f"code ~ '{CODE_PATTERN}'"),
    )
    _unique_alive(t, "code")

    t = "services"
    _create_table(
        t,
        _text("name", nullable=False),
        sa.Column("pricing_mode", _enum("pricing_mode"), nullable=False),
        _text("notes"),
        _parent_key(t),
    )
    _unique_alive(t, "name")

    t = "service_prices"
    _create_table(
        t,
        _uuid("service_id"),
        _uuid("vehicle_size_id"),
        _cents("price_cents", nullable=True),
        _int("duration_min"),
        _int("deposit_bps", default=0),
        _fk(t, "service_id", "services"),
        _fk(t, "vehicle_size_id", "vehicle_sizes"),
        _check(t, "precio_positivo", "price_cents IS NULL OR price_cents > 0"),
        _check(t, "duracion_positiva", "duration_min > 0"),
        _check(t, "sena_bps_rango", "deposit_bps BETWEEN 0 AND 10000"),
    )
    _index(t, "service_id")
    _index(t, "vehicle_size_id")
    _unique_alive(t, "service_id", "vehicle_size_id")

    t = "resources"
    _create_table(
        t,
        _text("name", nullable=False),
        _int("sort_order", default=0),
        _parent_key(t),
    )
    _unique_alive(t, "name")

    t = "business_hours"
    _create_table(
        t,
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("opens_at", sa.Time(), nullable=False),
        sa.Column("closes_at", sa.Time(), nullable=False),
        _check(t, "weekday_iso", "weekday BETWEEN 1 AND 7"),
        _check(t, "franja_valida", "closes_at > opens_at"),
    )
    _index(t, "weekday")

    t = "payment_methods"
    _create_table(
        t,
        _text("code", nullable=False),
        _text("label", nullable=False),
        _int("commission_bps", default=0),
        _text("settlement_account"),
        sa.Column("for_income", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("for_expense", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _parent_key(t),
        _check(t, "code_formato", f"code ~ '{CODE_PATTERN}'"),
        _check(t, "comision_bps_rango", "commission_bps BETWEEN 0 AND 10000"),
    )
    _unique_alive(t, "code")

    # ── 1.2 Clientes y vehículos ─────────────────────────────────────────────
    t = "customers"
    _create_table(
        t,
        _text("name", nullable=False),
        _text("phone_e164"),
        _text("phone_raw"),
        _text("email"),
        sa.Column("acquisition_channel", _enum("channel"), nullable=True),
        _text("notes"),
        sa.Column("legacy_ids", postgresql.ARRAY(sa.Text()), nullable=True),
        _parent_key(t),
        _check(t, "nombre_largo", "length(name) BETWEEN 1 AND 80"),
        _check(t, "telefono_e164", "phone_e164 ~ '^\\+[1-9][0-9]{7,14}$'"),
    )
    _unique_alive(t, "phone_e164", where="phone_e164 IS NOT NULL")

    t = "vehicles"
    _create_table(
        t,
        _text("plate"),
        _text("plate_normalized"),
        sa.Column("plate_format", _enum("plate_format"), nullable=True),
        _uuid("vehicle_size_id", nullable=True),
        _text("brand_model"),
        _text("color"),
        _text("notes"),
        _text("legacy_id"),
        _parent_key(t),
        _fk(t, "vehicle_size_id", "vehicle_sizes"),
        _check(t, "patente_normalizada", "plate_normalized ~ '^[A-Z0-9]{1,10}$'"),
        _check(t, "marca_modelo_largo", "length(brand_model) <= 40"),
    )
    _index(t, "vehicle_size_id")
    _unique_alive(t, "plate_normalized", where="plate_normalized IS NOT NULL")

    t = "customer_vehicles"
    _create_table(
        t,
        _uuid("customer_id"),
        _uuid("vehicle_id"),
        sa.Column(
            "valid_from", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _ts("valid_to"),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _fk(t, "customer_id", "customers"),
        _fk(t, "vehicle_id", "vehicles"),
        _check(t, "vigencia_valida", "valid_to IS NULL OR valid_to > valid_from"),
    )
    _index(t, "customer_id")
    _index(t, "vehicle_id")
    _unique_alive(t, "customer_id", "vehicle_id", where="valid_to IS NULL")

    # ── 1.3 Agenda ───────────────────────────────────────────────────────────
    t = "bookings"
    _create_table(
        t,
        _text("code", nullable=False),
        sa.Column("source", _enum("booking_source"), nullable=False),
        sa.Column("channel", _enum("channel"), nullable=False),
        sa.Column("status", _enum("booking_status"), nullable=False),
        _uuid("resource_id"),
        _ts("start_at", nullable=False),
        _ts("end_at", nullable=False),
        _ts("hold_expires_at"),
        _uuid("customer_id"),
        _uuid("vehicle_id", nullable=True),
        _uuid("service_id"),
        _uuid("vehicle_size_id"),
        _text("service_name_snapshot", nullable=False),
        _int("duration_min"),
        _cents("price_cents", nullable=True),
        _int("deposit_bps", default=0),
        _cents("deposit_required_cents", default=0),
        _uuid("quote_id", nullable=True),
        _text("terms_version"),
        _ts("terms_accepted_at"),
        _text("notes"),
        _text("legacy_id"),
        _parent_key(t),
        _fk(t, "resource_id", "resources"),
        _fk(t, "customer_id", "customers"),
        _fk(t, "vehicle_id", "vehicles"),
        _fk(t, "service_id", "services"),
        _fk(t, "vehicle_size_id", "vehicle_sizes"),
        # `quote_id → quotes` se agrega después de crear `quotes` (ciclo con quotes.booking_id).
        _check(t, "rango_valido", "end_at > start_at"),
        _check(
            t,
            "hold_obligatorio",
            f"status NOT IN ({HOLD_BOOKING}) OR hold_expires_at IS NOT NULL",
        ),
        _check(t, "duracion_positiva", "duration_min > 0"),
        _check(t, "precio_positivo", "price_cents IS NULL OR price_cents > 0"),
        _check(t, "sena_bps_rango", "deposit_bps BETWEEN 0 AND 10000"),
        _check(t, "sena_no_negativa", "deposit_required_cents >= 0"),
        _check(t, "sin_precio_sin_sena", "price_cents IS NOT NULL OR deposit_required_cents = 0"),
        _check(t, "notas_largo", "length(notes) <= 500"),
    )
    _add_constraint(t, BOOKING_OVERLAP, BOOKING_OVERLAP_SQL)
    _index(t, "resource_id", "start_at")
    _index(t, "customer_id")
    _index(t, "vehicle_id")
    _index(t, "service_id")
    _index(t, "vehicle_size_id")
    _index(t, "quote_id")
    _unique_alive(t, "code")

    t = "schedule_blocks"
    _create_table(
        t,
        _uuid("resource_id", nullable=True),
        _ts("starts_at", nullable=False),
        _ts("ends_at", nullable=False),
        _text("reason", nullable=False, default="Bloqueo operativo"),
        _uuid("created_by_user_id"),
        _fk(t, "resource_id", "resources"),
        _fk(t, "created_by_user_id", "users"),
        _check(t, "rango_valido", "ends_at > starts_at"),
    )
    _index(t, "resource_id")
    _index(t, "created_by_user_id")

    # ── 1.4 Operación ────────────────────────────────────────────────────────
    t = "quotes"
    _create_table(
        t,
        _uuid("customer_id"),
        _uuid("vehicle_id", nullable=True),
        _uuid("service_id"),
        _uuid("vehicle_size_id"),
        _uuid("booking_id", nullable=True),
        sa.Column("status", _enum("quote_status"), nullable=False),
        _ts("requested_at", nullable=False),
        _cents("agreed_price_cents", nullable=True),
        _int("agreed_duration_min", nullable=True),
        sa.Column(
            "supplies_purchaser",
            _enum("supplies_purchaser"),
            server_default="A_DEFINIR",
            nullable=False,
        ),
        _cents("supplies_cost_cents", nullable=True),
        _ts("expires_at"),
        _ts("quoted_at"),
        _ts("decided_at"),
        _uuid("decided_by_user_id", nullable=True),
        _text("notes"),
        _text("legacy_id"),
        _parent_key(t),
        _fk(t, "customer_id", "customers"),
        _fk(t, "vehicle_id", "vehicles"),
        _fk(t, "service_id", "services"),
        _fk(t, "vehicle_size_id", "vehicle_sizes"),
        _fk(t, "booking_id", "bookings"),
        _fk(t, "decided_by_user_id", "users"),
        _check(t, "precio_positivo", "agreed_price_cents IS NULL OR agreed_price_cents > 0"),
        _check(t, "duracion_positiva", "agreed_duration_min IS NULL OR agreed_duration_min > 0"),
        _check(
            t,
            "cotizado_completo",
            f"status NOT IN ({PRICED_QUOTE}) "
            "OR (agreed_price_cents IS NOT NULL AND agreed_duration_min IS NOT NULL)",
        ),
        _check(
            t, "insumos_no_negativos", "supplies_cost_cents IS NULL OR supplies_cost_cents >= 0"
        ),
    )
    for column in ("customer_id", "vehicle_id", "service_id", "vehicle_size_id", "booking_id"):
        _index(t, column)
    _index(t, "decided_by_user_id")
    _unique_alive(t, "booking_id", where=f"booking_id IS NOT NULL AND status IN ({OPEN_QUOTE})")

    _add_constraint(
        "bookings",
        "fk_bookings_tenant_id_quote_id_quotes",
        "FOREIGN KEY (tenant_id, quote_id) REFERENCES quotes (tenant_id, id) ON DELETE RESTRICT",
    )

    t = "jobs"
    _create_table(
        t,
        _uuid("booking_id", nullable=True),
        _uuid("quote_id", nullable=True),
        _uuid("customer_id", nullable=True),
        _uuid("vehicle_id"),
        _uuid("vehicle_size_id"),
        _uuid("service_id"),
        _uuid("resource_id", nullable=True),
        _uuid("responsible_user_id"),
        sa.Column("channel", _enum("channel"), nullable=False),
        sa.Column("status", _enum("job_status"), nullable=False),
        _text("service_name_snapshot", nullable=False),
        _cents("base_price_cents"),
        _cents("surcharge_cents", default=0),
        _cents("discount_cents", default=0),
        _text("discount_reason"),
        _cents("deposit_required_cents", default=0),
        _ts("scheduled_at"),
        _ts("arrived_at", nullable=False),
        _int("arrival_delay_min", nullable=True),
        _ts("started_at"),
        _ts("finished_at"),
        _ts("settled_at"),
        _ts("picked_up_at"),
        _text("notes"),
        _text("legacy_id"),
        sa.Column("legacy_review_flags", postgresql.ARRAY(sa.Text()), nullable=True),
        _parent_key(t),
        _fk(t, "booking_id", "bookings"),
        _fk(t, "quote_id", "quotes"),
        _fk(t, "customer_id", "customers"),
        _fk(t, "vehicle_id", "vehicles"),
        _fk(t, "vehicle_size_id", "vehicle_sizes"),
        _fk(t, "service_id", "services"),
        _fk(t, "resource_id", "resources"),
        _fk(t, "responsible_user_id", "users"),
        _check(t, "precio_base_positivo", "base_price_cents > 0"),
        _check(t, "recargo_no_negativo", "surcharge_cents >= 0"),
        _check(t, "descuento_no_negativo", "discount_cents >= 0"),
        _check(t, "descuento_con_motivo", "discount_cents = 0 OR discount_reason IS NOT NULL"),
        _check(t, "total_positivo", "base_price_cents + surcharge_cents - discount_cents > 0"),
        _check(t, "sena_no_negativa", "deposit_required_cents >= 0"),
    )
    for column in (
        "booking_id",
        "quote_id",
        "customer_id",
        "vehicle_id",
        "vehicle_size_id",
        "service_id",
        "resource_id",
        "responsible_user_id",
    ):
        _index(t, column)
    _unique_alive(t, "booking_id", where="booking_id IS NOT NULL")
    _unique_alive(t, "legacy_id", where="legacy_id IS NOT NULL")

    t = "job_events"
    _create_table(
        t,
        _uuid("job_id"),
        sa.Column("event_type", _enum("job_event_type"), nullable=False),
        sa.Column("from_status", _enum("job_status"), nullable=True),
        sa.Column("to_status", _enum("job_status"), nullable=True),
        _ts("occurred_at", nullable=False),
        _uuid("actor_user_id"),
        _text("idempotency_key", nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        _fk(t, "job_id", "jobs"),
        _fk(t, "actor_user_id", "users"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name=op.f("uq_job_events_tenant_id_idempotency_key")
        ),
        _check(
            t,
            "estados_coherentes",
            "(from_status IS NULL AND to_status IS NULL) OR to_status IS NOT NULL",
        ),
        # D-001.4. `coalesce`: sin él un `reason` ausente da NULL y el CHECK lo dejaría pasar.
        _check(
            t,
            "reversion_con_motivo",
            "event_type <> 'DEPOSIT_RETENTION_REVERSED' "
            "OR length(btrim(coalesce(metadata->>'reason', ''))) > 0",
        ),
        voidable=False,
    )
    _index(t, "job_id", "occurred_at")
    _index(t, "actor_user_id")
    for statement in append_only_trigger(t):
        op.execute(statement)

    t = "job_inspections"
    _create_table(
        t,
        _uuid("job_id"),
        sa.Column("dirt_level", _enum("dirt_level"), nullable=True),
        _text("pre_existing_damage"),
        _text("valuables"),
        sa.Column("photo_consent", sa.Boolean(), nullable=True),
        sa.Column("checklist", postgresql.JSONB(), nullable=True),
        _uuid("inspected_by_user_id", nullable=True),
        _ts("inspected_at"),
        _fk(t, "job_id", "jobs"),
        _fk(t, "inspected_by_user_id", "users"),
    )
    _index(t, "job_id")
    _index(t, "inspected_by_user_id")
    _unique_alive(t, "job_id")

    # ── 1.5 Dinero ───────────────────────────────────────────────────────────
    t = "payments"
    _create_table(
        t,
        _uuid("job_id", nullable=True),
        _uuid("booking_id", nullable=True),
        sa.Column("kind", _enum("payment_kind"), nullable=False),
        _cents("amount_cents"),
        _uuid("payment_method_id"),
        _int("commission_bps"),
        _cents("commission_cents"),
        _ts("occurred_at", nullable=False),
        _uuid("actor_user_id"),
        _text("idempotency_key", nullable=False),
        _text("reference"),
        _text("notes"),
        _text("legacy_id"),
        sa.Column("legacy_review_flags", postgresql.ARRAY(sa.Text()), nullable=True),
        _parent_key(t),
        _fk(t, "job_id", "jobs"),
        _fk(t, "booking_id", "bookings"),
        _fk(t, "payment_method_id", "payment_methods"),
        _fk(t, "actor_user_id", "users"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name=op.f("uq_payments_tenant_id_idempotency_key")
        ),
        _check(t, "con_job_o_turno", "job_id IS NOT NULL OR booking_id IS NOT NULL"),
        _check(t, "importe_positivo", "amount_cents > 0"),
        _check(t, "comision_bps_rango", "commission_bps BETWEEN 0 AND 10000"),
        _check(t, "comision_no_negativa", "commission_cents >= 0"),
    )
    for column in ("job_id", "booking_id", "payment_method_id", "actor_user_id"):
        _index(t, column)

    t = "cancellations"
    _create_table(
        t,
        _uuid("booking_id"),
        sa.Column("initiator", _enum("cancellation_initiator"), nullable=False),
        sa.Column("classification", _enum("cancellation_classification"), nullable=False),
        _ts("requested_at", nullable=False),
        _int("anticipation_min"),
        _text("reason", nullable=False, default="Prefiere no informarlo"),
        sa.Column("previous_status", _enum("booking_status"), nullable=False),
        sa.Column("resulting_status", _enum("booking_status"), nullable=False),
        _cents("deposit_paid_cents"),
        sa.Column("deposit_status", _enum("deposit_status"), nullable=False),
        _ts("resolved_at"),
        _uuid("resolved_by_user_id", nullable=True),
        _text("resolution_reason"),
        _uuid("refund_payment_id", nullable=True),
        _uuid("actor_user_id", nullable=True),
        _text("terms_version"),
        _fk(t, "booking_id", "bookings"),
        _fk(t, "resolved_by_user_id", "users"),
        _fk(t, "refund_payment_id", "payments"),
        _fk(t, "actor_user_id", "users"),
        sa.UniqueConstraint(
            "tenant_id", "booking_id", name=op.f("uq_cancellations_tenant_id_booking_id")
        ),
        _check(t, "motivo_largo", "length(reason) <= 160"),
        _check(t, "sena_no_negativa", "deposit_paid_cents >= 0"),
        _check(
            t,
            "cierre_con_actor",
            f"deposit_status NOT IN ({CLOSED_DEPOSIT}) "
            "OR (resolved_at IS NOT NULL AND resolved_by_user_id IS NOT NULL)",
        ),
        _check(
            t,
            "cierre_con_motivo",
            f"deposit_status NOT IN ({CLOSED_DEPOSIT}) "
            "OR length(btrim(coalesce(resolution_reason, ''))) > 0",
        ),
        _check(
            t,
            "devuelta_con_pago",
            "deposit_status <> 'DEVUELTA' OR refund_payment_id IS NOT NULL",
        ),
    )
    for column in ("resolved_by_user_id", "refund_payment_id", "actor_user_id"):
        _index(t, column)

    t = "cash_movements"
    _create_table(
        t,
        sa.Column("direction", _enum("cash_direction"), nullable=False),
        sa.Column("kind", _enum("cash_movement_kind"), nullable=False),
        _cents("amount_cents"),
        _uuid("payment_method_id"),
        _uuid("payment_id", nullable=True),
        _ts("occurred_at", nullable=False),
        _uuid("actor_user_id"),
        _text("idempotency_key", nullable=False),
        _text("notes"),
        _fk(t, "payment_method_id", "payment_methods"),
        _fk(t, "payment_id", "payments"),
        _fk(t, "actor_user_id", "users"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name=op.f("uq_cash_movements_tenant_id_idempotency_key"),
        ),
        _check(t, "importe_positivo", "amount_cents > 0"),
    )
    for column in ("payment_method_id", "payment_id", "actor_user_id"):
        _index(t, column)
    _unique_alive(t, "payment_id", where="payment_id IS NOT NULL")

    # ── RLS (ADR-0002) y permisos del runtime ────────────────────────────────
    for table in TENANT_TABLES:
        for statement in enable_rls(table):
            op.execute(statement)
    writable = [t for t in TENANT_TABLES if t not in APPEND_ONLY_TABLES]
    for statement in grant_app_role(writable):
        op.execute(statement)
    for statement in grant_app_role_append_only(APPEND_ONLY_TABLES):
        op.execute(statement)


# ── Downgrade ─────────────────────────────────────────────────────────────────


def downgrade() -> None:
    # Las políticas, índices, CHECKs y el EXCLUDE caen con cada tabla.
    for statement in drop_append_only_trigger("job_events"):
        op.execute(statement)
    op.drop_constraint(
        op.f("fk_bookings_tenant_id_quote_id_quotes"), "bookings", type_="foreignkey"
    )
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
    for name in reversed(ENUMS):
        op.execute(f"DROP TYPE {name}")
    op.drop_constraint(op.f("uq_users_tenant_id_id"), "users", type_="unique")
    op.drop_constraint(op.f("ck_tenants_currency_iso"), "tenants", type_="check")
    op.drop_column("tenants", "currency")
    op.drop_column("tenants", "timezone")
