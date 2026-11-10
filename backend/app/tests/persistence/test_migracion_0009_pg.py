"""La migración 0009 sobre datos reales — FASE-4-CONTRATO §11.9 (4.6-8) y A6.

- un negocio que ya cumple los requisitos de §6 queda con el alta terminada;
- uno al que le falta algo (un precio para un tamaño, un medio de cobro) sigue en `NULL`;
- lo anulado no cuenta;
- una fecha que ya estaba no se pisa, y re-aplicar no cambia nada;
- las tablas vuelven a quedar con RLS forzado;
- el downgrade es no-op: no le saca el alta a nadie.

`finally` deja la base en el head para los tests que siguen (corren en serie, `-n 0`). Datos
sintéticos.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.main import head_de_alembic
from app.tests.conftest_pg import migrate_head, owner_url
from app.tests.persistence.test_migracion_0005_pg import _alembic, _ejecutar, _version
from app.tests.persistence.test_migracion_0006_pg import _tenant

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

ANTERIOR = "0008_catalogo_vehiculos"
ESTA = "0009_onboarding_existentes"
FECHA_PREVIA = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


async def _negocio(
    admin: AsyncEngine,
    nombre: str,
    *,
    tamanos: int = 1,
    precios_faltantes: int = 0,
    cobro: bool = True,
    anular_puesto: bool = False,
    completado: datetime | None = None,
) -> uuid.UUID:
    """Un negocio con su catálogo; por defecto cumple todo lo de §6."""
    tenant = await _tenant(admin, nombre)
    await _ejecutar(
        admin,
        "INSERT INTO tenant_settings (id, tenant_id, onboarding_completed_at) "
        "VALUES (gen_random_uuid(), :t, :c)",
        t=tenant,
        c=completado,
    )
    servicio = uuid.uuid4()
    await _ejecutar(
        admin,
        "INSERT INTO services (id, tenant_id, name, search_key, pricing_mode) "
        "VALUES (:id, :t, 'Lavado', 'lavado', 'PRECIO_FIJO')",
        id=servicio,
        t=tenant,
    )
    for i in range(tamanos):
        tamano = uuid.uuid4()
        await _ejecutar(
            admin,
            "INSERT INTO vehicle_sizes (id, tenant_id, code, label) VALUES (:id, :t, :c, :c)",
            id=tamano,
            t=tenant,
            c=f"T{i}",
        )
        if i >= tamanos - precios_faltantes:
            continue
        await _ejecutar(
            admin,
            "INSERT INTO service_prices "
            "(id, tenant_id, service_id, vehicle_size_id, price_cents, duration_min) "
            "VALUES (gen_random_uuid(), :t, :s, :z, 1500000, 60)",
            t=tenant,
            s=servicio,
            z=tamano,
        )
    await _ejecutar(
        admin,
        "INSERT INTO resources (id, tenant_id, name, voided_at, void_reason) "
        "VALUES (gen_random_uuid(), :t, 'Box 1', :v, :r)",
        t=tenant,
        v=datetime.now(UTC) if anular_puesto else None,
        r="DESACTIVADO" if anular_puesto else None,
    )
    await _ejecutar(
        admin,
        "INSERT INTO business_hours (id, tenant_id, weekday, opens_at, closes_at) "
        "VALUES (gen_random_uuid(), :t, 1, '09:00', '18:00')",
        t=tenant,
    )
    await _ejecutar(
        admin,
        "INSERT INTO payment_methods (id, tenant_id, code, label, for_income, for_expense) "
        "VALUES (gen_random_uuid(), :t, 'EFECTIVO', 'Efectivo', :i, true)",
        t=tenant,
        i=cobro,
    )
    return tenant


async def _completado(admin: AsyncEngine, tenant: uuid.UUID) -> datetime | None:
    async with admin.connect() as conn:
        return await conn.scalar(
            text("SELECT onboarding_completed_at FROM tenant_settings WHERE tenant_id = :t"),
            {"t": tenant},
        )


async def test_marca_solo_a_los_que_cumplen(pg_admin_engine, pg_clean):
    _alembic("downgrade", ANTERIOR)
    try:
        completo = await _negocio(pg_admin_engine, "Lavadero Completo", tamanos=2)
        sin_precio = await _negocio(
            pg_admin_engine, "Lavadero Sin Precio", tamanos=2, precios_faltantes=1
        )
        sin_cobro = await _negocio(pg_admin_engine, "Lavadero Sin Cobro", cobro=False)
        puesto_anulado = await _negocio(pg_admin_engine, "Lavadero Sin Puesto", anular_puesto=True)
        previo = await _negocio(pg_admin_engine, "Lavadero Previo", completado=FECHA_PREVIA)
        vacio = await _tenant(pg_admin_engine, "Lavadero Vacio")
        await _ejecutar(
            pg_admin_engine,
            "INSERT INTO tenant_settings (id, tenant_id) VALUES (gen_random_uuid(), :t)",
            t=vacio,
        )

        _alembic("upgrade", "head")
        assert await _version(pg_admin_engine) == head_de_alembic() == ESTA

        marcada = await _completado(pg_admin_engine, completo)
        assert marcada is not None
        for tenant in (sin_precio, sin_cobro, puesto_anulado, vacio):
            assert await _completado(pg_admin_engine, tenant) is None
        assert await _completado(pg_admin_engine, previo) == FECHA_PREVIA

        # idempotente: re-aplicar no cambia la fecha
        _alembic("downgrade", ANTERIOR)
        _alembic("upgrade", "head")
        assert await _completado(pg_admin_engine, completo) == marcada

        async with pg_admin_engine.connect() as conn:
            sin_forzar = await conn.scalar(
                text(
                    "SELECT count(*) FROM pg_class WHERE relname = ANY(:t) "
                    "AND NOT relforcerowsecurity"
                ),
                {
                    "t": [
                        "tenant_settings",
                        "vehicle_sizes",
                        "services",
                        "service_prices",
                        "resources",
                        "business_hours",
                        "payment_methods",
                    ]
                },
            )
        assert sin_forzar == 0
    finally:
        migrate_head(owner_url())


async def test_el_downgrade_no_le_saca_el_alta_a_nadie(pg_admin_engine, pg_clean):
    completo = await _negocio(pg_admin_engine, "Lavadero Terminado", completado=FECHA_PREVIA)
    try:
        _alembic("downgrade", ANTERIOR)
        assert await _version(pg_admin_engine) == ANTERIOR
        assert await _completado(pg_admin_engine, completo) == FECHA_PREVIA
    finally:
        migrate_head(owner_url())
    assert await _version(pg_admin_engine) == head_de_alembic()
