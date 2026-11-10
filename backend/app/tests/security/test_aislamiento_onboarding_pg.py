"""Wizard de alta contra Postgres real (PR 4.6), con el rol de runtime `carwash_app` (RLS activo).

1. `OnboardingService` de B no cuenta el catálogo de A (aunque A lo tenga completo) y completar A
   no toca la fila de B.
2. Dos "Terminar" a la vez del mismo negocio se ordenan con `FOR UPDATE` sobre `tenant_settings`:
   los dos responden con la **misma** fecha (el segundo ve la del primero, no la pisa).

El superusuario solo siembra y es el testigo. Datos sintéticos.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.services.onboarding_service import (
    OnboardingIncompleteError,
    OnboardingService,
)

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]


async def _sembrar_catalogo(admin: AsyncEngine, tenant_id: uuid.UUID) -> None:
    size_id, service_id = uuid.uuid4(), uuid.uuid4()
    async with admin.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO vehicle_sizes (id, tenant_id, code, label) "
                "VALUES (:id, :t, 'AUTO', 'Auto')"
            ),
            {"id": size_id, "t": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO services (id, tenant_id, name, search_key, pricing_mode) "
                "VALUES (:id, :t, 'Lavado', 'lavado', 'PRECIO_FIJO')"
            ),
            {"id": service_id, "t": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO service_prices "
                "(id, tenant_id, service_id, vehicle_size_id, price_cents, duration_min) "
                "VALUES (gen_random_uuid(), :t, :s, :z, 1500000, 60)"
            ),
            {"t": tenant_id, "s": service_id, "z": size_id},
        )
        await conn.execute(
            text(
                "INSERT INTO resources (id, tenant_id, name) "
                "VALUES (gen_random_uuid(), :t, 'Box 1')"
            ),
            {"t": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO business_hours (id, tenant_id, weekday, opens_at, closes_at) "
                "VALUES (gen_random_uuid(), :t, 1, '09:00', '18:00')"
            ),
            {"t": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO payment_methods (id, tenant_id, code, label) "
                "VALUES (gen_random_uuid(), :t, 'EFECTIVO', 'Efectivo')"
            ),
            {"t": tenant_id},
        )


async def _completado(admin: AsyncEngine, tenant_id: uuid.UUID) -> object:
    async with admin.connect() as conn:
        return await conn.scalar(
            text("SELECT onboarding_completed_at FROM tenant_settings WHERE tenant_id = :t"),
            {"t": tenant_id},
        )


async def test_b_no_cuenta_el_catalogo_de_a(
    pg_admin_engine, pg_session_factory, pg_tenant_a, pg_tenant_b
):
    await _sembrar_catalogo(pg_admin_engine, pg_tenant_a)

    async with pg_session_factory() as s, s.begin():
        estado_a = await OnboardingService(s, pg_tenant_a, None).status()
    assert estado_a.missing == ()

    async with pg_session_factory() as s, s.begin():
        estado_b = await OnboardingService(s, pg_tenant_b, None).status()
    assert {m.requirement.value for m in estado_b.missing} == {
        "VEHICLE_SIZE",
        "SERVICE",
        "RESOURCE",
        "BUSINESS_HOURS",
        "PAYMENT_METHOD",
    }

    async with pg_session_factory() as s, s.begin():
        await OnboardingService(s, pg_tenant_a, None).complete()
    with pytest.raises(OnboardingIncompleteError):
        async with pg_session_factory() as s, s.begin():
            await OnboardingService(s, pg_tenant_b, None).complete()

    assert await _completado(pg_admin_engine, pg_tenant_a) is not None
    assert await _completado(pg_admin_engine, pg_tenant_b) is None


async def test_dos_terminar_a_la_vez_dejan_una_sola_fecha(
    pg_admin_engine, pg_session_factory, pg_tenant_a
):
    await _sembrar_catalogo(pg_admin_engine, pg_tenant_a)
    tomada = asyncio.Event()
    soltar = asyncio.Event()

    async def primero():
        async with pg_session_factory() as s, s.begin():
            hecho = await OnboardingService(s, pg_tenant_a, None).complete()
            tomada.set()
            await soltar.wait()  # retiene la fila hasta que el segundo esté esperando
            return hecho.completed_at

    async def segundo():
        await tomada.wait()
        async with pg_session_factory() as s, s.begin():
            tarea = asyncio.ensure_future(OnboardingService(s, pg_tenant_a, None).complete())
            await asyncio.sleep(0.2)
            assert not tarea.done(), "el segundo debió esperar el FOR UPDATE del primero"
            soltar.set()
            return (await tarea).completed_at

    fecha_1, fecha_2 = await asyncio.gather(primero(), segundo())
    assert fecha_1 is not None and fecha_1 == fecha_2
    guardada = await _completado(pg_admin_engine, pg_tenant_a)
    assert guardada == fecha_1
