"""La migración 0008 sobre datos reales — FASE-4-CONTRATO Y3, Y7, §3.4 y A6 (PR 4.4).

Baja a 0007, carga negocios con SQL crudo, sube y mira lo que quedó:

- **cada negocio existente recibe la base entera** (Y3), sin cruzar marcas entre negocios;
- `vehicles` suma `make_id`, `model_id` y `color_id`; los vehículos existentes quedan en `NULL`;
- el tipo `vehicle_body_type` tiene los valores del dominio;
- RLS forzado de nuevo al terminar, en las tablas nuevas y en las que tocó la copia;
- **idempotente**: re-aplicar con el stamp atrasado no duplica ni "resucita" lo renombrado;
- el downgrade **corta, diciendo por qué**, si perdería algo que hizo el negocio, y baja limpio si
  solo está la base intacta.

`finally` deja la base en el head para los tests que siguen (corren en serie, `-n 0`).
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.domain.vehicle_catalog import VehicleBodyType, load_vehicle_base
from app.main import head_de_alembic
from app.tests.conftest_pg import migrate_head, owner_url
from app.tests.persistence.test_migracion_0005_pg import _alembic, _ejecutar, _version
from app.tests.persistence.test_migracion_0006_pg import _tenant

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

ANTERIOR = "0007_sena_por_servicio"
TABLAS = ("vehicle_makes", "vehicle_models", "vehicle_colors", "body_type_sizes")
BASE = load_vehicle_base()


@pytest_asyncio.fixture
async def en_0007(
    pg_admin_engine: AsyncEngine, pg_clean: None
) -> AsyncGenerator[AsyncEngine, None]:
    _alembic("downgrade", ANTERIOR)
    try:
        yield pg_admin_engine
    finally:
        async with pg_admin_engine.begin() as conn:
            await conn.execute(text("TRUNCATE tenants CASCADE"))
        migrate_head(owner_url())


async def _conteos(admin: AsyncEngine, tenant_id: uuid.UUID) -> tuple[int, int, int]:
    async with admin.connect() as conn:
        fila = (
            await conn.execute(
                text(
                    "SELECT (SELECT count(*) FROM vehicle_makes WHERE tenant_id = :t), "
                    "(SELECT count(*) FROM vehicle_models WHERE tenant_id = :t), "
                    "(SELECT count(*) FROM vehicle_colors WHERE tenant_id = :t)"
                ),
                {"t": tenant_id},
            )
        ).one()
    return int(fila[0]), int(fila[1]), int(fila[2])


async def _vehiculo(admin: AsyncEngine, tenant_id: uuid.UUID) -> uuid.UUID:
    vehiculo_id = uuid.uuid4()
    await _ejecutar(
        admin,
        "INSERT INTO vehicles (id, tenant_id, brand_model, color) VALUES (:i, :t, 'Fiat Cronos', "
        "'Gris')",
        i=vehiculo_id,
        t=tenant_id,
    )
    return vehiculo_id


async def test_copia_la_base_a_cada_negocio_existente(en_0007: AsyncEngine):
    admin = en_0007
    uno = await _tenant(admin, "Lavadero Demo Uno")
    dos = await _tenant(admin, "Lavadero Demo Dos")
    vehiculo = await _vehiculo(admin, uno)
    _alembic("upgrade", "head")
    assert await _version(admin) == head_de_alembic()
    esperado = (len(BASE.makes), BASE.model_count, len(BASE.colors))
    assert await _conteos(admin, uno) == esperado
    assert await _conteos(admin, dos) == esperado
    async with admin.connect() as conn:
        cruzados = await conn.scalar(
            text(
                "SELECT count(*) FROM vehicle_models AS x JOIN vehicle_makes AS m "
                "ON m.id = x.make_id WHERE m.tenant_id <> x.tenant_id"
            )
        )
        assert cruzados == 0
        hilux = (
            await conn.execute(
                text(
                    "SELECT x.body_type::text, x.search_key, x.is_custom, x.hidden_at "
                    "FROM vehicle_models AS x JOIN vehicle_makes AS m ON m.id = x.make_id "
                    "WHERE m.tenant_id = :t AND m.name = 'Toyota' AND x.name = 'Hilux'"
                ),
                {"t": uno},
            )
        ).one()
        assert tuple(hilux) == ("PICKUP", "hilux", False, None)
        citroen = await conn.scalar(
            text("SELECT search_key FROM vehicle_makes WHERE tenant_id = :t AND name = 'Citroën'"),
            {"t": uno},
        )
        assert citroen == "citroen"
        fila = (
            await conn.execute(
                text("SELECT make_id, model_id, color_id, brand_model FROM vehicles WHERE id = :i"),
                {"i": vehiculo},
            )
        ).one()
        assert tuple(fila) == (None, None, None, "Fiat Cronos")
        assert await conn.scalar(text("SELECT count(*) FROM body_type_sizes")) == 0
        en_base = list(
            (await conn.execute(text("SELECT unnest(enum_range(NULL::vehicle_body_type))")))
            .scalars()
            .all()
        )
        assert en_base == [v.value for v in VehicleBodyType]
        filas = await conn.execute(
            text("SELECT relname, relforcerowsecurity FROM pg_class WHERE relname = ANY(:t)"),
            {"t": [*TABLAS, "tenants", "vehicles"]},
        )
        forzadas: dict[str, bool] = {str(r[0]): bool(r[1]) for r in filas}
    assert forzadas == {t: True for t in (*TABLAS, "tenants", "vehicles")}


async def test_reaplicar_no_duplica_ni_resucita_lo_renombrado(en_0007: AsyncEngine):
    admin = en_0007
    uno = await _tenant(admin, "Lavadero Demo Tres")
    _alembic("upgrade", "head")
    await _ejecutar(
        admin,
        "UPDATE vehicle_makes SET name = 'Toyota Arg', search_key = 'toyota arg' "
        "WHERE tenant_id = :t AND name = 'Toyota'",
        t=uno,
    )
    # Stamp atrasado sin tocar el esquema: alembic vuelve a correr la 0008 entera.
    await _ejecutar(admin, f"UPDATE alembic_version SET version_num = '{ANTERIOR}'")
    _alembic("upgrade", "head")
    assert await _conteos(admin, uno) == (len(BASE.makes), BASE.model_count, len(BASE.colors))
    async with admin.connect() as conn:
        toyotas = await conn.scalar(
            text("SELECT count(*) FROM vehicle_makes WHERE tenant_id = :t AND name LIKE 'Toyota%'"),
            {"t": uno},
        )
    assert toyotas == 1


@pytest.mark.parametrize(
    ("ensuciar", "motivo"),
    [
        (
            "INSERT INTO vehicle_makes (id, tenant_id, name, search_key, is_custom) "
            "VALUES (gen_random_uuid(), :t, 'Propia', 'propia', true)",
            "1 marcas propias, ocultas o renombradas",
        ),
        (
            "UPDATE vehicle_makes SET name = 'Fiat Arg', search_key = 'fiat arg' "
            "WHERE tenant_id = :t AND name = 'Fiat'",
            "1 marcas propias, ocultas o renombradas",
        ),
        (
            "UPDATE vehicle_models SET hidden_at = now() WHERE tenant_id = :t AND name = 'Hilux'",
            "1 modelos propios, ocultos o editados",
        ),
        (
            "UPDATE vehicle_models SET body_type = 'SUV' WHERE tenant_id = :t AND name = 'Hilux'",
            "1 modelos propios, ocultos o editados",
        ),
        (
            "UPDATE vehicle_colors SET name = 'Blanco perla' "
            "WHERE tenant_id = :t AND name = 'Blanco'",
            "1 colores propios, ocultos o renombrados",
        ),
        (
            "INSERT INTO vehicle_sizes (id, tenant_id, code, label) "
            "VALUES ('00000000-0000-4000-8000-000000000001', :t, 'AUTO', 'Auto'); "
            "INSERT INTO body_type_sizes (id, tenant_id, body_type, vehicle_size_id) VALUES "
            "(gen_random_uuid(), :t, 'SEDAN', '00000000-0000-4000-8000-000000000001')",
            "1 carrocerias con tamano",
        ),
        (
            "INSERT INTO vehicles (id, tenant_id, color_id) SELECT gen_random_uuid(), :t, id "
            "FROM vehicle_colors WHERE tenant_id = :t AND name = 'Rojo'",
            "1 vehiculos con marca, modelo o color del catalogo",
        ),
    ],
)
async def test_el_downgrade_corta_si_perderia_datos_del_negocio(
    pg_admin_engine: AsyncEngine, pg_clean: None, ensuciar: str, motivo: str
):
    tenant = await _tenant(pg_admin_engine, "Lavadero Demo Cuatro")
    # El tenant nace en el head: se le copia la base como haría la migración.
    await _ejecutar(pg_admin_engine, f"UPDATE alembic_version SET version_num = '{ANTERIOR}'")
    _alembic("upgrade", "head")
    try:
        assert await _conteos(pg_admin_engine, tenant) == (
            len(BASE.makes),
            BASE.model_count,
            len(BASE.colors),
        )
        for sentencia in ensuciar.split("; "):
            await _ejecutar(pg_admin_engine, sentencia, t=tenant)
        with pytest.raises(Exception, match=motivo):
            _alembic("downgrade", ANTERIOR)
        # el downgrade que corta se deshace entero: queda en el head (0009 encima de 0008)
        assert await _version(pg_admin_engine) == head_de_alembic()
    finally:
        async with pg_admin_engine.begin() as conn:
            await conn.execute(text("TRUNCATE tenants CASCADE"))
        migrate_head(owner_url())


async def test_el_downgrade_no_cuenta_lo_anulado(en_0007: AsyncEngine):
    """Lo anulado ya lo descartó el negocio: no frena el downgrade."""
    admin = en_0007
    tenant = await _tenant(admin, "Lavadero Demo Seis")
    _alembic("upgrade", "head")
    tamano = uuid.uuid4()
    await _ejecutar(
        admin,
        "INSERT INTO vehicle_sizes (id, tenant_id, code, label, voided_at, void_reason) "
        "VALUES (:s, :t, 'AUTO', 'Auto', now(), 'PEDIDO_DEL_USUARIO')",
        s=tamano,
        t=tenant,
    )
    for sentencia in (
        "INSERT INTO body_type_sizes "
        "(id, tenant_id, body_type, vehicle_size_id, voided_at, void_reason) "
        "VALUES (gen_random_uuid(), :t, 'SEDAN', :s, now(), 'PEDIDO_DEL_USUARIO')",
        "INSERT INTO vehicle_makes (id, tenant_id, name, search_key, is_custom, voided_at, "
        "void_reason) VALUES (gen_random_uuid(), :t, 'Vieja', 'vieja', true, now(), "
        "'ERROR_DE_CARGA')",
    ):
        await _ejecutar(admin, sentencia, t=tenant, s=tamano)
    _alembic("downgrade", ANTERIOR)
    assert await _version(admin) == ANTERIOR


async def test_el_downgrade_baja_limpio_con_la_base_intacta(en_0007: AsyncEngine):
    admin = en_0007
    tenant = await _tenant(admin, "Lavadero Demo Cinco")
    vehiculo = await _vehiculo(admin, tenant)
    _alembic("upgrade", "head")
    _alembic("downgrade", ANTERIOR)
    assert await _version(admin) == ANTERIOR
    async with admin.connect() as conn:
        for tabla in TABLAS:
            assert await conn.scalar(text(f"SELECT to_regclass('{tabla}') IS NULL")), tabla
        columnas = await conn.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns WHERE table_name = 'vehicles' "
                "AND column_name IN ('make_id', 'model_id', 'color_id')"
            )
        )
        assert columnas == 0
        tipo = await conn.scalar(text("SELECT to_regtype('vehicle_body_type') IS NULL"))
        assert tipo is True
        assert (
            await conn.scalar(text("SELECT count(*) FROM vehicles WHERE id = :i"), {"i": vehiculo})
            == 1
        )
        forzada = await conn.scalar(
            text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'vehicles'")
        )
    assert forzada is True
