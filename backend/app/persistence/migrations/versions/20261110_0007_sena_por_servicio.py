"""Seña propia por servicio.

Revision ID: 0007_sena_por_servicio
Revises: 0006_servicios_categorias
Create Date: 2026-11-10

Por qué (FASE-4-CONTRATO §11.6, D4-17): un servicio puede tener su propio % de seña que pisa al
general del negocio (en Sola, el camión paga 30 % y el resto 20 %). `services.deposit_bps` int
**nullable**: `NULL` = usa `tenant_settings.deposit_bps`. Mismo rango que el general (1..10000);
el interruptor `deposit_enabled` sigue siendo del negocio.

Sin relleno: los servicios existentes quedan en `NULL` (usan el general, como hasta ahora). Agregar
una columna nullable sin default no reescribe la tabla.

Idempotente (A6), como 0003 a 0006.

**Downgrade con corte explícito:** si algún servicio tiene % propio, bajar lo perdería y cambiaría
lo que paga el cliente; corta y lo dice. Hay que vaciarlo a mano (`UPDATE services SET deposit_bps
= NULL`) y reintentar.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_sena_por_servicio"
down_revision: str | None = "0006_servicios_categorias"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"
RANGE_CHECK = "ck_services_sena_bps_rango"


def upgrade() -> None:
    op.execute(LOCK_TIMEOUT)
    op.add_column(
        "services", sa.Column("deposit_bps", sa.Integer(), nullable=True), if_not_exists=True
    )
    # `ADD CONSTRAINT` idempotente: Postgres no tiene `ADD CONSTRAINT IF NOT EXISTS`.
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_constraint "
        f"WHERE conname = '{RANGE_CHECK}' AND conrelid = 'public.services'::regclass) THEN "
        f"ALTER TABLE services ADD CONSTRAINT {RANGE_CHECK} "
        "CHECK (deposit_bps IS NULL OR deposit_bps BETWEEN 1 AND 10000); "
        "END IF; END $$"
    )


def downgrade() -> None:
    """Vuelve al esquema de 0006. Corta, diciendo por qué, si algún servicio tiene % propio."""
    op.execute(LOCK_TIMEOUT)
    # `NO FORCE` para que el chequeo vea todas las filas (con FORCE y sin tenant en contexto: 0).
    op.execute("ALTER TABLE services NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        DECLARE
            propios integer := 0;
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema = 'public' AND table_name = 'services'
                         AND column_name = 'deposit_bps') THEN
                SELECT count(*) INTO propios FROM services WHERE deposit_bps IS NOT NULL;
            END IF;
            IF propios > 0 THEN
                RAISE EXCEPTION
                    'downgrade 0007: se perderia la sena propia de % servicios. Vaciarla a mano '
                    '(UPDATE services SET deposit_bps = NULL) y reintentar.', propios;
            END IF;
        END $$
        """
    )
    op.execute(f"ALTER TABLE services DROP CONSTRAINT IF EXISTS {RANGE_CHECK}")
    op.execute("ALTER TABLE services DROP COLUMN IF EXISTS deposit_bps")
    op.execute("ALTER TABLE services FORCE ROW LEVEL SECURITY")
