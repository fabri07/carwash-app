"""Configuración del negocio y precio siempre presente.

Revision ID: 0005_configuracion_precios
Revises: 0004_cuentas_permisos
Create Date: 2026-11-04

Por qué (FASE-4-CONTRATO §3.2, §5 y adenda §11):

- **`tenants.slug`** (NN, único entre vivos): la URL pública del turnero (F5).
- **`tenant_settings`** (nueva, con RLS, 1:1 con el tenant): seña global (D4-4), umbrales que en
  F3 eran parámetros sueltos, vigencia de la cotización (D4-16) y los datos del negocio.
- **`service_prices`**: sale `deposit_bps` (la seña es global) y `price_cents` pasa a NOT NULL
  (D4-6: todo servicio tiene precio base por tamaño).
- **`booking_items`** y **`job_items`**: suman `pricing_mode` (snapshot), así el ítem a cotizar
  se reconoce sin depender de que su precio sea NULL. `booking_items.price_cents` pasa a NOT NULL.
- **`bookings`** (D4-13, D4-15): un turno a cotizar no ocupa el puesto ni tiene hold. Sale
  `PENDIENTE_COTIZACION` del `EXCLUDE` y del CHECK de hold (el `WHERE` de un `EXCLUDE` no se
  altera: se recrea).

**Corta antes de alterar nada** si queda algún precio NULL en `service_prices`, `booking_items` o
`job_items`, y lista las filas. En staging se prepara antes con `scripts/prepare_staging_0005.py`
(PR 4.2.0). Las tablas tienen `FORCE ROW LEVEL SECURITY`: el dueño de la migración no ve filas sin
tenant en contexto, así que el chequeo y los rellenos corren con `NO FORCE` y se vuelve a forzar
antes de terminar, todo en la misma transacción (si algo falla, el rollback deja todo como estaba).

Idempotente (A6), como 0003 y 0004.

**Downgrade con pérdida, dicho acá:** la seña pasa a ser global, así que al bajar `deposit_bps`
vuelve a 0 en todos los precios y en todos los ítems, y los ítems a cotizar conservan el precio
base (0004 los tenía en NULL). Corta, con un mensaje que dice por qué, si hay turnos
`PENDIENTE_COTIZACION` sin hold (0004 lo exige).
"""

import re
import unicodedata
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.persistence.db.rls import enable_rls, grant_app_role

revision: str = "0005_configuracion_precios"
down_revision: str | None = "0004_cuentas_permisos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALIVE = "voided_at IS NULL"
LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"
TENANT_SETTINGS = "tenant_settings"

# Congelados acá: la migración describe el esquema que creó, no el código vivo.
SLUG_PATTERN = "^[a-z0-9-]{3,40}$"
SLUG_MAX = 40
SLUG_FALLBACK = "negocio"
SLUG_RESERVED = frozenset(
    {
        "admin",
        "api",
        "app",
        "auth",
        "cambiar-clave",
        "configuracion",
        "dashboard",
        "debug",
        "health",
        "login",
        "logout",
        "onboarding",
        "ready",
        "registro",
        "reservas",
        "static",
        "turnos",
        "v1",
        "www",
    }
)
# `tests/persistence/test_settings_defaults.py` compara estos valores con el modelo.
DEFAULTS = {
    "deposit_bps": 2000,
    "tolerance_min": 20,
    "late_cancel_threshold_min": 720,
    "hold_min": 30,
    "min_advance_min": 5,
    "quote_validity_hours": 24,
}
PALETTE_DEFAULT = "default"
FONT_DEFAULT = "default"

BOOKING_OVERLAP = "xc_bookings_sin_solapamiento"
ALIVE_EXCLUDE = (
    "EXCLUDE USING gist (tenant_id WITH =, resource_id WITH =, "
    "tstzrange(start_at, end_at, '[)') WITH &&) "
)
BLOCKING_BEFORE = "'PENDIENTE_SEÑA', 'PENDIENTE_COTIZACION', 'CONFIRMADO', 'RECIBIDO'"
BLOCKING_AFTER = "'PENDIENTE_SEÑA', 'CONFIRMADO', 'RECIBIDO'"
HOLD_BEFORE = "'PENDIENTE_SEÑA', 'PENDIENTE_COTIZACION'"
HOLD_AFTER = "'PENDIENTE_SEÑA'"

#: Tablas con RLS forzado que esta migración lee o rellena.
FORCED = (
    "tenants",
    "services",
    "service_prices",
    "booking_items",
    "job_items",
    "bookings",
)


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(op.get_bind()).get_columns(table))


