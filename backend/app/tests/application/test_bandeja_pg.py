"""Bandeja del dueño (PR 5.3) contra **Postgres real**, como `carwash_app` — D5-9, D5-10, B11.

Lo que SQLite no puede probar porque no tiene el `EXCLUDE`:

- reprogramar o cambiar de puesto sobre un horario tomado → `SlotTakenError` (y la transacción
  sigue usable: SAVEPOINT); sobre uno libre, entra;
- aprobar un pedido de la lista de espera con el primer puesto tomado → el siguiente (D5-6); con
  todos tomados → `SlotTakenError`, y el pedido sigue en revisión;
- la seña tardía con el horario tomado → `SlotTakenError` (A16 de F3, el caso queda como hoy);
- el 409 `SLOT_TAKEN` de la API con RLS y rollback reales.

Datos sintéticos. Sesiones con `NullPool` (como `test_servicios_pg.py`).
"""

import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.application.services.errors import SlotTakenError
from app.domain.enums import BookingStatus
from app.tests.application._armado import AHORA, SENA, Lavadero, T, armar_catalogo
from app.tests.application._bandeja import en_revision
from app.tests.conftest import cookie_header
from app.tests.conftest_pg import PG_TEST_URL, _as_app_role, _with_driver
from app.utils.cookies import ACCESS_COOKIE
from app.utils.security import create_access_token

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

Factory = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def fabrica(pg_admin_engine, pg_engine) -> AsyncGenerator[Factory, None]:
    # Tests anteriores (0010) recrean el enum `service_booking_mode`: el engine compartido puede
    # tener el OID viejo en caché.
    await pg_engine.dispose()
    engine = create_async_engine(
        _as_app_role(_with_driver(PG_TEST_URL, "asyncpg")), poolclass=NullPool
    )
    yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def lav(fabrica: Factory, pg_tenant_a, pg_user_factory) -> Lavadero:
    owner = await pg_user_factory(pg_tenant_a, f"owner-{uuid.uuid4().hex[:6]}@example.com")
    async with fabrica() as s, s.begin():
        armado = await armar_catalogo(s, pg_tenant_a, owner)
    return armado


async def _en_tx[R](fabrica: Factory, lav: Lavadero, fn: Callable[[Lavadero], Awaitable[R]]) -> R:
    async with fabrica() as s, s.begin():
        return await fn(lav.en(s))


# ── Reprogramar (D5-10) ──────────────────────────────────────────────────────


async def test_reprogramar_sobre_un_turno_choca_y_otro_puesto_entra(fabrica, lav):
    ocupante = await _en_tx(fabrica, lav, lambda lv: lv.turno(inicio=T + timedelta(hours=2)))
    turno = await _en_tx(fabrica, lav, lambda lv: lv.turno())
    async with fabrica() as s, s.begin():
        lv = lav.en(s)
        with pytest.raises(SlotTakenError):
            await lv.turnos.reschedule(
                turno.id, now=AHORA, start_at=T + timedelta(hours=2, minutes=30)
            )
        # El SAVEPOINT dejó la transacción usable: el mismo horario en el otro puesto, entra.
        r = await lv.turnos.reschedule(
            turno.id,
            now=AHORA,
            start_at=T + timedelta(hours=2, minutes=30),
            resource_id=lv.puesto_2,
        )
        assert r.booking.resource_id == lv.puesto_2
    async with fabrica() as s, s.begin():
        movido = await lav.en(s).turnos.get(turno.id)
        assert movido.start_at == T + timedelta(hours=2, minutes=30)
        assert movido.resource_id == lav.puesto_2
        assert (await lav.en(s).turnos.get(ocupante.id)).resource_id == lav.puesto


async def test_cambiar_de_puesto_a_uno_ocupado_choca(fabrica, lav):
    await _en_tx(fabrica, lav, lambda lv: lv.turno(puesto=lv.puesto_2))
    turno = await _en_tx(fabrica, lav, lambda lv: lv.turno())
    async with fabrica() as s, s.begin():
        with pytest.raises(SlotTakenError):
            await lav.en(s).turnos.reschedule(turno.id, now=AHORA, resource_id=lav.puesto_2)


