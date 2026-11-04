"""La migración 0005 sobre datos reales — FASE-4-CONTRATO §3.2 y A6.

`test_migraciones_idempotentes_pg` prueba que se puede re-aplicar; acá se prueba que **hace lo
que dice sobre filas que ya existen**, que es lo que pasa en staging: baja a 0004, carga datos con
SQL crudo (el ORM ya es el de 0005), sube, y mira lo que quedó.

Lo que no se puede dejar de probar:

- **corta antes de alterar** si queda un precio NULL (y el rollback deja a 0004 intacta);
- rellena `slug`, `tenant_settings`, `pricing_mode` y el precio de los turnos a cotizar;
- el `EXCLUDE` recreado ya no bloquea un turno a cotizar, y sigue bloqueando a los demás;
- el downgrade corta, diciendo por qué, si hay un turno a cotizar sin hold.

`finally` deja la base en el head para los tests que siguen (corren en serie, `-n 0`).
"""

import os
import re
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.main import head_de_alembic
from app.persistence.db.base import Base
from app.persistence.models.tenant import (
    DEFAULT_DEPOSIT_BPS,
    DEFAULT_HOLD_MIN,
    DEFAULT_LATE_CANCEL_THRESHOLD_MIN,
    DEFAULT_MIN_ADVANCE_MIN,
    DEFAULT_QUOTE_VALIDITY_HOURS,
    DEFAULT_TOLERANCE_MIN,
)
from app.tests.conftest_pg import _with_driver, migrate_head, owner_url

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

ANTERIOR = "0004_cuentas_permisos"
INICIO = datetime(2026, 11, 10, 13, tzinfo=UTC)


def _alembic(accion: str, revision: str) -> None:
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "app/persistence/migrations"))
    previous = os.environ.get("DATABASE_URL_SYNC")
    os.environ["DATABASE_URL_SYNC"] = _with_driver(owner_url(), "psycopg2")
    try:
        getattr(command, accion)(cfg, revision)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL_SYNC", None)
        else:  # pragma: no cover  # solo si el entorno ya traía la variable
            os.environ["DATABASE_URL_SYNC"] = previous


@pytest_asyncio.fixture
async def en_0004(
    pg_admin_engine: AsyncEngine, pg_clean: None
) -> AsyncGenerator[AsyncEngine, None]:
    """La base en 0004, vacía; al terminar, otra vez en el head."""
    _alembic("downgrade", ANTERIOR)
    try:
        yield pg_admin_engine
    finally:
        # Si el test falló a mitad de camino, quedan filas que la migración rechaza (precios NULL):
        # se vacía antes de volver al head para no contaminar a los tests que siguen.
        async with pg_admin_engine.begin() as conn:
            await conn.execute(text("TRUNCATE tenants CASCADE"))
        migrate_head(owner_url())


async def _version(admin: AsyncEngine) -> str:
    async with admin.connect() as conn:
        return str(await conn.scalar(text("SELECT version_num FROM alembic_version")))


async def _ejecutar(admin: AsyncEngine, sql: str, **params: object) -> None:
    async with admin.begin() as conn:
        await conn.execute(text(sql), params)


async def _tenant(admin: AsyncEngine, nombre: str) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    await _ejecutar(
        admin, "INSERT INTO tenants (id, name) VALUES (:id, :n)", id=tenant_id, n=nombre
    )
    return tenant_id


async def _catalogo(
    admin: AsyncEngine, tenant_id: uuid.UUID, *, precio_cotizar: int | None
) -> dict:
    """Un tamaño, un servicio fijo y uno a cotizar con su precio, un puesto y un cliente."""
    ids = {k: uuid.uuid4() for k in ("size", "fijo", "cotizar", "puesto", "cliente")}
    base = {"t": tenant_id}
    await _ejecutar(
        admin,
        "INSERT INTO vehicle_sizes (id, tenant_id, code, label) VALUES (:i, :t, 'AUTO', 'Auto')",
        i=ids["size"],
        **base,
    )
    for clave, nombre, modo in (
        ("fijo", "Lavado", "PRECIO_FIJO"),
        ("cotizar", "Tapizado", "A_COTIZAR"),
    ):
        await _ejecutar(
            admin,
            "INSERT INTO services (id, tenant_id, name, pricing_mode) VALUES (:i, :t, :n, :m)",
            i=ids[clave],
            n=nombre,
            m=modo,
            **base,
        )
    await _ejecutar(
        admin,
        "INSERT INTO service_prices"
        " (id, tenant_id, service_id, vehicle_size_id, price_cents, duration_min)"
        " VALUES (gen_random_uuid(), :t, :s, :z, 2000000, 60)",
        s=ids["fijo"],
        z=ids["size"],
        **base,
    )
    await _ejecutar(
        admin,
        "INSERT INTO service_prices"
        " (id, tenant_id, service_id, vehicle_size_id, price_cents, duration_min)"
        " VALUES (gen_random_uuid(), :t, :s, :z, :p, 120)",
        s=ids["cotizar"],
        z=ids["size"],
        p=precio_cotizar,
        **base,
    )
    await _ejecutar(
        admin,
        "INSERT INTO resources (id, tenant_id, name) VALUES (:i, :t, 'Puesto 1')",
        i=ids["puesto"],
        **base,
    )
    await _ejecutar(
        admin,
        "INSERT INTO customers (id, tenant_id, name) VALUES (:i, :t, 'Cliente Uno')",
        i=ids["cliente"],
        **base,
    )
    return ids


