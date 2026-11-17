"""La migración 0011 sobre datos reales — FASE-6-CONTRATO §2.

- subir desde 0010: los jobs que ya existían quedan `EN_VIVO`, el tipo tiene los dos valores y
  `jobs` conserva `FORCE ROW LEVEL SECURITY`; subir dos veces no falla (A6);
- el downgrade corta, diciendo por qué, si hay un job `RETROSPECTIVA` vivo, y baja limpio si no;
- la columna no acepta NULL ni un valor fuera del enum.

`finally` deja la base en el head para los tests que siguen (corren en serie, `-n 0`). Datos
sintéticos.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.enums import JobEntryMode
from app.main import head_de_alembic
from app.tests.conftest_pg import migrate_head, owner_url
from app.tests.persistence.test_migracion_0005_pg import _alembic, _ejecutar, _version

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

ANTERIOR = "0010_agenda_f5"


def _modulo():
    import importlib.util  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    ruta = (
        Path(__file__).resolve().parents[2]
        / "persistence/migrations/versions/20261116_0011_operacion_f6.py"
    )
    spec = importlib.util.spec_from_file_location("migracion_0011", ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_la_migracion_congela_lo_mismo_que_el_dominio():
    assert tuple(v.value for v in JobEntryMode) == _modulo().ENTRY_MODES


async def _job(admin, tenant: uuid.UUID, usuario: uuid.UUID, **extra: object) -> uuid.UUID:
    """Un job con SQL crudo (el ORM ya es el de 0011): lo mínimo que exigen los NOT NULL."""
    talle, vehiculo, job = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _ejecutar(
        admin,
        "INSERT INTO vehicle_sizes (id, tenant_id, code, label) VALUES (:id, :t, :c, 'Auto')",
        id=talle,
        t=tenant,
        c="AUTO",
    )
    await _ejecutar(
        admin, "INSERT INTO vehicles (id, tenant_id) VALUES (:id, :t)", id=vehiculo, t=tenant
    )
    columnas = {
        "id": job,
        "tenant_id": tenant,
        "vehicle_id": vehiculo,
        "vehicle_size_id": talle,
        "responsible_user_id": usuario,
        "channel": "CALLE",
        "status": "PRESENTE",
        "base_price_cents": 1000,
        "arrived_at": datetime(2026, 11, 10, 13, tzinfo=UTC),
        **extra,
    }
    nombres = ", ".join(columnas)
    valores = ", ".join(f":{c}" for c in columnas)
    await _ejecutar(admin, f"INSERT INTO jobs ({nombres}) VALUES ({valores})", **columnas)
    return job


async def test_subir_desde_0010_deja_los_jobs_en_vivo_y_es_idempotente(
    pg_admin_engine, pg_tenant_factory, pg_user_factory
):
    tenant = await pg_tenant_factory("Lavadero Demo Centro")
    usuario = await pg_user_factory(tenant, "dueno@example.com")
    _alembic("downgrade", ANTERIOR)
    try:
        existente = await _job(pg_admin_engine, tenant, usuario)
        _alembic("upgrade", "head")
        _alembic("upgrade", "head")  # idempotente
        assert await _version(pg_admin_engine) == head_de_alembic()
        async with pg_admin_engine.connect() as conn:
            modo = await conn.scalar(
                text("SELECT entry_mode::text FROM jobs WHERE id = :j"), {"j": existente}
            )
            valores = set(
                (
                    await conn.execute(text("SELECT unnest(enum_range(NULL::job_entry_mode))"))
                ).scalars()
            )
            forzada = await conn.scalar(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                    "WHERE relname = 'jobs'"
                )
            )
        assert modo == "EN_VIVO"
        assert valores == {"EN_VIVO", "RETROSPECTIVA"}
        assert forzada is True
    finally:
        migrate_head(owner_url())


async def test_reaplicar_con_el_stamp_atrasado_no_falla(pg_admin_engine):
    """A6 puntual de esta migración: la columna y el tipo ya existen y el `upgrade` los tolera."""
    await _ejecutar(pg_admin_engine, "UPDATE alembic_version SET version_num = :v", v=ANTERIOR)
    try:
        _alembic("upgrade", "head")
        assert await _version(pg_admin_engine) == head_de_alembic()
    finally:
        migrate_head(owner_url())


async def test_la_columna_no_acepta_null_ni_valores_ajenos(
    pg_admin_engine, pg_tenant_factory, pg_user_factory
):
    tenant = await pg_tenant_factory("Lavadero Demo Norte")
    usuario = await pg_user_factory(tenant, "dueno@example.com")
    con_default = await _job(pg_admin_engine, tenant, usuario)
    async with pg_admin_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT entry_mode::text FROM jobs WHERE id = :j"), {"j": con_default}
            )
            == "EN_VIVO"
        )
    with pytest.raises(DBAPIError):
        await _job(pg_admin_engine, tenant, usuario, entry_mode=None)
    with pytest.raises(DBAPIError):
        await _job(pg_admin_engine, tenant, usuario, entry_mode="OTRO")


async def test_el_downgrade_corta_si_perderia_la_marca_y_baja_limpio_si_no(
    pg_admin_engine, pg_tenant_factory, pg_user_factory
):
    tenant = await pg_tenant_factory("Lavadero Demo Sur")
    usuario = await pg_user_factory(tenant, "dueno@example.com")
    retro = await _job(pg_admin_engine, tenant, usuario, entry_mode="RETROSPECTIVA")
    with pytest.raises(Exception, match="carga retrospectiva"):
        _alembic("downgrade", ANTERIOR)
    assert await _version(pg_admin_engine) == head_de_alembic()

    await _ejecutar(
        pg_admin_engine,
        "UPDATE jobs SET voided_at = now(), void_reason = 'ERROR_DE_CARGA' WHERE id = :j",
        j=retro,
    )
    try:
        _alembic("downgrade", ANTERIOR)
        assert await _version(pg_admin_engine) == ANTERIOR
        async with pg_admin_engine.connect() as conn:
            assert (
                await conn.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'jobs' AND column_name = 'entry_mode'"
                    )
                )
                == 0
            )
            assert await conn.scalar(text("SELECT to_regtype('job_entry_mode')")) is None
            forzada = await conn.scalar(
                text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'jobs'")
            )
        assert forzada is True
    finally:
        migrate_head(owner_url())
    assert await _version(pg_admin_engine) == head_de_alembic()
