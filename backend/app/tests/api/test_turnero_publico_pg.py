"""Turnero público contra **Postgres real** (rol `carwash_app`, RLS activo) — PR 5.2.

Lo que SQLite no puede probar:

- **B8 · concurrencia:** dos `POST` simultáneos al mismo horario. Con un puesto, uno gana y el
  otro recibe 409 `SLOT_TAKEN` (el `EXCLUDE` es el juez, Z5); con dos puestos ganan los dos, cada
  uno en un puesto distinto (D5-6: el que pierde la carrera prueba el siguiente).
- **Z7/Z8 con la función real:** `public_tenant_by_slug` resuelve el negocio sin sesión; un slug
  inexistente y uno con el alta sin terminar dan el mismo 404.
- **RLS sin cookie:** reservar, ver y cancelar sin sesión funcionan porque el servicio fija el
  contexto del negocio del slug; con el slug de otro negocio, el turno no existe.

La app corre sobre un engine propio con `NullPool`: el `pg_engine` compartido tiene una sola
conexión y serializaría los dos pedidos. Datos sintéticos (`@example.com`, `+54 9 351 000-xxxx`).
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.void import VoidReason
from app.persistence.db.session import get_db_session, make_session_dependency
from app.persistence.db.tenant_context import set_tenant_context
from app.persistence.repositories.catalog import ResourceRepository
from app.tests.application._armado import Lavadero, armar_catalogo
from app.tests.conftest_pg import PG_TEST_URL, _as_app_role, _with_driver

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

TZ = ZoneInfo("America/Argentina/Cordoba")
DIA = (datetime.now(TZ) + timedelta(days=2)).date()
INICIO = datetime.combine(DIA, time(10), tzinfo=TZ).astimezone(UTC)


async def _abrir(admin, tenant: uuid.UUID, slug: str) -> None:
    async with admin.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET slug = :s WHERE id = :t"), {"s": slug, "t": tenant}
        )
        await conn.execute(
            text(
                "UPDATE tenant_settings SET onboarding_completed_at = now(), "
                "cancellation_policy = 'Cancelá con 12 h.' WHERE tenant_id = :t"
            ),
            {"t": tenant},
        )
        for weekday in range(1, 8):
            await conn.execute(
                text(
                    "INSERT INTO business_hours (id, tenant_id, weekday, opens_at, closes_at) "
                    "VALUES (gen_random_uuid(), :t, :w, '09:00', '18:00')"
                ),
                {"t": tenant, "w": weekday},
            )


@pytest_asyncio.fixture
async def http(pg_admin_engine, pg_clean) -> AsyncGenerator[AsyncClient, None]:
    """La app con un engine de runtime sin pool: cada request, su conexión y su transacción."""
    from app.api.rate_limit import limiter  # noqa: PLC0415
    from app.main import create_app  # noqa: PLC0415

    engine = create_async_engine(
        _as_app_role(_with_driver(PG_TEST_URL, "asyncpg")), poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    limiter.reset()
    app = create_app()
    app.dependency_overrides[get_db_session] = make_session_dependency(factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as ac:
        yield ac
    await engine.dispose()


@pytest_asyncio.fixture
async def lavaderos(
    pg_engine, pg_session_factory, pg_admin_engine, pg_tenant_a, pg_tenant_b, pg_user_factory
) -> dict[str, Lavadero]:
    # Los tests de la migración 0010 bajan y suben el enum `service_booking_mode`: una conexión
    # del pool guardaría el OID viejo ("cache lookup failed for type"). Se tira el pool.
    await pg_engine.dispose()
    out: dict[str, Lavadero] = {}
    for nombre, tenant, slug in (
        ("a", pg_tenant_a, "turnero-pg-a"),
        ("b", pg_tenant_b, "turnero-pg-b"),
    ):
        owner = await pg_user_factory(tenant, f"turnero-owner-{nombre}@example.com")
        async with pg_session_factory() as s, s.begin():
            out[nombre] = await armar_catalogo(s, tenant, owner)
        await _abrir(pg_admin_engine, tenant, slug)
    return out


async def _anular_puesto_2(pg_session_factory, lav: Lavadero) -> None:
    async with pg_session_factory() as s, s.begin():
        await set_tenant_context(s, lav.tenant_id)
        await ResourceRepository(s).void(lav.puesto_2, lav.tenant_id, VoidReason.DESACTIVADO)


def _cuerpo(lav: Lavadero, telefono: str) -> dict[str, Any]:
    return {
        "vehicle_size_id": str(lav.auto),
        "service_ids": [str(lav.lavado)],
        "start_at": INICIO.isoformat(),
        "name": "Cliente Web",
        "phone": telefono,
        "accept_policy": True,
    }


async def _reservar(http: AsyncClient, lav: Lavadero, telefono: str, slug="turnero-pg-a"):
    return await http.post(
        f"/v1/public/{slug}/bookings",
        json=_cuerpo(lav, telefono),
        headers={"Idempotency-Key": f"web-{uuid.uuid4()}"},
    )


async def _puestos(admin, tenant: uuid.UUID) -> list[uuid.UUID]:
    async with admin.connect() as conn:
        filas = await conn.execute(
            text("SELECT resource_id FROM bookings WHERE tenant_id = :t ORDER BY resource_id"),
            {"t": tenant},
        )
        return [f.resource_id for f in filas]


# ── B8 · dos clientes al mismo horario ───────────────────────────────────────


async def test_con_un_puesto_uno_gana_y_el_otro_recibe_slot_taken(
    http, lavaderos, pg_session_factory, pg_admin_engine
):
    a = lavaderos["a"]
    await _anular_puesto_2(pg_session_factory, a)
    r1, r2 = await asyncio.gather(
        _reservar(http, a, "+54 9 351 000-0101"), _reservar(http, a, "+54 9 351 000-0102")
    )
    codigos = sorted([r1.status_code, r2.status_code])
    assert codigos == [201, 409], (r1.text, r2.text)
    perdedor = r1 if r1.status_code == 409 else r2
    assert perdedor.json()["detail"]["code"] == "SLOT_TAKEN"
    assert await _puestos(pg_admin_engine, a.tenant_id) == [a.puesto]


async def test_con_dos_puestos_ganan_los_dos_en_puestos_distintos(http, lavaderos, pg_admin_engine):
    a = lavaderos["a"]
    r1, r2 = await asyncio.gather(
        _reservar(http, a, "+54 9 351 000-0201"), _reservar(http, a, "+54 9 351 000-0202")
    )
    assert (r1.status_code, r2.status_code) == (201, 201), (r1.text, r2.text)
    assert sorted(await _puestos(pg_admin_engine, a.tenant_id)) == sorted([a.puesto, a.puesto_2])
    # Un tercero ya no entra: los dos puestos están tomados.
    tercero = await _reservar(http, a, "+54 9 351 000-0203")
    assert tercero.status_code == 409 and tercero.json()["detail"]["code"] == "SLOT_TAKEN"


async def test_el_mismo_doble_envio_concurrente_crea_un_solo_turno(
    http, lavaderos, pg_admin_engine
):
    a = lavaderos["a"]
    clave = {"Idempotency-Key": "web-doble-concurrente"}
    ruta = "/v1/public/turnero-pg-a/bookings"
    cuerpo = _cuerpo(a, "+54 9 351 000-0301")
    r1, r2 = await asyncio.gather(
        http.post(ruta, json=cuerpo, headers=clave), http.post(ruta, json=cuerpo, headers=clave)
    )
    assert r1.status_code == r2.status_code == 201, (r1.text, r2.text)
    assert r1.json()["code"] == r2.json()["code"]
    assert len(await _puestos(pg_admin_engine, a.tenant_id)) == 1


# ── Z7/Z8 y RLS sin cookie ───────────────────────────────────────────────────


async def test_slug_inexistente_y_alta_sin_terminar_dan_el_mismo_404(
    http, lavaderos, pg_admin_engine
):
    inexistente = await http.get("/v1/public/no-existe-este")
    async with pg_admin_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tenant_settings SET onboarding_completed_at = NULL " "WHERE tenant_id = :t"
            ),
            {"t": lavaderos["b"].tenant_id},
        )
    sin_alta = await http.get("/v1/public/turnero-pg-b")
    assert inexistente.status_code == sin_alta.status_code == 404
    assert inexistente.content == sin_alta.content
    assert (await http.get("/v1/public/TURNERO-PG-A")).status_code == 200


async def test_reservar_ver_y_cancelar_sin_sesion_y_el_otro_slug_no_lo_ve(
    http, lavaderos, pg_admin_engine
):
    a = lavaderos["a"]
    catalogo = await http.get("/v1/public/turnero-pg-a/catalog")
    assert catalogo.status_code == 200
    assert {s["name"] for s in catalogo.json()["services"]} >= {"Lavado completo", "Tapizado"}
    dispo = await http.get(
        "/v1/public/turnero-pg-a/availability",
        params={
            "date": DIA.isoformat(),
            "vehicle_size_id": str(a.auto),
            "service_ids": [str(a.lavado)],
        },
    )
    assert dispo.status_code == 200 and len(dispo.json()["slots"]) == 17

    telefono = "+54 9 351 000-0401"
    reserva = await _reservar(http, a, telefono)
    assert reserva.status_code == 201, reserva.text
    codigo = reserva.json()["code"]
    credenciales = {"code": codigo, "phone": telefono}

    for accion in ("lookup", "cancel"):
        ajeno = await http.post(f"/v1/public/turnero-pg-b/bookings/{accion}", json=credenciales)
        assert ajeno.status_code == 404
    visto = await http.post("/v1/public/turnero-pg-a/bookings/lookup", json=credenciales)
    assert visto.status_code == 200 and visto.json()["status"] == "CONFIRMADO"
    cancelado = await http.post("/v1/public/turnero-pg-a/bookings/cancel", json=credenciales)
    assert cancelado.status_code == 200, cancelado.text
    assert cancelado.json()["status"] == "CANCELADO_CLIENTE"

    async with pg_admin_engine.connect() as conn:
        fila = (
            await conn.execute(
                text(
                    "SELECT b.tenant_id, b.source, b.status, c.acquisition_channel "
                    "FROM bookings b JOIN customers c ON c.id = b.customer_id WHERE b.code = :c"
                ),
                {"c": codigo},
            )
        ).one()
    assert fila.tenant_id == a.tenant_id
    assert (fila.source, fila.status, fila.acquisition_channel) == (
        "TURNERO_WEB",
        "CANCELADO_CLIENTE",
        "TURNERO_WEB",
    )
