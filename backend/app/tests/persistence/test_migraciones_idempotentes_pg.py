"""A6 — la cadena de migraciones se puede re-aplicar con el stamp atrasado.

Correr `upgrade` dos veces no prueba nada (alembic saltea por versión). Acá se
retrocede el stamp SIN tocar el esquema y se vuelve a aplicar: es el estado en que
Véktor abortó un deploy entero con `DuplicateColumn`.
"""

import pytest
from sqlalchemy import text

from app.tests.conftest_pg import migrate_head, owner_url

# loop de sesión: los engines de Postgres son session-scoped y asyncpg ata la conexión al loop.
pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]


async def test_reaplicar_con_stamp_atrasado_no_falla(pg_admin_engine):
    async with pg_admin_engine.begin() as conn:
        await conn.execute(text("DELETE FROM alembic_version"))
    migrate_head(owner_url())  # re-ejecuta 0001 sobre un esquema que ya existe
    async with pg_admin_engine.connect() as conn:
        version = await conn.scalar(text("SELECT version_num FROM alembic_version"))
        politicas = await conn.scalar(text("SELECT count(*) FROM pg_policies"))
        funciones = await conn.scalar(
            text("SELECT count(*) FROM pg_proc WHERE proname = 'auth_lookup_user'")
        )
    assert version == "0001_inicial"
    # aislamiento en tenants, users, dummy_resources, idempotency_keys + lectura del dueño
    assert politicas == 5
    assert funciones == 1
