"""Categorías de servicios, código de glosario y clave de búsqueda.

Revision ID: 0006_servicios_categorias
Revises: 0005_configuracion_precios
Create Date: 2026-11-05

Por qué (FASE-4-CONTRATO §3.3, Y6, Y8):

- **`service_categories`** (nueva, con RLS): `name` (único entre vivos) y `sort_order`.
- **`services`**: suma `category_id` (FK compuesta, nullable), `glossary_code` (nullable; único
  entre vivos, así la plantilla no duplica) y `search_key` (NN, `normalize(name)`).

**`search_key` de los servicios que ya existen:** se rellena acá con una copia **congelada** de
`domain/search.py::normalize` (`_normalize`): la migración describe lo que hizo en su momento y no
cambia si el código de búsqueda cambia. `test_migracion_0006_pg.py` compara la copia
con la función viva (si divergen hay que decidir si se reindexa).

Las tablas tienen `FORCE ROW LEVEL SECURITY`: el dueño de la migración no ve filas sin tenant en
contexto, así que el relleno corre con `NO FORCE` y se vuelve a forzar antes de terminar, todo en
la misma transacción.

Idempotente (A6), como 0003 a 0005.

**Downgrade con corte explícito:** bajar borra `service_categories` y las dos columnas de
`services`, que son datos del negocio (categorías, vínculo con el glosario). Si hay alguna
categoría o algún servicio con categoría o código de glosario, **corta y lo dice**: no se pierde
información en silencio. Hay que vaciarlas a mano (UPDATE/DELETE) y reintentar.
"""

import re
import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.persistence.db.rls import enable_rls, grant_app_role

revision: str = "0006_servicios_categorias"
down_revision: str | None = "0005_configuracion_precios"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALIVE = "voided_at IS NULL"
LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"
CATEGORIES = "service_categories"
VOID_REASONS = ("ERROR_DE_CARGA", "PEDIDO_DEL_USUARIO", "DESACTIVADO")
CATEGORY_NAME_MAX = 60
CATEGORY_FK = "fk_services_tenant_id_category_id_service_categories"
GLOSSARY_UNIQUE = "ux_services_tenant_id_glossary_code"

_NO_ALFANUM = re.compile(r"[^a-z0-9]+")


def _normalize(text: str) -> str:
    """Copia congelada de `app.domain.search.normalize` (ver docstring del módulo)."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return _NO_ALFANUM.sub(" ", folded.lower()).strip()


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


def _create_categories() -> None:
    if not _has_table(CATEGORIES):
        t = CATEGORIES
        op.create_table(
            t,
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
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
            sa.PrimaryKeyConstraint("id", name=f"pk_{t}"),
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["tenants.id"],
                name=f"fk_{t}_tenant_id_tenants",
                ondelete="RESTRICT",
            ),
            sa.UniqueConstraint("tenant_id", "id", name=f"uq_{t}_tenant_id_id"),
            sa.CheckConstraint(
                "(voided_at IS NULL) = (void_reason IS NULL)",
                name=op.f(f"ck_{t}_void_coherente"),
            ),
            sa.CheckConstraint(
                f"length(name) BETWEEN 1 AND {CATEGORY_NAME_MAX}",
                name=op.f(f"ck_{t}_nombre_largo"),
            ),
        )
    op.create_index(f"ix_{CATEGORIES}_tenant_id", CATEGORIES, ["tenant_id"], if_not_exists=True)
    op.create_index(
        f"ux_{CATEGORIES}_tenant_id_name",
        CATEGORIES,
        ["tenant_id", "name"],
        unique=True,
        postgresql_where=sa.text(ALIVE),
        if_not_exists=True,
    )
    for statement in [*enable_rls(CATEGORIES), *grant_app_role([CATEGORIES])]:
        op.execute(statement)


def _alter_services() -> None:
    op.add_column(
        "services", sa.Column("category_id", sa.UUID(), nullable=True), if_not_exists=True
    )
    op.add_column(
        "services", sa.Column("glossary_code", sa.Text(), nullable=True), if_not_exists=True
    )
    op.add_column("services", sa.Column("search_key", sa.Text(), nullable=True), if_not_exists=True)
    _add_constraint(
        "services",
        CATEGORY_FK,
        "FOREIGN KEY (tenant_id, category_id) "
        f"REFERENCES {CATEGORIES} (tenant_id, id) ON DELETE RESTRICT",
    )
    op.create_index(
        "ix_services_tenant_id_category_id",
        "services",
        ["tenant_id", "category_id"],
        if_not_exists=True,
    )
    op.create_index(
        GLOSSARY_UNIQUE,
        "services",
        ["tenant_id", "glossary_code"],
        unique=True,
        postgresql_where=sa.text(ALIVE),
        if_not_exists=True,
    )


def _backfill_search_key() -> None:
    """`search_key` = `normalize(name)` de cada servicio (anulados incluidos).

    Con `NO FORCE`: con RLS forzado y sin tenant en contexto el dueño vería 0 filas.
    """
    op.execute("ALTER TABLE services NO FORCE ROW LEVEL SECURITY")
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, name FROM services WHERE search_key IS NULL")).all()
    for service_id, name in rows:
        bind.execute(
            sa.text("UPDATE services SET search_key = :key WHERE id = :id"),
            {"key": _normalize(name), "id": service_id},
        )
    op.alter_column("services", "search_key", existing_type=sa.Text(), nullable=False)
    op.execute("ALTER TABLE services FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.execute(LOCK_TIMEOUT)
    _create_categories()
    _alter_services()
    _backfill_search_key()


def downgrade() -> None:
    """Vuelve al esquema de 0005. Corta, diciendo por qué, si perdería datos del negocio."""
    op.execute(LOCK_TIMEOUT)
    # `NO FORCE` para que el chequeo vea todas las filas (con FORCE y sin tenant en contexto: 0).
    for table in ("services", CATEGORIES):
        if _has_table(table):
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        DO $$
        DECLARE
            categorias integer := 0;
            vinculados integer := 0;
        BEGIN
            IF to_regclass('{CATEGORIES}') IS NOT NULL THEN
                SELECT count(*) INTO categorias FROM {CATEGORIES};
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'services' AND column_name = 'category_id') THEN
                SELECT count(*) INTO vinculados FROM services
                    WHERE category_id IS NOT NULL OR glossary_code IS NOT NULL;
            END IF;
            IF categorias > 0 OR vinculados > 0 THEN
                RAISE EXCEPTION
                    'downgrade 0006: se perderian datos (% categorias, % servicios con categoria '
                    'o codigo de glosario). Vaciarlos a mano (UPDATE services SET category_id = '
                    'NULL, glossary_code = NULL; DELETE FROM {CATEGORIES}) y reintentar.',
                    categorias, vinculados;
            END IF;
        END $$
        """
    )
    op.drop_index(GLOSSARY_UNIQUE, "services", if_exists=True)
    op.drop_index("ix_services_tenant_id_category_id", "services", if_exists=True)
    op.execute(f"ALTER TABLE services DROP CONSTRAINT IF EXISTS {CATEGORY_FK}")
    for column in ("search_key", "glossary_code", "category_id"):
        op.execute(f"ALTER TABLE services DROP COLUMN IF EXISTS {column}")
    op.drop_table(CATEGORIES, if_exists=True)
    op.execute("ALTER TABLE services FORCE ROW LEVEL SECURITY")