def _add_constraint(table: str, name: str, definition: str) -> None:
    """`ADD CONSTRAINT` idempotente: Postgres no tiene `ADD CONSTRAINT IF NOT EXISTS`."""
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_constraint "
        f"WHERE conname = '{name}' AND conrelid = 'public.{table}'::regclass) THEN "
        f"ALTER TABLE {table} ADD CONSTRAINT {name} {definition}; "
        "END IF; END $$"
    )


def _replace_constraint(table: str, name: str, definition: str) -> None:
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} {definition}")


def _slugify(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")[:SLUG_MAX].strip("-")


def _slug_for(name: str, tenant_id: uuid.UUID) -> str:
    """Nombre + sufijo corto (los 6 primeros hex del id): único sin consultar a los demás."""
    base = _slugify(name)
    if len(base) < 3 or base in SLUG_RESERVED:
        base = SLUG_FALLBACK
    tail = f"-{tenant_id.hex[:6]}"
    return f"{base[: SLUG_MAX - len(tail)].rstrip('-')}{tail}"


def _force(on: bool) -> None:
    for table in (*FORCED, TENANT_SETTINGS):
        if table == TENANT_SETTINGS and not _has_table(TENANT_SETTINGS):
            continue
        op.execute(f"ALTER TABLE {table} {'FORCE' if on else 'NO FORCE'} ROW LEVEL SECURITY")


def _preflight() -> None:
    """Corta, antes de alterar nada, si queda un precio NULL. Lista hasta 20 filas.

    Corre con `NO FORCE`: con RLS forzado el dueño vería 0 filas y el chequeo pasaría en falso.
    """
    op.execute(
        """
        DO $$
        DECLARE
            filas text;
            total integer;
        BEGIN
            SELECT count(*), string_agg(origen || ':' || id::text, ', ')
              INTO total, filas
              FROM (
                SELECT 'service_prices' AS origen, id FROM service_prices
                 WHERE price_cents IS NULL
                UNION ALL
                SELECT 'booking_items', id FROM booking_items WHERE price_cents IS NULL
                UNION ALL
                SELECT 'job_items', id FROM job_items WHERE price_cents IS NULL
                LIMIT 20
              ) AS pendientes;
            IF total > 0 THEN
                RAISE EXCEPTION
                    '0005: hay precios NULL (primeras filas: %). Completarlos antes de migrar: '
                    'en staging, `python scripts/prepare_staging_0005.py`.', filas;
            END IF;
        END $$
        """
    )


def _tenants_slug() -> None:
    op.add_column("tenants", sa.Column("slug", sa.Text(), nullable=True), if_not_exists=True)
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, name FROM tenants WHERE slug IS NULL")).all()
    for raw_id, name in rows:
        tenant_id = uuid.UUID(str(raw_id))
        bind.execute(
            sa.text("UPDATE tenants SET slug = :slug WHERE id = :id"),
            {"slug": _slug_for(name, tenant_id), "id": tenant_id},
        )
    op.alter_column("tenants", "slug", existing_type=sa.Text(), nullable=False)
    _add_constraint("tenants", "ck_tenants_slug_formato", f"CHECK (slug ~ '{SLUG_PATTERN}')")
    op.create_index(
        "ux_tenants_slug",
        "tenants",
        ["slug"],
        unique=True,
        postgresql_where=sa.text(ALIVE),
        if_not_exists=True,
    )


def _create_tenant_settings() -> None:
    if not _has_table(TENANT_SETTINGS):
        t = TENANT_SETTINGS
        op.create_table(
            t,
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("tenant_id", sa.UUID(), nullable=False),
            sa.Column("address", sa.Text(), nullable=True),
            sa.Column("whatsapp_e164", sa.Text(), nullable=True),
            sa.Column("instagram_url", sa.Text(), nullable=True),
            sa.Column("welcome_text", sa.Text(), nullable=True),
            sa.Column("cancellation_policy", sa.Text(), nullable=True),
            sa.Column(
                "deposit_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
            ),
            *(
                sa.Column(name, sa.Integer(), server_default=str(value), nullable=False)
                for name, value in DEFAULTS.items()
            ),
            sa.Column("palette", sa.Text(), server_default=PALETTE_DEFAULT, nullable=False),
            sa.Column("font", sa.Text(), server_default=FONT_DEFAULT, nullable=False),
            sa.Column("logo_key", sa.Text(), nullable=True),
            sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
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
            sa.PrimaryKeyConstraint("id", name=f"pk_{t}"),
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["tenants.id"],
                name=f"fk_{t}_tenant_id_tenants",
                ondelete="RESTRICT",
            ),
            sa.UniqueConstraint("tenant_id", name=f"uq_{t}_tenant_id"),
            sa.CheckConstraint(
                "welcome_text IS NULL OR length(welcome_text) <= 280",
                name=op.f(f"ck_{t}_bienvenida_largo"),
            ),
            sa.CheckConstraint(
                "whatsapp_e164 IS NULL OR whatsapp_e164 ~ '^\\+[1-9][0-9]{7,14}$'",
                name=op.f(f"ck_{t}_whatsapp_e164"),
            ),
            sa.CheckConstraint(
                "deposit_bps BETWEEN 1 AND 10000", name=op.f(f"ck_{t}_sena_bps_rango")
            ),
            sa.CheckConstraint("tolerance_min > 0", name=op.f(f"ck_{t}_tolerancia_positiva")),
            sa.CheckConstraint(
                "late_cancel_threshold_min > 0", name=op.f(f"ck_{t}_umbral_tardia_positivo")
            ),
            sa.CheckConstraint("hold_min > 0", name=op.f(f"ck_{t}_retencion_positiva")),
            sa.CheckConstraint(
                "min_advance_min >= 0", name=op.f(f"ck_{t}_anticipacion_no_negativa")
            ),
            sa.CheckConstraint(
                "quote_validity_hours > 0", name=op.f(f"ck_{t}_vigencia_cotizacion_positiva")
            ),
            sa.CheckConstraint(
                "palette ~ '^[a-z0-9_-]{1,40}$'", name=op.f(f"ck_{t}_paleta_formato")
            ),
            sa.CheckConstraint("font ~ '^[a-z0-9_-]{1,40}$'", name=op.f(f"ck_{t}_fuente_formato")),
        )
    op.create_index(
        f"ix_{TENANT_SETTINGS}_tenant_id", TENANT_SETTINGS, ["tenant_id"], if_not_exists=True
    )
    for statement in [*enable_rls(TENANT_SETTINGS), *grant_app_role([TENANT_SETTINGS])]:
        op.execute(statement)
    # Una fila por tenant existente. El resto de las columnas toma su default.
    op.execute(f"ALTER TABLE {TENANT_SETTINGS} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"INSERT INTO {TENANT_SETTINGS} (id, tenant_id) "
        "SELECT gen_random_uuid(), t.id FROM tenants AS t "
        f"WHERE NOT EXISTS (SELECT 1 FROM {TENANT_SETTINGS} AS s WHERE s.tenant_id = t.id)"
    )


