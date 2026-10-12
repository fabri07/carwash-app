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
    # El segundo lavadero, sin cotización en su turno ni en su job: sus ítems son de precio fijo.
    async with pg_admin_engine.begin() as conn:
        for tabla in ("bookings", "jobs"):
            await conn.execute(
                text(f"UPDATE {tabla} SET quote_id = NULL WHERE tenant_id = :t"), {"t": tenants[1]}
            )
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
                        "SELECT tenant_id, 'booking' AS t, pricing_mode::text FROM booking_items "
                        "UNION ALL SELECT tenant_id, 'job', pricing_mode::text FROM job_items"
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
    # La modalidad sale de la cotización del turno o del job (la de F3 al reservar), no de la
    # modalidad actual del servicio, que pudo cambiar.
    assert sorted(modos) == sorted(
        [(tenants[0], t, "A_COTIZAR") for t in ("booking", "job")]
        + [(tenants[1], t, "PRECIO_FIJO") for t in ("booking", "job")]
    )
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


async def test_la_bajada_vuelve_a_dejar_sin_precio_lo_a_cotizar(
    pg_admin_engine, pg_engine, pg_tenant_factory
):
    """El código de 0004 reconoce lo "a cotizar" por el precio NULL: bajar con precios base
    cargados en servicios e ítems a cotizar los dejaría incoherentes para ese código."""
    tenant = await pg_tenant_factory("A cotizar")
    ids = await poblar(pg_admin_engine, tenant)
    async with pg_admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE services SET pricing_mode = 'A_COTIZAR' WHERE id = :i"),
            {"i": ids["services"]},
        )
        await conn.execute(
            text("UPDATE booking_items SET pricing_mode = 'A_COTIZAR' WHERE id = :i"),
            {"i": ids["booking_items"]},
        )
    with _en_0004():
        async with pg_admin_engine.begin() as conn:
            precio = await conn.scalar(
                text("SELECT price_cents FROM service_prices WHERE id = :i"),
                {"i": ids["service_prices"]},
            )
            item = (
                await conn.execute(
                    text("SELECT price_cents, deposit_bps FROM booking_items WHERE id = :i"),
                    {"i": ids["booking_items"]},
                )
            ).one()
            # Para volver a subir hace falta el precio (lo que hace el script de preparación).
            await conn.execute(text("UPDATE service_prices SET price_cents = 100"))
            await conn.execute(text("UPDATE booking_items SET price_cents = 100"))
    assert precio is None
    assert (item.price_cents, item.deposit_bps) == (None, 0)
    await pg_engine.dispose()