async def test_reprogramar_sobre_un_hold_vencido_entra(fabrica, lav):
    await _en_tx(fabrica, lav, lambda lv: lv.turno_con_sena(puesto=lv.puesto_2))
    turno = await _en_tx(fabrica, lav, lambda lv: lv.turno())
    async with fabrica() as s, s.begin():
        r = await lav.en(s).turnos.reschedule(
            turno.id, now=AHORA + timedelta(hours=1), resource_id=lav.puesto_2
        )
        assert r.booking.resource_id == lav.puesto_2


# ── Aprobar la lista de espera (D5-9) ────────────────────────────────────────


async def test_aprobar_toma_el_primer_puesto_libre(fabrica, lav):
    await _en_tx(fabrica, lav, lambda lv: lv.turno())  # puesto 1 ocupado a las T
    pedido = await _en_tx(fabrica, lav, lambda lv: en_revision(lv, puesto=lv.puesto_2))
    aprobado = await _en_tx(fabrica, lav, lambda lv: lv.turnos.approve_review(pedido.id, now=AHORA))
    assert aprobado.status == BookingStatus.CONFIRMADO
    assert aprobado.resource_id == lav.puesto_2


async def test_aprobar_sin_puesto_libre_es_slot_taken_y_sigue_en_revision(fabrica, lav):
    await _en_tx(fabrica, lav, lambda lv: lv.turno())
    await _en_tx(fabrica, lav, lambda lv: lv.turno(puesto=lv.puesto_2))
    # El pedido se arma en otro horario y se mueve encima (reprogramar en revisión no choca: no
    # ocupa el puesto).
    pedido = await _en_tx(fabrica, lav, lambda lv: en_revision(lv, inicio=T + timedelta(hours=3)))
    await _en_tx(fabrica, lav, lambda lv: lv.turnos.reschedule(pedido.id, now=AHORA, start_at=T))
    async with fabrica() as s, s.begin():
        with pytest.raises(SlotTakenError):
            await lav.en(s).turnos.approve_review(pedido.id, now=AHORA)
    async with fabrica() as s, s.begin():
        turno = await lav.en(s).turnos.get(pedido.id)
        assert turno.status == BookingStatus.PENDIENTE_REVISION


# ── Seña tardía (A16) ────────────────────────────────────────────────────────


async def test_la_sena_tardia_con_el_horario_tomado_es_slot_taken(fabrica, lav):
    turno = await _en_tx(fabrica, lav, lambda lv: lv.turno_con_sena())
    tarde = AHORA + timedelta(hours=1)
    await _en_tx(fabrica, lav, lambda lv: lv.turno(ahora=tarde))  # vence el hold y toma el puesto
    async with fabrica() as s, s.begin():
        with pytest.raises(SlotTakenError):
            await lav.en(s).turnos.confirm_deposit_or_late(turno.id, lav.pago(SENA), now=tarde)
    async with fabrica() as s, s.begin():
        assert (await lav.en(s).turnos.get(turno.id)).status == BookingStatus.VENCIDO


# ── HTTP: el 409 `SLOT_TAKEN` con RLS y rollback reales ──────────────────────


async def test_la_api_responde_slot_taken_y_no_mueve_nada(fabrica, lav, pg_client):
    hoy = datetime.now(UTC)
    inicio = hoy + timedelta(days=2)
    await _en_tx(fabrica, lav, lambda lv: lv.turno(inicio=inicio + timedelta(hours=2), ahora=hoy))
    turno = await _en_tx(fabrica, lav, lambda lv: lv.turno(inicio=inicio, ahora=hoy))
    token = create_access_token(
        {"sub": str(lav.owner_id), "tenant_id": str(lav.tenant_id), "ver": 0}
    )
    cabecera = cookie_header({ACCESS_COOKIE: token})
    r = await pg_client.post(
        f"/v1/bookings/{turno.id}/reschedule",
        json={"start_at": (inicio + timedelta(hours=2, minutes=30)).isoformat()},
        headers=cabecera,
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "SLOT_TAKEN"
    async with fabrica() as s, s.begin():
        assert (await lav.en(s).turnos.get(turno.id)).start_at == inicio
    ok = await pg_client.post(
        f"/v1/bookings/{turno.id}/reschedule",
        json={"resource_id": str(lav.puesto_2)},
        headers=cabecera,
    )
    assert ok.status_code == 200, ok.text
