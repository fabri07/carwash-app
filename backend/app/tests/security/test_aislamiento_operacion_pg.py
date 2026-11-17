"""Tester-aislamiento del PR 6.1 (operación del job) contra **Postgres real**, como `carwash_app`.

Complementa `test_aislamiento_servicios_pg.py` (que corre la tabla de intentos de
`_intentos_servicios.py`, con las operaciones nuevas de la F6) y `api/test_operacion.py` (HTTP,
SQLite).

1. **HTTP con RLS real**: B recorre todas las rutas con `{id}` del job de A y recibe el 404 del id
   al azar; la agenda del día de B no trae nada de A; el testigo (superusuario) ve las filas de A
   sin cambios.
2. **SQL directo con `carwash_app`** sobre la columna nueva (`jobs.entry_mode`): con el contexto de
   B o sin contexto, leer o cambiar la marca de un job de A afecta 0 filas.

Datos sintéticos (`@example.com`, `+54 9 351 000-xxxx`).
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.persistence.db.session import get_db_session, make_session_dependency
from app.persistence.db.tenant_context import set_tenant_context
from app.tests.application._armado import armar_catalogo
from app.tests.conftest import cookie_header
from app.tests.conftest_pg import PG_TEST_URL, _as_app_role, _with_driver
from app.utils.cookies import ACCESS_COOKIE
from app.utils.security import create_access_token

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

Factory = async_sessionmaker[AsyncSession]
CORDOBA = ZoneInfo("America/Argentina/Cordoba")


def _sesion(user: uuid.UUID, tenant: uuid.UUID) -> dict[str, str]:
    token = create_access_token({"sub": str(user), "tenant_id": str(tenant), "ver": 0})
    return cookie_header({ACCESS_COOKIE: token})


def _clave() -> dict[str, str]:
    return {"Idempotency-Key": f"k-{uuid.uuid4()}"}


def _iso(instante: datetime) -> str:
    return instante.astimezone(UTC).isoformat()


@pytest_asyncio.fixture
async def fabrica(pg_admin_engine, pg_engine) -> AsyncGenerator[Factory, None]:
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
async def dos(fabrica: Factory, pg_tenant_a, pg_tenant_b, pg_user_factory) -> dict[str, Any]:
    owner_a = await pg_user_factory(pg_tenant_a, "operacion-owner-a@example.com")
    owner_b = await pg_user_factory(pg_tenant_b, "operacion-owner-b@example.com")
    async with fabrica() as s, s.begin():
        a = await armar_catalogo(s, pg_tenant_a, owner_a)
    async with fabrica() as s, s.begin():
        b = await armar_catalogo(s, pg_tenant_b, owner_b)
    return {
        "a": a,
        "b": b,
        "ha": _sesion(owner_a, pg_tenant_a),
        "hb": _sesion(owner_b, pg_tenant_b),
        "ta": pg_tenant_a,
        "tb": pg_tenant_b,
    }


async def _foto_de_a(admin, tenant: uuid.UUID) -> dict[str, Any]:
    tablas = ("jobs", "job_items", "job_events", "job_inspections", "payments", "cash_movements")
    async with admin.connect() as conn:
        return {
            t: (
                await conn.execute(
                    text(  # noqa: S608
                        f"SELECT coalesce(json_agg(x ORDER BY x::text), '[]'::json)::text "
                        f"FROM {t} x WHERE tenant_id = :t"
                    ),
                    {"t": tenant},
                )
            ).scalar()
            for t in tablas
        }


async def test_b_recorre_toda_la_operacion_de_a_y_no_mueve_una_fila(http, pg_admin_engine, dos):
    a, b, ha, hb = dos["a"], dos["b"], dos["ha"], dos["hb"]
    ahora = datetime.now(UTC)
    ingreso = await http.post(
        "/v1/jobs",
        json={
            "occurred_at": _iso(ahora),
            "plate": "AB123CD",
            "vehicle_size_id": str(a.auto),
            "service_ids": [str(a.lavado)],
        },
        headers={**ha, **_clave()},
    )
    job = ingreso.json()["id"]
    for accion in ("start", "finish"):
        await http.post(
            f"/v1/jobs/{job}/{accion}",
            json={"occurred_at": _iso(ahora)},
            headers={**ha, **_clave()},
        )
    cobro = await http.post(
        f"/v1/jobs/{job}/payments",
        json={
            "occurred_at": _iso(ahora),
            "payment_method_id": str(a.efectivo),
            "amount_cents": 1000,
        },
        headers={**ha, **_clave()},
    )
    pago = cobro.json()["payments"][0]["id"]
    antes = await _foto_de_a(pg_admin_engine, dos["ta"])

    cuerpo = {"occurred_at": _iso(ahora)}
    intentos: list[tuple[str, str, Any]] = [
        ("GET", f"/v1/jobs/{job}", None),
        ("GET", f"/v1/jobs/{job}/events", None),
        ("POST", f"/v1/jobs/{job}/start", cuerpo),
        ("POST", f"/v1/jobs/{job}/finish", cuerpo),
        ("POST", f"/v1/jobs/{job}/pick-up", cuerpo),
        ("POST", f"/v1/jobs/{job}/cancel-delay", cuerpo),
        ("POST", f"/v1/jobs/{job}/inspection", {**cuerpo, "dirt_level": "BARRO"}),
        (
            "POST",
            f"/v1/jobs/{job}/payments",
            {**cuerpo, "payment_method_id": str(b.efectivo), "amount_cents": 1000},
        ),
        ("POST", f"/v1/jobs/{job}/payments/{pago}/void", {**cuerpo, "reason": "x"}),
    ]
    azar = str(uuid.uuid4())
    for metodo, ruta, json in intentos:
        ajeno = await http.request(metodo, ruta, json=json, headers={**hb, **_clave()})
        nada = await http.request(
            metodo, ruta.replace(job, azar), json=json, headers={**hb, **_clave()}
        )
        assert ajeno.status_code == nada.status_code == 404, (metodo, ruta, ajeno.text)
        assert ajeno.json() == nada.json(), (metodo, ruta)

    # Recibir el turno de A, o con el catálogo de A, desde B.
    con_servicio_ajeno = await http.post(
        "/v1/jobs",
        json={
            "occurred_at": _iso(ahora),
            "vehicle_size_id": str(b.auto),
            "service_ids": [str(a.lavado)],
        },
        headers={**hb, **_clave()},
    )
    assert con_servicio_ajeno.status_code == 404
    assert await _foto_de_a(pg_admin_engine, dos["ta"]) == antes


async def test_la_agenda_del_dia_de_b_no_trae_nada_de_a(http, dos):
    a, ha, hb = dos["a"], dos["ha"], dos["hb"]
    ayer = datetime.now(CORDOBA).date() - timedelta(days=1)
    r = await http.post(
        "/v1/jobs/retrospective",
        json={
            "started_at": _iso(datetime.combine(ayer, time(10), tzinfo=CORDOBA)),
            "finished_at": _iso(datetime.combine(ayer, time(11), tzinfo=CORDOBA)),
            "vehicle_size_id": str(a.auto),
            "service_ids": [str(a.lavado)],
        },
        headers={**ha, **_clave()},
    )
    assert r.status_code == 201, r.text
    de_a = (await http.get("/v1/agenda/day", params={"date": ayer.isoformat()}, headers=ha)).json()
    de_b = (await http.get("/v1/agenda/day", params={"date": ayer.isoformat()}, headers=hb)).json()
    assert len(de_a["entries"]) == 1
    assert de_b["entries"] == [] and de_b["resources"] != de_a["resources"]


async def test_carwash_app_no_ve_ni_cambia_entry_mode_de_otro_negocio(
    http, fabrica, pg_admin_engine, dos
):
    a, ha = dos["a"], dos["ha"]
    ayer = datetime.now(CORDOBA).date() - timedelta(days=1)
    r = await http.post(
        "/v1/jobs/retrospective",
        json={
            "started_at": _iso(datetime.combine(ayer, time(10), tzinfo=CORDOBA)),
            "finished_at": _iso(datetime.combine(ayer, time(11), tzinfo=CORDOBA)),
            "vehicle_size_id": str(a.auto),
            "service_ids": [str(a.lavado)],
        },
        headers={**ha, **_clave()},
    )
    job = r.json()["id"]
    async with fabrica() as s, s.begin():
        await set_tenant_context(s, dos["tb"])
        visto = await s.scalar(text("SELECT count(*) FROM jobs WHERE entry_mode = 'RETROSPECTIVA'"))
        cambio = await s.execute(
            text("UPDATE jobs SET entry_mode = 'EN_VIVO' WHERE id = :j"), {"j": job}
        )
    assert visto == 0 and cambio.rowcount == 0
    async with fabrica() as s, s.begin():  # sin contexto de tenant
        assert await s.scalar(text("SELECT count(*) FROM jobs")) == 0
        sin = await s.execute(text("UPDATE jobs SET entry_mode = 'EN_VIVO'"))
    assert sin.rowcount == 0
    async with pg_admin_engine.connect() as conn:
        assert (
            await conn.scalar(text("SELECT entry_mode::text FROM jobs WHERE id = :j"), {"j": job})
            == "RETROSPECTIVA"
        )
