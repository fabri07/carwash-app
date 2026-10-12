"""Migración `0005_configuracion_precios` sobre datos existentes — FASE-4-CONTRATO §3.2 y A6.

Se puebla el grafo completo de un lavadero en head, se baja a 0004 (los datos quedan) y se
vuelve a subir: la 0005 tiene que completar `slug`, `tenant_settings` y `pricing_mode` de los
tenants que ya existían. Y si queda un precio NULL, cortar **antes de alterar nada** y decir
cuál.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.tests.conftest_pg import migrate_head, owner_url
from app.tests.persistence.test_cuentas_pg import _alembic
from app.tests.security._poblar_dominio import poblar

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]


@contextmanager
def _en_0004() -> Iterator[None]:
    """Baja a 0004 y, pase lo que pase, deja la base en head (los demás tests la usan)."""
    _alembic("downgrade", "0004_cuentas_permisos")
    try:
        yield
    finally:
        migrate_head(owner_url())


async def test_la_migracion_0005_completa_los_tenants_existentes(
    pg_admin_engine, pg_engine, pg_tenant_factory
):
    tenants = [await pg_tenant_factory("Lavadero Ñandú"), await pg_tenant_factory("Otro")]
    ids = {t: await poblar(pg_admin_engine, t) for t in tenants}
    with _en_0004():
        async with pg_admin_engine.connect() as conn:
            assert not await conn.scalar(text("SELECT to_regclass('public.tenant_settings')"))
        migrate_head(owner_url())
        async with pg_admin_engine.connect() as conn:
            slugs = dict((await conn.execute(text("SELECT id, slug FROM tenants"))).tuples().all())
            ajustes = (
                await conn.execute(
                    text("SELECT tenant_id, deposit_enabled, deposit_bps FROM tenant_settings")
                )
            ).all()
            modos = (
                await conn.execute(
                    text(
                        "SELECT 'booking' AS t, pricing_mode::text FROM booking_items "
                        "UNION ALL SELECT 'job', pricing_mode::text FROM job_items"
                    )
                )
            ).all()
            forzadas = (
                await conn.scalars(
                    text(
                        "SELECT relname FROM pg_class WHERE relname IN "
                        "('tenants', 'tenant_settings', 'services', 'booking_items', "
                        "'job_items') AND relrowsecurity AND relforcerowsecurity"
                    )
                )
            ).all()
    assert set(slugs) == set(tenants)
    assert slugs[tenants[0]].startswith("lavadero-nandu-")
    assert len(set(slugs.values())) == 2
    # Una fila por tenant, con los defaults (la seña apagada al 20 %).
    assert sorted((a.tenant_id, a.deposit_enabled, a.deposit_bps) for a in ajustes) == sorted(
        (t, False, 2000) for t in tenants
    )
    assert sorted(modos) == sorted([("booking", "PRECIO_FIJO"), ("job", "PRECIO_FIJO")] * 2)
    assert set(forzadas) == {"tenants", "tenant_settings", "services", "booking_items", "job_items"}
    assert all(ids.values())
    await pg_engine.dispose()


async def test_la_guarda_corta_antes_de_alterar_nada_y_lista_los_precios_null(
    pg_admin_engine, pg_engine, pg_tenant_factory
):
    tenant = await pg_tenant_factory("Con precio NULL")
    ids = await poblar(pg_admin_engine, tenant)
    with _en_0004():
        async with pg_admin_engine.begin() as conn:
            await conn.execute(
                text("UPDATE service_prices SET price_cents = NULL WHERE id = :i"),
                {"i": ids["service_prices"]},
            )
        with pytest.raises(DBAPIError, match=str(ids["service_prices"])):
            _alembic("upgrade", "head")
        async with pg_admin_engine.connect() as conn:
            assert await conn.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0004_cuentas_permisos"
            )
            assert not await conn.scalar(text("SELECT to_regclass('public.tenant_settings')"))
        # Lo arregla el script de preparación (PR 4.2a) y la migración pasa.
        async with pg_admin_engine.begin() as conn:
            await conn.execute(
                text("UPDATE service_prices SET price_cents = 100 WHERE id = :i"),
                {"i": ids["service_prices"]},
            )
    await pg_engine.dispose()
    assert uuid.UUID(str(tenant))