def _service_prices() -> None:
    op.execute(
        "ALTER TABLE service_prices DROP CONSTRAINT IF EXISTS ck_service_prices_sena_bps_rango"
    )
    op.execute("ALTER TABLE service_prices DROP COLUMN IF EXISTS deposit_bps")
    op.alter_column("service_prices", "price_cents", existing_type=sa.BigInteger(), nullable=False)
    _replace_constraint(
        "service_prices", "ck_service_prices_precio_positivo", "CHECK (price_cents > 0)"
    )


def _items_pricing_mode(table: str) -> None:
    op.add_column(
        table,
        sa.Column(
            "pricing_mode",
            postgresql.ENUM("PRECIO_FIJO", "A_COTIZAR", name="pricing_mode", create_type=False),
            nullable=True,
        ),
        if_not_exists=True,
    )
    op.execute(
        f"UPDATE {table} AS i SET pricing_mode = s.pricing_mode "
        "FROM services AS s "
        "WHERE s.tenant_id = i.tenant_id AND s.id = i.service_id AND i.pricing_mode IS NULL"
    )
    op.alter_column(
        table,
        "pricing_mode",
        existing_type=postgresql.ENUM(
            "PRECIO_FIJO", "A_COTIZAR", name="pricing_mode", create_type=False
        ),
        nullable=False,
    )


def _booking_items() -> None:
    _items_pricing_mode("booking_items")
    op.alter_column("booking_items", "price_cents", existing_type=sa.BigInteger(), nullable=False)
    op.execute(
        "ALTER TABLE booking_items DROP CONSTRAINT IF EXISTS ck_booking_items_sin_precio_sin_sena"
    )
    _replace_constraint(
        "booking_items", "ck_booking_items_precio_positivo", "CHECK (price_cents > 0)"
    )