async def _turno(
    admin: AsyncEngine,
    tenant_id: uuid.UUID,
    ids: dict,
    *,
    estado: str,
    inicio: datetime = INICIO,
    precio: int | None = None,
    hold: datetime | None = None,
) -> uuid.UUID:
    booking_id = uuid.uuid4()
    await _ejecutar(
        admin,
        "INSERT INTO bookings (id, tenant_id, code, source, channel, status, resource_id,"
        " start_at, end_at,"
        " hold_expires_at, customer_id, vehicle_size_id, duration_min, price_cents)"
        " VALUES (:id, :t, :code, 'PANEL', 'WHATSAPP', :st, :r, :s, :e, :h, :c, :z, 60, :p)",
        id=booking_id,
        t=tenant_id,
        code=f"TUR-{booking_id.hex[:8]}",
        st=estado,
        r=ids["puesto"],
        s=inicio,
        e=inicio + timedelta(minutes=60),
        h=hold,
        c=ids["cliente"],
        z=ids["size"],
        p=precio,
    )
    return booking_id


async def test_corta_antes_de_alterar_si_hay_un_precio_nulo(en_0004: AsyncEngine):
    admin = en_0004
    tenant_id = await _tenant(admin, "Lavadero Demo Norte")
    await _catalogo(admin, tenant_id, precio_cotizar=None)

    with pytest.raises(Exception, match="precios NULL"):
        migrate_head(owner_url())

    # La transacción entera se deshizo: sigue en 0004, sin `slug` ni `tenant_settings`.
    assert await _version(admin) == ANTERIOR
    async with admin.connect() as conn:
        assert not await conn.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns"
                " WHERE table_name = 'tenants' AND column_name = 'slug'"
            )
        )
        assert not await conn.scalar(text("SELECT to_regclass('tenant_settings') IS NOT NULL"))

    # Con el precio completado, migra.
    await _ejecutar(
        admin, "UPDATE service_prices SET price_cents = 3000000 WHERE price_cents IS NULL"
    )
    migrate_head(owner_url())
    assert await _version(admin) == head_de_alembic()


async def test_rellena_slug_settings_modalidad_y_precio_del_turno(en_0004: AsyncEngine):
    admin = en_0004
    norte = await _tenant(admin, "Lavadero Demo Norte")
    tilde = await _tenant(admin, "Ñandú & Hijos Autolavado")
    reservado = await _tenant(admin, "Admin")
    ids = await _catalogo(admin, norte, precio_cotizar=3000000)
    turno = await _turno(admin, norte, ids, estado="PENDIENTE_COTIZACION", hold=INICIO)
    for servicio, precio in ((ids["fijo"], 2000000), (ids["cotizar"], 3000000)):
        await _ejecutar(
            admin,
            "INSERT INTO booking_items (id, tenant_id, booking_id, service_id, position,"
            " service_name_snapshot,"
            " duration_min, price_cents) VALUES (gen_random_uuid(), :t, :b, :s, :pos, 'x', 60, :p)",
            t=norte,
            b=turno,
            s=servicio,
            pos=0 if servicio == ids["fijo"] else 1,
            p=precio,
        )

    migrate_head(owner_url())

    async with admin.connect() as conn:
        slugs = {
            fila[0]: fila[1]
            for fila in (await conn.execute(text("SELECT name, slug FROM tenants"))).all()
        }
        settings = (
            await conn.execute(
                text(
                    "SELECT tenant_id, deposit_enabled, deposit_bps, tolerance_min,"
                    " late_cancel_threshold_min, hold_min, min_advance_min,"
                    " quote_validity_hours FROM tenant_settings"
                )
            )
        ).all()
        modos_por_servicio = {
            fila[0]: fila[1]
            for fila in (
                await conn.execute(
                    text(
                        "SELECT s.name, i.pricing_mode FROM booking_items i"
                        " JOIN services s ON s.id = i.service_id"
                    )
                )
            ).all()
        }
        precio = await conn.scalar(
            text("SELECT price_cents FROM bookings WHERE id = :i"), {"i": turno}
        )
        sin_deposit = await conn.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns"
                " WHERE table_name = 'service_prices' AND column_name = 'deposit_bps'"
            )
        )

    # slug: válido, único, derivado del nombre; el reservado y el sin letras caen en `negocio`.
    assert len(set(slugs.values())) == 3
    assert all(re.fullmatch(r"[a-z0-9-]{3,40}", s) for s in slugs.values())
    assert slugs["Lavadero Demo Norte"].startswith("lavadero-demo-norte-")
    assert slugs["Ñandú & Hijos Autolavado"].startswith("nandu-hijos-autolavado-")
    assert slugs["Admin"].startswith("negocio-")
    # tenant_settings: una fila por tenant, con los defaults del contrato.
    assert {row.tenant_id for row in settings} == {norte, tilde, reservado}
    for row in settings:
        assert row.deposit_enabled is False
        assert row.deposit_bps == DEFAULT_DEPOSIT_BPS
        assert row.tolerance_min == DEFAULT_TOLERANCE_MIN
        assert row.late_cancel_threshold_min == DEFAULT_LATE_CANCEL_THRESHOLD_MIN
        assert row.hold_min == DEFAULT_HOLD_MIN
        assert row.min_advance_min == DEFAULT_MIN_ADVANCE_MIN
        assert row.quote_validity_hours == DEFAULT_QUOTE_VALIDITY_HOURS
    # pricing_mode copiado de cada servicio, y el turno a cotizar suma los precios base.
    assert modos_por_servicio == {"Lavado": "PRECIO_FIJO", "Tapizado": "A_COTIZAR"}
    assert precio == 5000000
    assert sin_deposit == 0


