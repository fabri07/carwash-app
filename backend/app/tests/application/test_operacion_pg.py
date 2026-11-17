"""Operación del job, agenda del día y carga retrospectiva (PR 6.1) contra **Postgres real**, como
`carwash_app` — FASE-6-CONTRATO A2, A3, A5 y A6.

Lo que SQLite no puede probar:

- el `EXCLUDE` decide el choque de un turno a mano (409 `SLOT_TAKEN`) y el rollback no deja un
  cliente huérfano;
- la carga retrospectiva queda persistida con `entry_mode = RETROSPECTIVA` y `manual_entry` en cada
  evento, y cae en la agenda de su fecha (zona del negocio, instantes con zona);
- las carreras de verdad (conexiones distintas, en paralelo): el mismo toque dos veces con la misma
  clave → una sola vez; con claves distintas → un ganador y un 409 `INVALID_TRANSITION`; la misma
  carga retrospectiva dos veces → un solo job; dos turnos a mano al mismo horario → un solo turno.

Datos sintéticos. Sesiones con `NullPool`, una por pedido (permite pedidos en paralelo).
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.persistence.db.session import get_db_session, make_session_dependency
from app.tests.application._armado import PRECIO_FIJO, Lavadero, armar_catalogo
from app.tests.conftest import cookie_header
from app.tests.conftest_pg import PG_TEST_URL, _as_app_role, _with_driver
from app.utils.cookies import ACCESS_COOKIE
from app.utils.security import create_access_token

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

Factory = async_sessionmaker[AsyncSession]
CORDOBA = ZoneInfo("America/Argentina/Cordoba")


def _clave() -> dict[str, str]:
    return {"Idempotency-Key": f"k-{uuid.uuid4()}"}


def _iso(instante: datetime) -> str:
    return instante.astimezone(UTC).isoformat()


@pytest_asyncio.fixture
async def fabrica(pg_admin_engine, pg_engine) -> AsyncGenerator[Factory, None]:
    # Tests anteriores (0010 y 0011) recrean enums: el pool compartido puede tener el OID viejo.
    await pg_engine.dispose()
    engine = create_async_engine(
        _as_app_role(_with_driver(PG_TEST_URL, "asyncpg")), poolclass=NullPool
    )
    yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def http(fabrica: Factory, pg_clean) -> AsyncGenerator[AsyncClient, None]:
    from app.api.rate_limit import limiter  # noqa: PLC0415
    from app.main import create_app  # noqa: PLC0415

    limiter.reset()
    app = create_app()
    app.dependency_overrides[get_db_session] = make_session_dependency(fabrica)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def lav(fabrica: Factory, pg_tenant_a, pg_user_factory) -> tuple[Lavadero, dict[str, str]]:
    owner = await pg_user_factory(pg_tenant_a, f"owner-{uuid.uuid4().hex[:6]}@example.com")
    async with fabrica() as s, s.begin():
        armado = await armar_catalogo(s, pg_tenant_a, owner)
        for dia in range(1, 8):
            await armado.catalogo.add_business_hours(dia, time(0, 0), time(23, 59))
    token = create_access_token({"sub": str(owner), "tenant_id": str(pg_tenant_a), "ver": 0})
    return armado, cookie_header({ACCESS_COOKIE: token})


async def _contar(admin, tabla: str, tenant: uuid.UUID) -> int:
    async with admin.connect() as conn:
        return int(
            await conn.scalar(
                text(f"SELECT count(*) FROM {tabla} WHERE tenant_id = :t"),  # noqa: S608
                {"t": tenant},
            )
            or 0
        )


def _a_mano(lv: Lavadero, inicio: datetime, **extra):
    return {
        "start_at": _iso(inicio),
        "resource_id": str(lv.puesto),
        "vehicle_size_id": str(lv.auto),
        "service_ids": [str(lv.lavado)],
        "customer_name": "Cliente Mostrador",
        "customer_phone": "+54 9 351 000-0606",
        **extra,
    }


def _retro(lv: Lavadero, dia, **extra):
    return {
        "started_at": _iso(datetime.combine(dia, time(10), tzinfo=CORDOBA)),
        "finished_at": _iso(datetime.combine(dia, time(11), tzinfo=CORDOBA)),
        "vehicle_size_id": str(lv.auto),
        "service_ids": [str(lv.lavado)],
        "customer_name": "Cliente Ayer",
        "plate": "XY987ZW",
        "payment": {"payment_method_id": str(lv.efectivo)},
        **extra,
    }


# ── Turno a mano: el EXCLUDE decide ──────────────────────────────────────────


async def test_el_turno_a_mano_choca_con_el_exclude_y_no_deja_filas_huerfanas(
    http, lav, pg_admin_engine, pg_tenant_a
):
    lv, cabecera = lav
    inicio = datetime.now(UTC) + timedelta(days=2)
    uno = await http.post(
        "/v1/bookings/manual", json=_a_mano(lv, inicio), headers={**cabecera, **_clave()}
    )
    assert uno.status_code == 201, uno.text
    clientes = await _contar(pg_admin_engine, "customers", pg_tenant_a)
    choque = await http.post(
        "/v1/bookings/manual",
        json=_a_mano(
            lv,
            inicio + timedelta(minutes=30),
            customer_name="Otro Cliente",
            customer_phone="+54 9 351 000-0707",
            plate="QQ111WW",
        ),
        headers={**cabecera, **_clave()},
    )
    assert choque.status_code == 409 and choque.json()["detail"]["code"] == "SLOT_TAKEN"
    # El rollback deshizo todo: ni el cliente ni el auto nuevos quedaron.
    assert await _contar(pg_admin_engine, "customers", pg_tenant_a) == clientes
    assert await _contar(pg_admin_engine, "bookings", pg_tenant_a) == 1
    otro_puesto = await http.post(
        "/v1/bookings/manual",
        json=_a_mano(lv, inicio + timedelta(minutes=30), resource_id=str(lv.puesto_2)),
        headers={**cabecera, **_clave()},
    )
    assert otro_puesto.status_code == 201, otro_puesto.text


async def test_dos_turnos_a_mano_al_mismo_horario_en_paralelo_dejan_uno(
    http, lav, pg_admin_engine, pg_tenant_a
):
    lv, cabecera = lav
    inicio = datetime.now(UTC) + timedelta(days=3)
    respuestas = await asyncio.gather(
        *(
            http.post(
                "/v1/bookings/manual",
                json=_a_mano(lv, inicio, customer_phone=f"+54 9 351 000-08{n:02d}"),
                headers={**cabecera, **_clave()},
            )
            for n in range(2)
        )
    )
    assert sorted(r.status_code for r in respuestas) == [201, 409]
    assert {r.json()["detail"]["code"] for r in respuestas if r.status_code == 409} == {
        "SLOT_TAKEN"
    }
    assert await _contar(pg_admin_engine, "bookings", pg_tenant_a) == 1


# ── Carga retrospectiva: persistida y en su fecha ────────────────────────────


async def test_la_carga_retrospectiva_queda_marcada_en_la_base_y_en_la_agenda_de_ayer(
    http, lav, pg_admin_engine, pg_tenant_a
):
    lv, cabecera = lav
    ayer = datetime.now(CORDOBA).date() - timedelta(days=1)
    r = await http.post(
        "/v1/jobs/retrospective", json=_retro(lv, ayer), headers={**cabecera, **_clave()}
    )
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "COBRADO" and job["entry_mode"] == "RETROSPECTIVA"
    async with pg_admin_engine.connect() as conn:
        modo = await conn.scalar(
            text("SELECT entry_mode::text FROM jobs WHERE id = :j"), {"j": job["id"]}
        )
        eventos = (
            await conn.execute(
                text(
                    "SELECT event_type::text, metadata->>'manual_entry', occurred_at "
                    "FROM job_events WHERE job_id = :j ORDER BY created_at, id"
                ),
                {"j": job["id"]},
            )
        ).all()
    assert modo == "RETROSPECTIVA"
    assert [e[0] for e in eventos] == [
        "JOB_RECEIVED",
        "JOB_STARTED",
        "JOB_FINISHED",
        "PAYMENT_RECORDED",
        "JOB_SETTLED",
    ]
    assert all(e[1] == "true" for e in eventos)
    assert eventos[1][2] == datetime.combine(ayer, time(10), tzinfo=CORDOBA)
    assert eventos[2][2] == datetime.combine(ayer, time(11), tzinfo=CORDOBA)

    en_ayer = (
        await http.get("/v1/agenda/day", params={"date": ayer.isoformat()}, headers=cabecera)
    ).json()
    assert [e["job_id"] for e in en_ayer["entries"]] == [job["id"]]
    entrada = en_ayer["entries"][0]
    assert datetime.fromisoformat(entrada["start_at"]) == datetime.combine(
        ayer, time(10), tzinfo=CORDOBA
    )  # con zona de verdad: Postgres no la pierde
    hoy = datetime.now(CORDOBA).date().isoformat()
    en_hoy = (await http.get("/v1/agenda/day", params={"date": hoy}, headers=cabecera)).json()
    assert en_hoy["entries"] == []


async def test_la_misma_carga_retrospectiva_en_paralelo_deja_un_solo_job(
    http, lav, pg_admin_engine, pg_tenant_a
):
    lv, cabecera = lav
    ayer = datetime.now(CORDOBA).date() - timedelta(days=1)
    clave = {"Idempotency-Key": f"retro-{uuid.uuid4()}"}
    cuerpo = _retro(lv, ayer)
    respuestas = await asyncio.gather(
        *(
            http.post("/v1/jobs/retrospective", json=cuerpo, headers={**cabecera, **clave})
            for _ in range(3)
        )
    )
    assert [r.status_code for r in respuestas] == [201, 201, 201], [r.text for r in respuestas]
    assert len({r.json()["id"] for r in respuestas}) == 1
    assert await _contar(pg_admin_engine, "jobs", pg_tenant_a) == 1
    assert await _contar(pg_admin_engine, "job_events", pg_tenant_a) == 5
    assert await _contar(pg_admin_engine, "payments", pg_tenant_a) == 1
    assert await _contar(pg_admin_engine, "cash_movements", pg_tenant_a) == 1


# ── Toques en paralelo (A2, A3) ──────────────────────────────────────────────


async def _ingreso(http, lv: Lavadero, cabecera: dict[str, str]) -> dict:
    r = await http.post(
        "/v1/jobs",
        json={
            "occurred_at": _iso(datetime.now(UTC)),
            "plate": "AB123CD",
            "vehicle_size_id": str(lv.auto),
            "service_ids": [str(lv.lavado)],
        },
        headers={**cabecera, **_clave()},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_un_doble_toque_con_la_misma_clave_en_paralelo_registra_una_vez(
    http, lav, pg_admin_engine, pg_tenant_a
):
    lv, cabecera = lav
    job = await _ingreso(http, lv, cabecera)
    clave = {"Idempotency-Key": f"toque-{uuid.uuid4()}"}
    cuerpo = {"occurred_at": _iso(datetime.now(UTC))}
    respuestas = await asyncio.gather(
        *(
            http.post(f"/v1/jobs/{job['id']}/start", json=cuerpo, headers={**cabecera, **clave})
            for _ in range(4)
        )
    )
    assert [r.status_code for r in respuestas] == [200] * 4
    assert {r.json()["status"] for r in respuestas} == {"EN_PROCESO"}
    async with pg_admin_engine.connect() as conn:
        iniciados = await conn.scalar(
            text(
                "SELECT count(*) FROM job_events WHERE job_id = :j AND event_type = 'JOB_STARTED'"
            ),
            {"j": job["id"]},
        )
    assert iniciados == 1


async def test_dos_dispositivos_con_claves_distintas_un_ganador_y_un_409_estable(
    http, lav, pg_admin_engine
):
    lv, cabecera = lav
    job = await _ingreso(http, lv, cabecera)
    cuerpo = {"occurred_at": _iso(datetime.now(UTC))}
    respuestas = await asyncio.gather(
        *(
            http.post(f"/v1/jobs/{job['id']}/start", json=cuerpo, headers={**cabecera, **_clave()})
            for _ in range(2)
        )
    )
    assert sorted(r.status_code for r in respuestas) == [200, 409]
    perdedora = next(r for r in respuestas if r.status_code == 409)
    assert perdedora.json()["detail"]["code"] == "INVALID_TRANSITION"
    assert (await http.get(f"/v1/jobs/{job['id']}", headers=cabecera)).json()["status"] == (
        "EN_PROCESO"
    )


async def test_dos_cobros_distintos_en_paralelo_no_cobran_de_mas(http, lav, pg_admin_engine):
    lv, cabecera = lav
    job = await _ingreso(http, lv, cabecera)
    for accion in ("start", "finish"):
        await http.post(
            f"/v1/jobs/{job['id']}/{accion}",
            json={"occurred_at": _iso(datetime.now(UTC))},
            headers={**cabecera, **_clave()},
        )
    cuerpo = {"occurred_at": _iso(datetime.now(UTC)), "payment_method_id": str(lv.efectivo)}
    respuestas = await asyncio.gather(
        *(
            http.post(
                f"/v1/jobs/{job['id']}/payments", json=cuerpo, headers={**cabecera, **_clave()}
            )
            for _ in range(2)
        )
    )
    # El segundo cobro de "el saldo" ya no tiene saldo que cobrar: 422, no un sobrecobro.
    assert sorted(r.status_code for r in respuestas) == [200, 422], [r.text for r in respuestas]
    final = (await http.get(f"/v1/jobs/{job['id']}", headers=cabecera)).json()
    assert final["paid_cents"] == PRECIO_FIJO and final["balance_due_cents"] == 0


async def test_recibir_el_mismo_turno_en_paralelo_deja_un_solo_job(
    http, lav, pg_admin_engine, pg_tenant_a, fabrica
):
    lv, cabecera = lav
    ahora = datetime.now(UTC)
    async with fabrica() as s, s.begin():
        turno = await lv.en(s).turno(
            inicio=ahora + timedelta(hours=3), ahora=ahora - timedelta(days=1)
        )
    respuestas = await asyncio.gather(
        *(
            http.post(
                "/v1/jobs",
                json={"occurred_at": _iso(ahora), "booking_id": str(turno.id)},
                headers={**cabecera, **_clave()},
            )
            for _ in range(3)
        )
    )
    assert [r.status_code for r in respuestas] == [201, 201, 201], [r.text for r in respuestas]
    assert len({r.json()["id"] for r in respuestas}) == 1
    assert await _contar(pg_admin_engine, "jobs", pg_tenant_a) == 1