def _bookings() -> None:
    """D4-13 y D4-15: sin `PENDIENTE_COTIZACION` en el `EXCLUDE` ni en el CHECK de hold.

    Los turnos a cotizar que ya existen conservan su `hold_expires_at` (inofensivo: ya nada lo
    mira). Su `price_cents` NULL pasa a ser la suma de los precios base de sus ítems.
    """
    op.execute(f"ALTER TABLE bookings DROP CONSTRAINT IF EXISTS {BOOKING_OVERLAP}")
    op.execute(
        f"ALTER TABLE bookings ADD CONSTRAINT {BOOKING_OVERLAP} {ALIVE_EXCLUDE}"
        f"WHERE (status IN ({BLOCKING_AFTER}) AND {ALIVE})"
    )
    _replace_constraint(
        "bookings",
        "ck_bookings_hold_obligatorio",
        f"CHECK (status NOT IN ({HOLD_AFTER}) OR hold_expires_at IS NOT NULL)",
    )
    op.execute(
        "UPDATE bookings AS b SET price_cents = s.total FROM ("
        "SELECT tenant_id, booking_id, sum(price_cents) AS total FROM booking_items "
        f"WHERE {ALIVE} GROUP BY tenant_id, booking_id) AS s "
        "WHERE s.tenant_id = b.tenant_id AND s.booking_id = b.id AND b.price_cents IS NULL"
    )


def upgrade() -> None:
    op.execute(LOCK_TIMEOUT)
    _force(False)
    _preflight()
    _tenants_slug()
    _create_tenant_settings()
    _service_prices()
    _booking_items()
    _items_pricing_mode("job_items")
    _bookings()
    _force(True)


def downgrade() -> None:
    """Vuelve al esquema de 0004. Ver el docstring del módulo: la baja pierde la seña por precio."""
    op.execute(LOCK_TIMEOUT)
    _force(False)
    op.execute(
        """
        DO $$
        DECLARE
            sin_hold integer;
        BEGIN
            SELECT count(*) INTO sin_hold FROM bookings
                WHERE status = 'PENDIENTE_COTIZACION' AND hold_expires_at IS NULL;
            IF sin_hold > 0 THEN
                RAISE EXCEPTION
                    'downgrade 0005: % turnos PENDIENTE_COTIZACION sin hold; 0004 lo exige',
                    sin_hold;
            END IF;
        END $$
        """
    )
    # bookings: vuelve a ocupar el puesto y a exigir hold.
    op.execute(f"ALTER TABLE bookings DROP CONSTRAINT IF EXISTS {BOOKING_OVERLAP}")
    op.execute(
        f"ALTER TABLE bookings ADD CONSTRAINT {BOOKING_OVERLAP} {ALIVE_EXCLUDE}"
        f"WHERE (status IN ({BLOCKING_BEFORE}) AND {ALIVE})"
    )
    _replace_constraint(
        "bookings",
        "ck_bookings_hold_obligatorio",
        f"CHECK (status NOT IN ({HOLD_BEFORE}) OR hold_expires_at IS NOT NULL)",
    )
    # booking_items / job_items: sale `pricing_mode`; el precio vuelve a poder ser NULL.
    for table in ("job_items", "booking_items"):
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS pricing_mode")
    op.alter_column("booking_items", "price_cents", existing_type=sa.BigInteger(), nullable=True)
    _replace_constraint(
        "booking_items",
        "ck_booking_items_precio_positivo",
        "CHECK (price_cents IS NULL OR price_cents > 0)",
    )
    _add_constraint(
        "booking_items",
        "ck_booking_items_sin_precio_sin_sena",
        "CHECK (price_cents IS NOT NULL OR deposit_bps = 0)",
    )
    # service_prices: vuelve `deposit_bps` (en 0) y el precio nullable.
    op.add_column(
        "service_prices",
        sa.Column("deposit_bps", sa.Integer(), server_default="0", nullable=False),
        if_not_exists=True,
    )
    _add_constraint(
        "service_prices",
        "ck_service_prices_sena_bps_rango",
        "CHECK (deposit_bps BETWEEN 0 AND 10000)",
    )
    op.alter_column("service_prices", "price_cents", existing_type=sa.BigInteger(), nullable=True)
    _replace_constraint(
        "service_prices",
        "ck_service_prices_precio_positivo",
        "CHECK (price_cents IS NULL OR price_cents > 0)",
    )
    # tenant_settings y slug.
    op.drop_table(TENANT_SETTINGS, if_exists=True)
    op.drop_index("ux_tenants_slug", "tenants", if_exists=True)
    op.execute("ALTER TABLE tenants DROP CONSTRAINT IF EXISTS ck_tenants_slug_formato")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS slug")
    _force(True)
