"""Catálogo de vehículos por negocio: marcas, modelos, colores y carrocería → tamaño.

Revision ID: 0008_catalogo_vehiculos
Revises: 0007_sena_por_servicio
Create Date: 2026-11-10

Por qué (FASE-4-CONTRATO D4-8, Y3, Y7, §3.4):

- **`vehicle_body_type`** (enum nativo, valores congelados acá como `ENUMS` en 0003).
- **`vehicle_makes`**, **`vehicle_models`**, **`vehicle_colors`** (con RLS): `name`, `search_key`,
  `is_custom`, `hidden_at`; únicos entre vivos por `search_key` (los modelos, por marca).
- **`body_type_sizes`** (con RLS): qué tamaño del negocio sugiere cada carrocería; única viva por
  carrocería.
- **`vehicles`**: suma `make_id`, `model_id` y `color_id` (FKs compuestas, nullable). `brand_model`
  y `color` quedan como texto libre de respaldo.

**Copia de la base a los negocios existentes (Y3):** cada tenant que todavía no tiene marcas
recibe la base de `app/data/vehiculos_ar.json` entera, en tres `INSERT … SELECT` (marcas, modelos
por marca, colores). Un tenant que ya tiene marcas no se toca: re-aplicar no duplica ni
"resucita" lo que el negocio renombró. La base se lee **del archivo** (no se congela acá: son
~400 modelos); si la base cambia, los negocios existentes se completan con una migración de datos
nueva, esta no se edita.
`search_key` se calcula con una copia **congelada** de `normalize` (`_normalize`), como en 0006.

`body_type_sizes` nace **vacía** (también en `provision_tenant`): al registrarse el negocio
todavía no tiene tamaños, y adivinar por código (`AUTO`, `SUV`…) en los existentes dejaría filas
que el negocio no eligió. Se carga con `PUT /body-type-sizes` (el wizard del 4.6, paso "tamaños y
carrocerías").

Las tablas tienen `FORCE ROW LEVEL SECURITY`: el dueño de la migración no ve filas sin tenant en
contexto, así que la copia corre con `NO FORCE` y se vuelve a forzar antes de terminar, todo en la
misma transacción. `carwash_owner` no crea extensiones (X11): `gen_random_uuid()` es del núcleo.

Idempotente (A6), como 0003 a 0007.

**Downgrade con corte explícito:** bajar borra el catálogo de cada negocio. Si hay algo que el
negocio hizo (marcas, modelos o colores propios, ocultos o renombrados respecto de la base;
carrocerías con tamaño; vehículos con marca, modelo o color del catálogo), **corta y lo dice**. Hay
que vaciarlo a mano y reintentar. La copia intacta de la base se borra sin preguntar: se regenera.
"""

import json
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import SchemaItem

from app.persistence.db.rls import enable_rls, grant_app_role

revision: str = "0008_catalogo_vehiculos"
down_revision: str | None = "0007_sena_por_servicio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALIVE = "voided_at IS NULL"
LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"
VOID_REASONS = ("ERROR_DE_CARGA", "PEDIDO_DEL_USUARIO", "DESACTIVADO")

# Congelado acá: la migración describe el tipo que creó. `test_migracion_0008_pg.py` lo compara
# con `app/domain/vehicle_catalog.py::VehicleBodyType`.
BODY_TYPE_ENUM = "vehicle_body_type"
BODY_TYPES = (
    "SEDAN",
    "HATCHBACK",
    "RURAL",
    "SUV",
    "PICKUP",
    "UTILITARIO",
    "FURGON",
    "MOTO",
    "OTRO",
)
MAKE_NAME_MAX = 40
MODEL_NAME_MAX = 60
COLOR_NAME_MAX = 40

MAKES = "vehicle_makes"
MODELS = "vehicle_models"
COLORS = "vehicle_colors"
BODY_TYPE_SIZES = "body_type_sizes"
NEW_TABLES = (MAKES, MODELS, COLORS, BODY_TYPE_SIZES)

#: `vehicles.<columna>` → tabla del catálogo.
VEHICLE_FKS = {"make_id": MAKES, "model_id": MODELS, "color_id": COLORS}

DATA_FILE = Path(__file__).resolve().parents[3] / "data" / "vehiculos_ar.json"

_NO_ALFANUM = re.compile(r"[^a-z0-9]+")


