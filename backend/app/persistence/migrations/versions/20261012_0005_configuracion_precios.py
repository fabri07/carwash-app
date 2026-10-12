"""Configuración del negocio, seña global y precio base obligatorio.

Revision ID: 0005_configuracion_precios
Revises: 0004_cuentas_permisos
Create Date: 2026-10-12

Por qué (FASE-4-CONTRATO §3.2, D4-4, D4-6, D4-13, Y3, Y10):

- **`tenants.slug`** (Y10): la URL pública de F5. Único global entre vivos.
- **`tenant_settings`** (nueva, 1:1, con RLS, sin anulación): contacto, seña global con un
  único % (D4-4) y los parámetros que en F3 eran constantes o argumentos.
- **Precio base obligatorio** (D4-6): `service_prices` pierde `deposit_bps` (la seña pasa a
  ser global) y su precio pasa a NOT NULL; `booking_items` también. Ítems de turno y de job
  guardan `pricing_mode`: el ítem cotizado se reconoce por la modalidad, no por el NULL.
- **Un turno a cotizar no ocupa el puesto** (D4-13): `PENDIENTE_COTIZACION` sale del `WHERE`
  del `EXCLUDE` y del CHECK que exige `hold_expires_at`.

**Procedimiento sobre datos existentes**, en este orden:

1. **Guarda:** si queda algún precio NULL en `service_prices`, `booking_items` o
   `job_items`, corta **antes de alterar nada** y lista los ids. Staging se prepara antes con
   `scripts/prepare_staging_0005.py` (PR 4.2a); producción está en 0001, sin datos.
2. `slug` desde el nombre más 6 caracteres del id; `tenant_settings` con defaults para cada
   tenant; `pricing_mode` de los ítems desde su servicio.
3. Recién después, los NOT NULL y los constraints nuevos.

Las tablas tienen `FORCE ROW LEVEL SECURITY`: el relleno corre con `NO FORCE` dentro de esta
transacción y se vuelve a forzar antes de terminar, como en 0004.

Idempotente (A6), como 0003 y 0004.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.persistence.db.rls import enable_rls, grant_app_role

revision: str = "0005_configuracion_precios"
down_revision: str | None = "0004_cuentas_permisos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Congelados acá: la migración describe el esquema que creó, no el ORM vivo.
ALIVE = "voided_at IS NULL"
LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"
SETTINGS = "tenant_settings"
SLUG_PATTERN = "^[a-z0-9-]{3,40}$"
CATALOG_KEY_PATTERN = "^[a-z0-9-]{1,40}$"

BOOKING_OVERLAP = "xc_bookings_sin_solapamiento"
HOLD_CHECK = "ck_bookings_hold_obligatorio"


def _overlap_sql(blocking: str) -> str:
    return (
        "EXCLUDE USING gist (tenant_id WITH =, resource_id WITH =, "
        "tstzrange(start_at, end_at, '[)') WITH &&) "
        f"WHERE (status IN ({blocking}) AND {ALIVE})"
    )


BLOCKING_0005 = "'PENDIENTE_SEÑA', 'CONFIRMADO', 'RECIBIDO'"
BLOCKING_0004 = "'PENDIENTE_SEÑA', 'PENDIENTE_COTIZACION', 'CONFIRMADO', 'RECIBIDO'"
HOLD_0005 = "'PENDIENTE_SEÑA'"
HOLD_0004 = "'PENDIENTE_SEÑA', 'PENDIENTE_COTIZACION'"

#: Tablas que el relleno lee o escribe (todas con `FORCE`).
BACKFILL_TABLES = ("tenants", SETTINGS, "bookings", "jobs", "quotes", "booking_items", "job_items")
#: Las que la bajada vuelve a dejar como las espera el código de 0004.
DOWNGRADE_TABLES = ("services", "service_prices", "bookings", "booking_items")

#: Slug desde el nombre: sin acentos, minúsculas, todo lo que no sea `[a-z0-9]` → `-`, hasta
#: 33 caracteres, más `-` y 6 del md5 del id (único sin consultar a los demás).
SLUG_FROM_NAME = (
    "coalesce(nullif(btrim(left(btrim(regexp_replace(lower(translate(t.name, "
    "'áéíóúüñÁÉÍÓÚÜÑ', 'aeiouunaeiouun')), '[^a-z0-9]+', '-', 'g'), '-'), 33), '-'), ''), "
    "'negocio') || '-' || left(md5(t.id::text), 6)"
)


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


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
    """Cambia la definición de un constraint existente (idempotente: drop + add)."""
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} {definition}")


def _guard_null_prices() -> None:
    """Corta antes de alterar nada si queda un precio NULL, y dice cuáles."""
    op.execute(
        """
        DO $$
        DECLARE
            ids text;
        BEGIN
            -- Con FORCE, el dueño tampoco ve filas sin tenant en contexto: la guarda no
            -- encontraría nada. Se mira sin forzar y se vuelve a forzar antes de decidir.
            ALTER TABLE service_prices NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE booking_items NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE job_items NO FORCE ROW LEVEL SECURITY;
            SELECT string_agg(tabla || ':' || id::text, ', ') INTO ids FROM (
                SELECT 'service_prices' AS tabla, id FROM service_prices
                    WHERE price_cents IS NULL
                UNION ALL
                SELECT 'booking_items', id FROM booking_items WHERE price_cents IS NULL
                UNION ALL
                SELECT 'job_items', id FROM job_items WHERE price_cents IS NULL
            ) AS nulos;
            ALTER TABLE service_prices FORCE ROW LEVEL SECURITY;
            ALTER TABLE booking_items FORCE ROW LEVEL SECURITY;
            ALTER TABLE job_items FORCE ROW LEVEL SECURITY;
            IF ids IS NOT NULL THEN
                RAISE EXCEPTION
                    'upgrade 0005: hay precios NULL (correr scripts/prepare_staging_0005.py): %',
                    ids;
            END IF;
        END $$
        """
    )


def _create_settings_table() -> None:
    t = SETTINGS
    if not _has_table(t):
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
            sa.Column("deposit_bps", sa.Integer(), server_default="2000", nullable=False),
            sa.Column("tolerance_min", sa.Integer(), server_default="20", nullable=False),
            sa.Column(
                "late_cancel_threshold_min", sa.Integer(), server_default="720", nullable=False
            ),
            sa.Column("hold_min", sa.Integer(), server_default="30", nullable=False),
            sa.Column("min_advance_min", sa.Integer(), server_default="5", nullable=False),
            sa.Column("palette", sa.Text(), server_default="default", nullable=False),
            sa.Column("font", sa.Text(), server_default="default", nullable=False),
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
            sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{t}")),
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["tenants.id"],
                name=op.f(f"fk_{t}_tenant_id_tenants"),
                ondelete="RESTRICT",
            ),
            sa.UniqueConstraint("tenant_id", name=op.f(f"uq_{t}_tenant_id")),
            sa.CheckConstraint(
                "length(welcome_text) <= 280", name=op.f(f"ck_{t}_bienvenida_largo")
            ),
            sa.CheckConstraint(
                "deposit_bps BETWEEN 1 AND 10000", name=op.f(f"ck_{t}_sena_bps_rango")
            ),
            sa.CheckConstraint("tolerance_min > 0", name=op.f(f"ck_{t}_tolerancia_positiva")),
            sa.CheckConstraint(
                "late_cancel_threshold_min > 0", name=op.f(f"ck_{t}_cancelacion_tardia_positiva")
            ),
            sa.CheckConstraint("hold_min > 0", name=op.f(f"ck_{t}_retencion_positiva")),
            sa.CheckConstraint(
                "min_advance_min >= 0", name=op.f(f"ck_{t}_anticipacion_no_negativa")
            ),
            sa.CheckConstraint(
                f"palette ~ '{CATALOG_KEY_PATTERN}'", name=op.f(f"ck_{t}_paleta_formato")
            ),
            sa.CheckConstraint(
                f"font ~ '{CATALOG_KEY_PATTERN}'", name=op.f(f"ck_{t}_fuente_formato")
            ),
        )
    op.create_index(op.f(f"ix_{t}_tenant_id"), t, ["tenant_id"], if_not_exists=True)
    for statement in [*enable_rls(t), *grant_app_role([t])]:
        op.execute(statement)


def _add_nullable_columns() -> None:
    op.add_column("tenants", sa.Column("slug", sa.Text(), nullable=True), if_not_exists=True)
    mode = postgresql.ENUM(name="pricing_mode", create_type=False)
    for table in ("booking_items", "job_items"):
        op.add_column(table, sa.Column("pricing_mode", mode, nullable=True), if_not_exists=True)


def _backfill() -> None:
    for table in BACKFILL_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"UPDATE tenants AS t SET slug = {SLUG_FROM_NAME} WHERE t.slug IS NULL")
    op.execute(
        f"INSERT INTO {SETTINGS} (id, tenant_id) "
        "SELECT gen_random_uuid(), t.id FROM tenants AS t "
        f"WHERE NOT EXISTS (SELECT 1 FROM {SETTINGS} AS s WHERE s.tenant_id = t.id)"
    )
    # La modalidad AL RESERVAR, no la de hoy del servicio (pudo cambiar con
    # `change_pricing_mode`): en F3 un ítem es a cotizar si y solo si su turno o su job tiene
    # cotización y es de ese servicio.
    for table, parent, fk in (
        ("booking_items", "bookings", "booking_id"),
        ("job_items", "jobs", "job_id"),
    ):
        op.execute(
            f"UPDATE {table} AS i SET pricing_mode = CASE WHEN EXISTS ("
            f"SELECT 1 FROM {parent} AS p JOIN quotes AS q "
            "ON q.tenant_id = p.tenant_id AND q.id = p.quote_id "
            f"WHERE p.tenant_id = i.tenant_id AND p.id = i.{fk} AND q.service_id = i.service_id"
            ") THEN 'A_COTIZAR'::pricing_mode ELSE 'PRECIO_FIJO'::pricing_mode END "
            "WHERE i.pricing_mode IS NULL"
        )
    for table in BACKFILL_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.execute(LOCK_TIMEOUT)
    _guard_null_prices()
    _create_settings_table()
    _add_nullable_columns()
    _backfill()

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

    for table in ("booking_items", "job_items"):
        op.alter_column(table, "pricing_mode", nullable=False)

    # D4-6: precio siempre; la seña es global (D4-4).
    op.execute(
        "ALTER TABLE service_prices DROP CONSTRAINT IF EXISTS ck_service_prices_sena_bps_rango"
    )
    op.drop_column("service_prices", "deposit_bps", if_exists=True)
    op.execute(
        "ALTER TABLE booking_items DROP CONSTRAINT IF EXISTS ck_booking_items_sin_precio_sin_sena"
    )
    for table in ("service_prices", "booking_items"):
        op.alter_column(table, "price_cents", existing_type=sa.BigInteger(), nullable=False)
        _replace_constraint(table, f"ck_{table}_precio_positivo", "CHECK (price_cents > 0)")

    # D4-13: un turno a cotizar no ocupa el puesto ni retiene horario.
    _replace_constraint(
        "bookings",
        HOLD_CHECK,
        f"CHECK (status NOT IN ({HOLD_0005}) OR hold_expires_at IS NOT NULL)",
    )
    _replace_constraint("bookings", BOOKING_OVERLAP, _overlap_sql(BLOCKING_0005))


def downgrade() -> None:
    """Vuelve al esquema de 0004. Corta, con un mensaje que dice por qué, si hay turnos
    `PENDIENTE_COTIZACION` vivos sin hold: 0004 se lo exige y bajar no puede inventarlo.

    El código de 0004 reconoce lo "a cotizar" por el precio NULL: antes de borrar
    `pricing_mode`, los precios base de los servicios a cotizar, de sus ítems de turno y de los
    turnos todavía sin cotizar vuelven a NULL (con seña 0). La seña por precio vuelve en 0,
    como un servicio sin seña.
    """
    op.execute(LOCK_TIMEOUT)
    op.execute(
        """
        DO $$
        DECLARE
            n integer;
        BEGIN
            ALTER TABLE bookings NO FORCE ROW LEVEL SECURITY;
            SELECT count(*) INTO n FROM bookings
                WHERE status = 'PENDIENTE_COTIZACION' AND hold_expires_at IS NULL
                  AND voided_at IS NULL;
            ALTER TABLE bookings FORCE ROW LEVEL SECURITY;
            IF n > 0 THEN
                RAISE EXCEPTION
                    'downgrade 0005: % turnos a cotizar sin hold; 0004 lo exige', n;
            END IF;
        END $$
        """
    )
    _replace_constraint("bookings", BOOKING_OVERLAP, _overlap_sql(BLOCKING_0004))
    _replace_constraint(
        "bookings",
        HOLD_CHECK,
        f"CHECK (status NOT IN ({HOLD_0004}) OR hold_expires_at IS NOT NULL)",
    )

    for table in ("service_prices", "booking_items"):
        op.alter_column(table, "price_cents", existing_type=sa.BigInteger(), nullable=True)
        _replace_constraint(
            table, f"ck_{table}_precio_positivo", "CHECK (price_cents IS NULL OR price_cents > 0)"
        )
    _add_constraint(
        "booking_items",
        "ck_booking_items_sin_precio_sin_sena",
        "CHECK (price_cents IS NOT NULL OR deposit_bps = 0)",
    )
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

    if _has_column("booking_items", "pricing_mode"):
        for table in DOWNGRADE_TABLES:
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(
            "UPDATE service_prices AS sp SET price_cents = NULL FROM services AS s "
            "WHERE s.tenant_id = sp.tenant_id AND s.id = sp.service_id "
            "AND s.pricing_mode = 'A_COTIZAR'"
        )
        op.execute(
            "UPDATE booking_items SET price_cents = NULL, deposit_bps = 0 "
            "WHERE pricing_mode = 'A_COTIZAR'"
        )
        op.execute(
            "UPDATE bookings SET price_cents = NULL, deposit_required_cents = 0 "
            "WHERE status = 'PENDIENTE_COTIZACION'"
        )
        for table in DOWNGRADE_TABLES:
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for table in ("booking_items", "job_items"):
        op.drop_column(table, "pricing_mode", if_exists=True)
    op.drop_index("ux_tenants_slug", table_name="tenants", if_exists=True)
    op.execute("ALTER TABLE tenants DROP CONSTRAINT IF EXISTS ck_tenants_slug_formato")
    op.drop_column("tenants", "slug", if_exists=True)
    op.execute(f"DROP TABLE IF EXISTS {SETTINGS}")