async def test_el_exclude_ya_no_bloquea_un_turno_a_cotizar_pero_si_a_los_demas(
    en_0004: AsyncEngine,
):
    admin = en_0004
    tenant_id = await _tenant(admin, "Lavadero Demo Sur")
    ids = await _catalogo(admin, tenant_id, precio_cotizar=3000000)
    migrate_head(owner_url())

    # Dos turnos a cotizar en el mismo puesto y horario: ahora conviven (D4-13).
    await _turno(admin, tenant_id, ids, estado="PENDIENTE_COTIZACION", precio=5000000)
    await _turno(admin, tenant_id, ids, estado="PENDIENTE_COTIZACION", precio=5000000)
    # Un turno confirmado sí ocupa el puesto: el segundo, encima, lo rechaza la base.
    await _turno(admin, tenant_id, ids, estado="CONFIRMADO", precio=2000000)
    with pytest.raises(Exception, match="xc_bookings_sin_solapamiento"):
        await _turno(admin, tenant_id, ids, estado="CONFIRMADO", precio=2000000)
    # Y un `PENDIENTE_SEÑA` sin hold sigue prohibido; uno a cotizar sin hold ya no.
    with pytest.raises(Exception, match="hold_obligatorio"):
        await _turno(
            admin,
            tenant_id,
            ids,
            estado="PENDIENTE_SEÑA",
            inicio=INICIO + timedelta(days=1),
            precio=2000000,
        )


async def test_el_downgrade_corta_con_un_turno_a_cotizar_sin_hold(en_0004: AsyncEngine):
    admin = en_0004
    tenant_id = await _tenant(admin, "Lavadero Demo Este")
    ids = await _catalogo(admin, tenant_id, precio_cotizar=3000000)
    migrate_head(owner_url())
    turno = await _turno(admin, tenant_id, ids, estado="PENDIENTE_COTIZACION", precio=5000000)

    with pytest.raises(Exception, match="sin hold"):
        _alembic("downgrade", ANTERIOR)
    assert await _version(admin) == head_de_alembic()  # el corte no dejó la base a medias

    # Sin ese turno, baja limpio (y el fixture vuelve a subir).
    await _ejecutar(admin, "DELETE FROM bookings WHERE id = :i", i=turno)
    _alembic("downgrade", ANTERIOR)
    assert await _version(admin) == ANTERIOR


async def test_los_defaults_de_la_migracion_son_los_del_modelo():
    """La migración congela sus defaults (no importa el modelo); acá se comparan."""
    import importlib  # noqa: PLC0415

    migracion = importlib.import_module(
        "app.persistence.migrations.versions.20261104_0005_configuracion_precios"
    )
    tabla = Base.metadata.tables["tenant_settings"]
    for columna, valor in migracion.DEFAULTS.items():
        assert str(tabla.c[columna].server_default.arg) == str(valor), columna
    assert str(tabla.c.palette.server_default.arg) == migracion.PALETTE_DEFAULT
    assert str(tabla.c.font.server_default.arg) == migracion.FONT_DEFAULT