def _normalize(text: str) -> str:
    """Copia congelada de `app.domain.search.normalize` (ver docstring del módulo)."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return _NO_ALFANUM.sub(" ", folded.lower()).strip()


def _base() -> dict[str, list[tuple[str, ...]]]:
    """La base del archivo, en filas listas para `VALUES`."""
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    makes, models = [], []
    for make in data["makes"]:
        make_key = _normalize(make["name"])
        makes.append((make["name"], make_key))
        for model in make["models"]:
            models.append((make_key, model["name"], model["body_type"], _normalize(model["name"])))
    colors = [(name, _normalize(name)) for name in data["colors"]]
    return {"makes": makes, "models": models, "colors": colors}


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _values(rows: Sequence[tuple[str, ...]]) -> str:
    return ", ".join("(" + ", ".join(_literal(v) for v in row) + ")" for row in rows)


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


def _fk_name(table: str, column: str, parent: str) -> str:
    return f"fk_{table}_tenant_id_{column}_{parent}"


def _body_type() -> postgresql.ENUM:
    return postgresql.ENUM(*BODY_TYPES, name=BODY_TYPE_ENUM, create_type=False)


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


def _catalog_columns(table: str, name_max: int) -> list[SchemaItem]:
    """Lo común a marcas, modelos y colores."""
    return [
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("search_key", sa.Text(), nullable=False),
        sa.Column("is_custom", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{table}_tenant_id_id"),
        sa.CheckConstraint(
            f"length(name) BETWEEN 1 AND {name_max}", name=op.f(f"ck_{table}_nombre_largo")
        ),
    ]


def _tenant_fk(table: str, column: str, parent: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{parent}.tenant_id", f"{parent}.id"],
        name=_fk_name(table, column, parent),
        ondelete="RESTRICT",
    )


def _unique_alive(table: str, *columns: str) -> None:
    op.create_index(
        f"ux_{table}_tenant_id_{'_'.join(columns)}",
        table,
        ["tenant_id", *columns],
        unique=True,
        postgresql_where=sa.text(ALIVE),
        if_not_exists=True,
    )


def _index(table: str, column: str) -> None:
    op.create_index(
        f"ix_{table}_tenant_id_{column}", table, ["tenant_id", column], if_not_exists=True
    )


def _create_tables() -> None:
    op.execute(
        f"DO $$ BEGIN CREATE TYPE {BODY_TYPE_ENUM} AS ENUM "
        f"({', '.join(_literal(v) for v in BODY_TYPES)}); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    if not _has_table(MAKES):
        op.create_table(MAKES, *_base_columns(MAKES), *_catalog_columns(MAKES, MAKE_NAME_MAX))
    if not _has_table(MODELS):
        op.create_table(
            MODELS,
            *_base_columns(MODELS),
            sa.Column("make_id", sa.UUID(), nullable=False),
            sa.Column("body_type", _body_type(), nullable=False),
            *_catalog_columns(MODELS, MODEL_NAME_MAX),
            _tenant_fk(MODELS, "make_id", MAKES),
        )
    if not _has_table(COLORS):
        op.create_table(COLORS, *_base_columns(COLORS), *_catalog_columns(COLORS, COLOR_NAME_MAX))
    if not _has_table(BODY_TYPE_SIZES):
        op.create_table(
            BODY_TYPE_SIZES,
            *_base_columns(BODY_TYPE_SIZES),
            sa.Column("body_type", _body_type(), nullable=False),
            sa.Column("vehicle_size_id", sa.UUID(), nullable=False),
            _tenant_fk(BODY_TYPE_SIZES, "vehicle_size_id", "vehicle_sizes"),
        )
    for table in NEW_TABLES:
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"], if_not_exists=True)
    _unique_alive(MAKES, "search_key")
    _index(MODELS, "make_id")
    _unique_alive(MODELS, "make_id", "search_key")
    _unique_alive(COLORS, "search_key")
    _index(BODY_TYPE_SIZES, "vehicle_size_id")
    _unique_alive(BODY_TYPE_SIZES, "body_type")
    for statement in [
        *(s for table in NEW_TABLES for s in enable_rls(table)),
        *grant_app_role(list(NEW_TABLES)),
    ]:
        op.execute(statement)


def _alter_vehicles() -> None:
    for column, parent in VEHICLE_FKS.items():
        op.add_column("vehicles", sa.Column(column, sa.UUID(), nullable=True), if_not_exists=True)
        _add_constraint(
            "vehicles",
            _fk_name("vehicles", column, parent),
            f"FOREIGN KEY (tenant_id, {column}) "
            f"REFERENCES {parent} (tenant_id, id) ON DELETE RESTRICT",
        )
        _index("vehicles", column)


def _copy_base_to_existing_tenants() -> None:
    """La base entera a cada tenant sin marcas (Y3). Con `NO FORCE`: con RLS forzado y sin
    tenant en contexto el dueño vería 0 filas y no copiaría nada."""
    tables = ("tenants", MAKES, MODELS, COLORS)
    for table in tables:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    bind = op.get_bind()
    # Como texto: psycopg2 no adapta `uuid.UUID` sin registrarlo; la consulta lo castea.
    pending = [
        str(row[0])
        for row in bind.execute(
            sa.text(
                f"SELECT t.id FROM tenants AS t WHERE NOT EXISTS "
                f"(SELECT 1 FROM {MAKES} AS m WHERE m.tenant_id = t.id)"
            )
        )
    ]
    if pending:
        base = _base()
        ids = {"ids": pending}
        bind.execute(
            sa.text(
                f"INSERT INTO {MAKES} (id, tenant_id, name, search_key, is_custom) "
                "SELECT gen_random_uuid(), t.id, v.name, v.key, false "
                f"FROM tenants AS t CROSS JOIN (VALUES {_values(base['makes'])}) AS v(name, key) "
                "WHERE t.id = ANY(CAST(:ids AS uuid[]))"
            ),
            ids,
        )
        bind.execute(
            sa.text(
                f"INSERT INTO {MODELS} "
                "(id, tenant_id, make_id, name, body_type, search_key, is_custom) "
                "SELECT gen_random_uuid(), m.tenant_id, m.id, v.name, "
                f"v.body_type::{BODY_TYPE_ENUM}, v.key, false "
                f"FROM (VALUES {_values(base['models'])}) AS v(make_key, name, body_type, key) "
                f"JOIN {MAKES} AS m ON m.search_key = v.make_key AND m.{ALIVE} "
                "WHERE m.tenant_id = ANY(CAST(:ids AS uuid[]))"
            ),
            ids,
        )
        bind.execute(
            sa.text(
                f"INSERT INTO {COLORS} (id, tenant_id, name, search_key, is_custom) "
                "SELECT gen_random_uuid(), t.id, v.name, v.key, false "
                f"FROM tenants AS t CROSS JOIN (VALUES {_values(base['colors'])}) AS v(name, key) "
                "WHERE t.id = ANY(CAST(:ids AS uuid[]))"
            ),
            ids,
        )
    for table in tables:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.execute(LOCK_TIMEOUT)
    _create_tables()
    _alter_vehicles()
    _copy_base_to_existing_tenants()


def _business_data() -> dict[str, int]:
    """Lo que el negocio hizo sobre su catálogo y bajar perdería. Vacío si solo está la base."""
    bind = op.get_bind()
    base = _base()
    counts: dict[str, int] = {}

    def count(label: str, sql: str) -> None:
        counts[label] = int(bind.scalar(sa.text(sql)) or 0)

    if _has_table(MAKES):
        count(
            "marcas propias, ocultas o renombradas",
            f"SELECT count(*) FROM {MAKES} WHERE is_custom OR hidden_at IS NOT NULL "
            f"OR (name, search_key) NOT IN (VALUES {_values(base['makes'])})",
        )
    if _has_table(MODELS) and _has_table(MAKES):
        count(
            "modelos propios, ocultos o editados",
            f"SELECT count(*) FROM {MODELS} AS x JOIN {MAKES} AS m "
            "ON m.tenant_id = x.tenant_id AND m.id = x.make_id "
            "WHERE x.is_custom OR x.hidden_at IS NOT NULL "
            "OR (m.search_key, x.name, x.body_type::text, x.search_key) NOT IN "
            f"(VALUES {_values(base['models'])})",
        )
    if _has_table(COLORS):
        count(
            "colores propios, ocultos o renombrados",
            f"SELECT count(*) FROM {COLORS} WHERE is_custom OR hidden_at IS NOT NULL "
            f"OR (name, search_key) NOT IN (VALUES {_values(base['colors'])})",
        )
    if _has_table(BODY_TYPE_SIZES):
        count("carrocerias con tamano", f"SELECT count(*) FROM {BODY_TYPE_SIZES}")
    present = bind.scalar(
        sa.text(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = 'vehicles' AND column_name = 'make_id'"
        )
    )
    if present:
        count(
            "vehiculos con marca, modelo o color del catalogo",
            "SELECT count(*) FROM vehicles "
            "WHERE make_id IS NOT NULL OR model_id IS NOT NULL OR color_id IS NOT NULL",
        )
    return {label: n for label, n in counts.items() if n}


def downgrade() -> None:
    """Vuelve al esquema de 0007. Corta, diciendo por qué, si perdería datos del negocio."""
    op.execute(LOCK_TIMEOUT)
    # `NO FORCE` para que el chequeo vea todas las filas (con FORCE y sin tenant en contexto: 0).
    for table in ("vehicles", *NEW_TABLES):
        if _has_table(table):
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    lost = _business_data()
    if lost:
        detail = ", ".join(f"{n} {label}" for label, n in lost.items())
        raise RuntimeError(
            f"downgrade 0008: se perderian datos del catalogo de vehiculos ({detail}). "
            "Vaciarlos a mano (UPDATE vehicles SET make_id = NULL, model_id = NULL, "
            f"color_id = NULL; DELETE FROM {BODY_TYPE_SIZES}; y las filas propias, ocultas o "
            "renombradas de marcas, modelos y colores) y reintentar."
        )
    for column, parent in VEHICLE_FKS.items():
        op.execute(
            f"ALTER TABLE vehicles DROP CONSTRAINT IF EXISTS {_fk_name('vehicles', column, parent)}"
        )
        op.drop_index(f"ix_vehicles_tenant_id_{column}", "vehicles", if_exists=True)
        op.execute(f"ALTER TABLE vehicles DROP COLUMN IF EXISTS {column}")
    for table in (BODY_TYPE_SIZES, MODELS, MAKES, COLORS):
        op.drop_table(table, if_exists=True)
    op.execute(f"DROP TYPE IF EXISTS {BODY_TYPE_ENUM}")
    op.execute("ALTER TABLE vehicles FORCE ROW LEVEL SECURITY")
