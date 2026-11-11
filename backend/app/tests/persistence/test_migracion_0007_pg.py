"""La migración 0007 sobre datos reales — FASE-4-CONTRATO §11.6 (D4-17) y A6.

- los servicios existentes quedan con `deposit_bps` NULL (usan la seña del negocio);
- el CHECK de rango vale en la base (1..10000 o NULL);
- es idempotente: subir dos veces no falla;
- el downgrade **corta, diciendo por qué**, si algún servicio tiene % propio, y baja limpio si no.

`finally` deja la base en el head para los tests que siguen (corren en serie, `-n 0`).
"""

import pytest
from sqlalchemy import text

from app.main import head_de_alembic
from app.tests.conftest_pg import migrate_head, owner_url
from app.tests.persistence.test_migracion_0005_pg import _alembic, _ejecutar, _version
from app.tests.persistence.test_migracion_0006_pg import _servicio, _tenant

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

ANTERIOR = "0006_servicios_categorias"


async def test_los_servicios_existentes_quedan_sin_sena_propia(pg_admin_engine, pg_clean):
    _alembic("downgrade", ANTERIOR)
    try:
        tenant = await _tenant(pg_admin_engine, "Lavadero Demo Sur")
        await _servicio(pg_admin_engine, tenant, "Lavado exterior", search_key="lavado exterior")
        _alembic("upgrade", "head")
        _alembic("upgrade", "head")  # idempotente
        assert await _version(pg_admin_engine) == head_de_alembic()
        async with pg_admin_engine.connect() as conn:
            assert await conn.scalar(text("SELECT deposit_bps FROM services")) is None
            forzado = await conn.scalar(
                text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'services'")
            )
        assert forzado is True
    finally:
        migrate_head(owner_url())


@pytest.mark.parametrize(
    ("valor", "entra"), [(None, True), (1, True), (10000, True), (0, False), (10001, False)]
)
async def test_el_rango_de_la_sena_propia_lo_cuida_la_base(pg_admin_engine, pg_clean, valor, entra):
    tenant = await _tenant(pg_admin_engine, "Lavadero Demo Este")
    alta = _servicio(
        pg_admin_engine, tenant, "Lavado camión", search_key="lavado camion", deposit_bps=valor
    )
    if entra:
        await alta
    else:
        with pytest.raises(Exception, match="ck_services_sena_bps_rango"):
            await alta


async def test_el_downgrade_corta_si_perderia_la_sena_propia(pg_admin_engine, pg_clean):
    tenant = await _tenant(pg_admin_engine, "Lavadero Demo Oeste")
    await _servicio(
        pg_admin_engine, tenant, "Lavado camión", search_key="lavado camion", deposit_bps=3000
    )
    with pytest.raises(Exception, match="se perderia la sena propia de 1 servicios"):
        _alembic("downgrade", ANTERIOR)
    assert await _version(pg_admin_engine) == head_de_alembic()

    await _ejecutar(pg_admin_engine, "UPDATE services SET deposit_bps = NULL")
    try:
        _alembic("downgrade", ANTERIOR)
        assert await _version(pg_admin_engine) == ANTERIOR
        async with pg_admin_engine.connect() as conn:
            columna = await conn.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_name = 'services' AND column_name = 'deposit_bps'"
                )
            )
            assert columna == 0
            assert await conn.scalar(text("SELECT count(*) FROM services")) == 1
    finally:
        migrate_head(owner_url())
    assert await _version(pg_admin_engine) == head_de_alembic()
