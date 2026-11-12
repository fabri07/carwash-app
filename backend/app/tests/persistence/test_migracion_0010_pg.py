"""La migración 0010 sobre datos reales — FASE-5-CONTRATO §3, B5 y B6.

- subir desde 0009: los servicios quedan en `TURNERO`, la configuración con los defaults de
  reservas, los valores nuevos de `booking_status` existen y las tablas nuevas tienen RLS forzado;
  subir dos veces no falla (A6);
- un negocio vivo con un slug que pasó a reservado **corta** la migración, diciendo cuál;
- el downgrade corta si perdería datos de la agenda y baja limpio si no (los valores del enum
  quedan, ver el docstring de la migración);
- `public_tenant_by_slug`: vivo con el alta terminada → id; sin alta, anulado o inexistente →
  NULL; el rol de runtime sigue sin ver `tenants` sin contexto; la función es segura.

`finally` deja la base en el head para los tests que siguen (corren en serie, `-n 0`). Datos
sintéticos.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.domain.enums import BookingStatus, ServiceBookingMode
from app.domain.slug import RESERVED_SLUGS
from app.main import head_de_alembic
from app.persistence.db.tenant_context import set_tenant_context
from app.tests.conftest_pg import migrate_head, owner_url
from app.tests.persistence.test_migracion_0005_pg import _alembic, _ejecutar, _version
from app.tests.persistence.test_migracion_0006_pg import _servicio, _tenant

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

ANTERIOR = "0009_onboarding_existentes"
NUEVAS = ("service_schedule_rules", "schedule_openings", "customer_incident_marks")


def _modulo():
    import importlib.util  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    ruta = (
        Path(__file__).resolve().parents[2]
        / "persistence/migrations/versions/20261111_0010_agenda_f5.py"
    )
    spec = importlib.util.spec_from_file_location("migracion_0010", ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_la_migracion_congela_lo_mismo_que_el_dominio():
    m = _modulo()
    assert set(m.RESERVED_SLUGS) == RESERVED_SLUGS
    assert tuple(v.value for v in ServiceBookingMode) == m.BOOKING_MODES
    assert tuple(v.value for v in list(BookingStatus)[-2:]) == m.BOOKING_STATUS_ADDED


async def _con_settings(admin, nombre: str, slug: str | None = None) -> uuid.UUID:
    tenant = await _tenant(admin, nombre)
    if slug is not None:
        await _ejecutar(admin, "UPDATE tenants SET slug = :s WHERE id = :t", s=slug, t=tenant)
    await _ejecutar(
        admin,
        "INSERT INTO tenant_settings (id, tenant_id) VALUES (gen_random_uuid(), :t)",
        t=tenant,
    )
    return tenant


async def test_subir_desde_0009_deja_defaults_y_es_idempotente(pg_admin_engine, pg_clean):
    _alembic("downgrade", ANTERIOR)
    try:
        tenant = await _con_settings(pg_admin_engine, "Lavadero Demo Centro")
        await _servicio(pg_admin_engine, tenant, "Lavado exterior", search_key="lavado exterior")
        _alembic("upgrade", "head")
        _alembic("upgrade", "head")  # idempotente
        assert await _version(pg_admin_engine) == head_de_alembic()
        async with pg_admin_engine.connect() as conn:
            assert await conn.scalar(text("SELECT booking_mode::text FROM services")) == "TURNERO"
            fila = (
                await conn.execute(
                    text(
                        "SELECT booking_horizon_days, slot_step_min, deposit_instructions "
                        "FROM tenant_settings WHERE tenant_id = :t"
                    ),
                    {"t": tenant},
                )
            ).one()
            assert tuple(fila) == (14, 30, None)
            valores = set(
                (
                    await conn.execute(text("SELECT unnest(enum_range(NULL::booking_status))"))
                ).scalars()
            )
            assert {"PENDIENTE_REVISION", "RECHAZADO"} <= valores
            sin_forzar = await conn.scalar(
                text(
                    "SELECT count(*) FROM pg_class WHERE relname = ANY(:t) "
                    "AND NOT (relrowsecurity AND relforcerowsecurity)"
                ),
                {"t": [*NUEVAS, "tenants", "tenant_settings", "services", "bookings"]},
            )
        assert sin_forzar == 0
    finally:
        migrate_head(owner_url())


async def test_un_slug_que_paso_a_reservado_corta_la_migracion(pg_admin_engine, pg_clean):
    _alembic("downgrade", ANTERIOR)
    try:
        tenant = await _con_settings(pg_admin_engine, "Catalogo Lavadero", slug="catalogo")
        with pytest.raises(Exception, match="catalogo"):
            _alembic("upgrade", "head")
        assert await _version(pg_admin_engine) == ANTERIOR
        await _ejecutar(
            pg_admin_engine, "UPDATE tenants SET slug = 'catalogo-centro' WHERE id = :t", t=tenant
        )
        _alembic("upgrade", "head")
        assert await _version(pg_admin_engine) == head_de_alembic()
    finally:
        migrate_head(owner_url())


async def test_un_slug_reservado_anulado_no_corta(pg_admin_engine, pg_clean):
    _alembic("downgrade", ANTERIOR)
    try:
        tenant = await _con_settings(pg_admin_engine, "Register Viejo", slug="register")
        await _ejecutar(
            pg_admin_engine,
            "UPDATE tenants SET voided_at = now(), void_reason = 'DESACTIVADO' WHERE id = :t",
            t=tenant,
        )
        _alembic("upgrade", "head")
        assert await _version(pg_admin_engine) == head_de_alembic()
    finally:
        migrate_head(owner_url())


async def test_el_downgrade_corta_si_perderia_datos_y_baja_limpio_si_no(pg_admin_engine, pg_clean):
    tenant = await _con_settings(pg_admin_engine, "Lavadero Demo Norte")
    await _servicio(
        pg_admin_engine,
        tenant,
        "Lavado de camión",
        search_key="lavado de camion",
        booking_mode="SOLO_A_MANO",
    )
    await _ejecutar(
        pg_admin_engine,
        "UPDATE tenant_settings SET deposit_instructions = 'Alias demo' WHERE tenant_id = :t",
        t=tenant,
    )
    with pytest.raises(Exception, match="servicios solo a mano"):
        _alembic("downgrade", ANTERIOR)
    assert await _version(pg_admin_engine) == head_de_alembic()

    await _ejecutar(pg_admin_engine, "UPDATE services SET booking_mode = 'TURNERO'")
    await _ejecutar(pg_admin_engine, "UPDATE tenant_settings SET deposit_instructions = NULL")
    try:
        _alembic("downgrade", ANTERIOR)
        assert await _version(pg_admin_engine) == ANTERIOR
        async with pg_admin_engine.connect() as conn:
            for tabla in NUEVAS:
                assert await conn.scalar(text(f"SELECT to_regclass('public.{tabla}')")) is None
            assert (
                await conn.scalar(
                    text("SELECT count(*) FROM pg_proc WHERE proname = 'public_tenant_by_slug'")
                )
                == 0
            )
            assert await conn.scalar(text("SELECT count(*) FROM services")) == 1
            # Los valores del enum quedan (Postgres no los puede sacar sin recrear el tipo).
            valores = set(
                (
                    await conn.execute(text("SELECT unnest(enum_range(NULL::booking_status))"))
                ).scalars()
            )
            assert "PENDIENTE_REVISION" in valores
    finally:
        migrate_head(owner_url())
    assert await _version(pg_admin_engine) == head_de_alembic()


# ── B6 · public_tenant_by_slug ────────────────────────────────────────────────


async def _buscar(pg_session_factory, slug: str) -> uuid.UUID | None:
    async with pg_session_factory() as session, session.begin():
        return await session.scalar(text("SELECT public_tenant_by_slug(:s)"), {"s": slug})


async def test_public_tenant_by_slug_solo_devuelve_un_negocio_vivo_y_dado_de_alta(
    pg_admin_engine, pg_session_factory, pg_tenant_factory
):
    listo = await pg_tenant_factory("Lavadero Listo")
    a_medias = await pg_tenant_factory("Lavadero A Medias")
    anulado = await pg_tenant_factory("Lavadero Cerrado")
    await _ejecutar(
        pg_admin_engine,
        "UPDATE tenant_settings SET onboarding_completed_at = :c WHERE tenant_id = ANY(:t)",
        c=datetime(2026, 11, 1, tzinfo=UTC),
        t=[listo, anulado],
    )
    await _ejecutar(
        pg_admin_engine,
        "UPDATE tenants SET voided_at = now(), void_reason = 'DESACTIVADO' WHERE id = :t",
        t=anulado,
    )
    slugs = {}
    async with pg_admin_engine.connect() as conn:
        for tenant in (listo, a_medias, anulado):
            slugs[tenant] = await conn.scalar(
                text("SELECT slug FROM tenants WHERE id = :t"), {"t": tenant}
            )

    assert await _buscar(pg_session_factory, slugs[listo]) == listo
    assert await _buscar(pg_session_factory, slugs[listo].upper()) == listo
    assert await _buscar(pg_session_factory, slugs[a_medias]) is None
    assert await _buscar(pg_session_factory, slugs[anulado]) is None
    assert await _buscar(pg_session_factory, "no-existe-este-negocio") is None

    # El rol de runtime sigue sin ver `tenants` ni `tenant_settings` sin contexto.
    async with pg_session_factory() as session, session.begin():
        assert await session.scalar(text("SELECT count(*) FROM tenants")) == 0
        assert await session.scalar(text("SELECT count(*) FROM tenant_settings")) == 0
    # Con contexto ve solo lo suyo, como siempre.
    async with pg_session_factory() as session, session.begin():
        await set_tenant_context(session, listo)
        assert await session.scalar(text("SELECT count(*) FROM tenants")) == 1


async def test_public_tenant_by_slug_es_segura(pg_admin_engine):
    async with pg_admin_engine.connect() as conn:
        f = (
            await conn.execute(
                text(
                    "SELECT p.prosecdef, pg_get_userbyid(p.proowner) AS owner, p.proconfig, "
                    "has_function_privilege('public', p.oid, 'EXECUTE') AS publico, "
                    "has_function_privilege('carwash_app', p.oid, 'EXECUTE') AS app "
                    "FROM pg_proc p WHERE p.proname = 'public_tenant_by_slug'"
                )
            )
        ).one()
    assert f.prosecdef is True
    assert f.owner == "carwash_owner"
    assert any(c.startswith("search_path=") for c in f.proconfig)
    assert f.publico is False and f.app is True
