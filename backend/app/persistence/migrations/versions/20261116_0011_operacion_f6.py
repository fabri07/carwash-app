"""Operación de la F6: `jobs.entry_mode`.

Revision ID: 0011_operacion_f6
Revises: 0010_agenda_f5
Create Date: 2026-11-16

Por qué (FASE-6-CONTRATO §2): la carga retrospectiva (D6-3) crea un job **después del hecho**, con
las horas reales que tipeó el operador. `jobs.entry_mode` (enum `job_entry_mode`: `EN_VIVO`,
`RETROSPECTIVA`; `NOT NULL`, default `EN_VIVO`) distingue esos jobs de los toques en vivo, y cada
evento de la carga lleva además `{"manual_entry": true}` en `job_events.metadata`. Es lo único que
la F6 pide a la base: sin tablas nuevas.

Los jobs existentes quedan `EN_VIVO` por el default del servidor: no hay relleno, así que la subida
no necesita `NO FORCE`. La bajada sí lo usa (como 0007 a 0010) para contar **todas** las filas: con
`FORCE ROW LEVEL SECURITY` y sin tenant en contexto el dueño de la migración no ve ninguna.

Idempotente (A6), como 0003 a 0010: `CREATE TYPE` tolera `duplicate_object` y la columna se agrega
con `IF NOT EXISTS`.

**Downgrade con corte explícito:** si hay jobs `RETROSPECTIVA` vivos, **corta y lo dice**: bajar
borraría la marca que los distingue de los toques en vivo.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_operacion_f6"
down_revision: str | None = "0010_agenda_f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"

# Congelados acá: la migración describe lo que creó. `test_migracion_0011_pg.py` los compara con
# `app/domain/enums.py`.
ENTRY_MODE_ENUM = "job_entry_mode"
ENTRY_MODES = ("EN_VIVO", "RETROSPECTIVA")


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


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


def upgrade() -> None:
    op.execute(LOCK_TIMEOUT)
    op.execute(
        f"DO $$ BEGIN CREATE TYPE {ENTRY_MODE_ENUM} AS ENUM "
        f"({', '.join(_literal(v) for v in ENTRY_MODES)}); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    op.add_column(
        "jobs",
        sa.Column(
            "entry_mode",
            postgresql.ENUM(*ENTRY_MODES, name=ENTRY_MODE_ENUM, create_type=False),
            server_default=ENTRY_MODES[0],
            nullable=False,
        ),
        if_not_exists=True,
    )


def downgrade() -> None:
    """Vuelve al esquema de 0010. Corta, diciendo por qué, si perdería la marca de la carga manual."""
    op.execute(LOCK_TIMEOUT)
    # `NO FORCE` para que el chequeo vea todas las filas (con FORCE y sin tenant en contexto: 0).
    op.execute("ALTER TABLE jobs NO FORCE ROW LEVEL SECURITY")
    if _has_column("jobs", "entry_mode"):
        lost = int(
            op.get_bind().scalar(
                sa.text(
                    "SELECT count(*) FROM jobs WHERE voided_at IS NULL "
                    f"AND entry_mode::text <> {_literal(ENTRY_MODES[0])}"
                )
            )
            or 0
        )
        if lost:
            raise RuntimeError(
                f"downgrade 0011: se perderia la marca de {lost} jobs de carga retrospectiva. "
                "Anularlos a mano (UPDATE jobs SET voided_at = now(), void_reason = "
                "'ERROR_DE_CARGA' WHERE entry_mode = 'RETROSPECTIVA') y reintentar."
            )
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS entry_mode")
    op.execute(f"DROP TYPE IF EXISTS {ENTRY_MODE_ENUM}")
    op.execute("ALTER TABLE jobs FORCE ROW LEVEL SECURITY")
