# ruff: noqa: E501, F841, E731, C408
"""T3 F6 / 6.1 — punto 10 y lo que solo se puede afirmar contra Postgres real como `carwash_app`:
concurrencia (dos requests a la vez, sesiones separadas), choque del EXCLUDE, rollback real de
claves y filas auxiliares, y `timestamptz` con offset.

Correr:  CI=true PG_TEST_URL=postgresql://carwash:carwash@localhost:5437/carwash_test \\
         uv run pytest app/tests/adversarial_f6/test_pg_atomicidad_concurrencia.py -m postgres --no-cov -n 0
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, time, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.persistence.db.session import get_db_session, make_session_dependency
from app.tests.adversarial_f6._h import CORDOBA, a_mano, retro
from app.tests.application._armado import armar_catalogo
from app.tests.conftest import cookie_header
from app.tests.conftest_pg import PG_TEST_URL, _as_app_role, _with_driver
from app.utils.cookies import ACCESS_COOKIE
from app.utils.security import create_access_token

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

Factory = async_sessionmaker[AsyncSession]


def _sesion(user, tenant):
    token = create_access_token({"sub": str(user), "tenant_id": str(tenant), "ver": 0})
    return cookie_header({ACCESS_COOKIE: token})


def _k() -> dict[str, str]:
    return {"Idempotency-Key": f"k-{uuid.uuid4()}"}


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat()


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
    from app.api.rate_limit import limiter
    from app.main import create_app

    limiter.reset()
    app = create_app()
    app.dependency_overrides[get_db_session] = make_session_dependency(fabrica)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def mundo(
    fabrica: Factory, pg_tenant_a, pg_tenant_b, pg_user_factory, pg_admin_engine
) -> dict[str, Any]:
    owner_a = await pg_user_factory(pg_tenant_a, "f6t-owner-a@example.com")
    owner_b = await pg_user_factory(pg_tenant_b, "f6t-owner-b@example.com")
    out = {}
    for nombre, t, o in (("a", pg_tenant_a, owner_a), ("b", pg_tenant_b, owner_b)):
        async with fabrica() as s, s.begin():
            lv = await armar_catalogo(s, t, o)
            for dia in range(1, 8):
                await lv.catalogo.add_business_hours(dia, time(0, 0), time(23, 59))
        out[nombre] = lv
    out["ha"], out["hb"] = _sesion(owner_a, pg_tenant_a), _sesion(owner_b, pg_tenant_b)
    out["ta"], out["tb"] = pg_tenant_a, pg_tenant_b
    return out


async def _cuenta(admin, tabla: str, tenant, where: str = "") -> int:
    async with admin.connect() as conn:
        return (
            await conn.execute(
                text(f"SELECT count(*) FROM {tabla} WHERE tenant_id = :t {where}"), {"t": tenant}
            )
        ).scalar_one()  # noqa: S608


async def _post(http, ruta, cuerpo, h, k=None):
    return await http.post(
        ruta, json=cuerpo, headers={**h, "Idempotency-Key": k or f"k-{uuid.uuid4()}"}
    )


async def _job(http, mundo, estado="FINALIZADO"):
    lv, h = mundo["a"], mundo["ha"]
    ahora = datetime.now(UTC)
    j = (
        await _post(
            http,
            "/v1/jobs",
            {
                "occurred_at": _iso(ahora - timedelta(hours=1)),
                "plate": "AB123CD",
                "vehicle_size_id": str(lv.auto),
                "service_ids": [str(lv.lavado)],
                "customer_name": "Cliente Demo",
                "customer_phone": "+54 9 351 000-0101",
            },
            h,
        )
    ).json()
    if estado in ("EN_PROCESO", "FINALIZADO"):
        await _post(http, f"/v1/jobs/{j['id']}/start", {"occurred_at": _iso(ahora)}, h)
    if estado == "FINALIZADO":
        await _post(http, f"/v1/jobs/{j['id']}/finish", {"occurred_at": _iso(ahora)}, h)
    return j


def _pago(lv, monto=None):
    c = {"occurred_at": _iso(datetime.now(UTC)), "payment_method_id": str(lv.efectivo)}
    if monto:
        c["amount_cents"] = monto
    return c


# ── 10. Concurrencia ─────────────────────────────────────────────────────────


async def test_dos_cobros_del_saldo_a_la_vez_gana_uno(http, mundo, pg_admin_engine):
    j = await _job(http, mundo)
    rs = await asyncio.gather(
        *[
            _post(http, f"/v1/jobs/{j['id']}/payments", _pago(mundo["a"]), mundo["ha"])
            for _ in range(2)
        ]
    )
    codigos = sorted(r.status_code for r in rs)
    assert codigos == [200, 422], [(r.status_code, r.text[:200]) for r in rs]
    assert await _cuenta(pg_admin_engine, "payments", mundo["ta"]) == 1
    final = (await http.get(f"/v1/jobs/{j['id']}", headers=mundo["ha"])).json()
    assert final["paid_cents"] == 2_000_000 and final["status"] == "COBRADO"


async def test_cinco_cobros_del_saldo_a_la_vez(http, mundo, pg_admin_engine):
    j = await _job(http, mundo)
    rs = await asyncio.gather(
        *[
            _post(http, f"/v1/jobs/{j['id']}/payments", _pago(mundo["a"]), mundo["ha"])
            for _ in range(5)
        ]
    )
    assert sorted(r.status_code for r in rs) == [200, 422, 422, 422, 422], [
        r.status_code for r in rs
    ]
    assert await _cuenta(pg_admin_engine, "payments", mundo["ta"]) == 1


async def test_dos_cobros_parciales_que_juntos_pasan_el_total(http, mundo, pg_admin_engine):
    j = await _job(http, mundo)
    rs = await asyncio.gather(
        *[
            _post(http, f"/v1/jobs/{j['id']}/payments", _pago(mundo["a"], 1_200_000), mundo["ha"])
            for _ in range(2)
        ]
    )
    assert sorted(r.status_code for r in rs) == [200, 422]
    assert (await http.get(f"/v1/jobs/{j['id']}", headers=mundo["ha"])).json()[
        "balance_due_cents"
    ] == 800_000


async def test_dos_cobros_con_la_misma_clave_a_la_vez_es_uno_solo(http, mundo, pg_admin_engine):
    j = await _job(http, mundo)
    k = f"k-{uuid.uuid4()}"
    rs = await asyncio.gather(
        *[
            _post(http, f"/v1/jobs/{j['id']}/payments", _pago(mundo["a"], 700_000), mundo["ha"], k)
            for _ in range(4)
        ]
    )
    assert [r.status_code for r in rs] == [200] * 4, [(r.status_code, r.text[:150]) for r in rs]
    assert await _cuenta(pg_admin_engine, "payments", mundo["ta"]) == 1


async def test_cobro_sin_importe_misma_clave_a_la_vez(http, mundo, pg_admin_engine):
    """`amount=None` calcula el saldo ANTES del lock: la carrera del mismo toque no debe dar 422/500."""
    j = await _job(http, mundo)
    k = f"k-{uuid.uuid4()}"
    rs = await asyncio.gather(
        *[
            _post(http, f"/v1/jobs/{j['id']}/payments", _pago(mundo["a"]), mundo["ha"], k)
            for _ in range(4)
        ]
    )
    assert [r.status_code for r in rs] == [200] * 4, [(r.status_code, r.text[:150]) for r in rs]
    assert await _cuenta(pg_admin_engine, "payments", mundo["ta"]) == 1


async def test_doble_start_simultaneo_misma_clave_y_distinta(http, mundo, pg_admin_engine):
    j = await _job(http, mundo, "PRESENTE")
    k = f"k-{uuid.uuid4()}"
    cuerpo = {"occurred_at": _iso(datetime.now(UTC))}
    rs = await asyncio.gather(
        *[_post(http, f"/v1/jobs/{j['id']}/start", cuerpo, mundo["ha"], k) for _ in range(4)]
    )
    assert [r.status_code for r in rs] == [200] * 4
    assert (
        await _cuenta(pg_admin_engine, "job_events", mundo["ta"], "AND event_type='JOB_STARTED'")
        == 1
    )
    j2 = await _job(http, mundo, "PRESENTE")
    rs = await asyncio.gather(
        *[_post(http, f"/v1/jobs/{j2['id']}/start", cuerpo, mundo["ha"]) for _ in range(4)]
    )
    assert sorted(r.status_code for r in rs) == [200, 409, 409, 409], [r.status_code for r in rs]
    assert all(
        r.json()["detail"]["code"] == "INVALID_TRANSITION" for r in rs if r.status_code == 409
    )
    assert (
        await _cuenta(pg_admin_engine, "job_events", mundo["ta"], "AND event_type='JOB_STARTED'")
        == 2
    )


async def test_finish_y_start_cruzados_no_rompen(http, mundo):
    j = await _job(http, mundo, "PRESENTE")
    c = {"occurred_at": _iso(datetime.now(UTC))}
    rs = await asyncio.gather(
        _post(http, f"/v1/jobs/{j['id']}/finish", c, mundo["ha"]),
        _post(http, f"/v1/jobs/{j['id']}/start", c, mundo["ha"]),
        _post(http, f"/v1/jobs/{j['id']}/cancel-delay", c, mundo["ha"]),
    )
    assert all(r.status_code < 500 for r in rs), [r.status_code for r in rs]
    estado = (await http.get(f"/v1/jobs/{j['id']}", headers=mundo["ha"])).json()["status"]
    assert estado in ("EN_PROCESO", "FINALIZADO", "PRESENTE")


async def test_recibir_misma_clave_misma_patente_nueva_a_la_vez(http, mundo, pg_admin_engine):
    lv = mundo["a"]
    k = f"k-{uuid.uuid4()}"
    cuerpo = {
        "occurred_at": _iso(datetime.now(UTC)),
        "plate": "ZZ999TT",
        "vehicle_size_id": str(lv.auto),
        "service_ids": [str(lv.lavado)],
        "customer_name": "Nuevo",
        "customer_phone": "+54 9 351 000-0999",
    }
    rs = await asyncio.gather(*[_post(http, "/v1/jobs", cuerpo, mundo["ha"], k) for _ in range(4)])
    assert all(r.status_code == 201 for r in rs), [(r.status_code, r.text[:200]) for r in rs]
    assert len({r.json()["id"] for r in rs}) == 1
    assert await _cuenta(pg_admin_engine, "jobs", mundo["ta"]) == 1
    assert (
        await _cuenta(
            pg_admin_engine,
            "vehicles",
            mundo["ta"],
            "AND plate_normalized IS NOT NULL" if False else "",
        )
        >= 1
    )


async def test_recibir_dos_dispositivos_misma_patente_nueva_distinta_clave(
    http, mundo, pg_admin_engine
):
    """Dos celulares reciben el mismo auto nuevo a la vez: no debe haber 500 ni autos duplicados."""
    lv = mundo["a"]
    cuerpo = {
        "occurred_at": _iso(datetime.now(UTC)),
        "plate": "NN555MM",
        "vehicle_size_id": str(lv.auto),
        "service_ids": [str(lv.lavado)],
    }
    antes = await _cuenta(pg_admin_engine, "vehicles", mundo["ta"])
    rs = await asyncio.gather(*[_post(http, "/v1/jobs", cuerpo, mundo["ha"]) for _ in range(3)])
    assert all(r.status_code < 500 for r in rs), [(r.status_code, r.text[:200]) for r in rs]
    assert (
        await _cuenta(pg_admin_engine, "vehicles", mundo["ta"]) == antes + 1
    ), "autos duplicados para la misma patente"


async def test_recibir_dos_dispositivos_mismo_telefono_nuevo(http, mundo, pg_admin_engine):
    lv = mundo["a"]
    base = {
        "occurred_at": _iso(datetime.now(UTC)),
        "vehicle_size_id": str(lv.auto),
        "service_ids": [str(lv.lavado)],
        "customer_name": "Mismo Tel",
        "customer_phone": "+54 9 351 000-0444",
    }
    antes = await _cuenta(pg_admin_engine, "customers", mundo["ta"])
    rs = await asyncio.gather(*[_post(http, "/v1/jobs", base, mundo["ha"]) for _ in range(3)])
    assert all(r.status_code < 500 for r in rs), [(r.status_code, r.text[:200]) for r in rs]
    assert await _cuenta(pg_admin_engine, "customers", mundo["ta"]) == antes + 1


async def test_carga_retrospectiva_misma_clave_a_la_vez(http, mundo, pg_admin_engine):
    lv = mundo["a"]
    ayer = (datetime.now(CORDOBA) - timedelta(days=1)).date()
    k = f"k-{uuid.uuid4()}"
    c = retro(
        lv,
        ayer,
        payment={"payment_method_id": str(lv.efectivo)},
        customer_phone="+54 9 351 000-0321",
    )
    rs = await asyncio.gather(
        *[_post(http, "/v1/jobs/retrospective", c, mundo["ha"], k) for _ in range(4)]
    )
    assert all(r.status_code == 201 for r in rs), [(r.status_code, r.text[:200]) for r in rs]
    assert len({r.json()["id"] for r in rs}) == 1
    assert await _cuenta(pg_admin_engine, "jobs", mundo["ta"]) == 1
    assert await _cuenta(pg_admin_engine, "payments", mundo["ta"]) == 1
    assert await _cuenta(pg_admin_engine, "job_events", mundo["ta"]) == 5


async def test_turno_a_mano_mismo_horario_a_la_vez_gana_uno_slot_taken(
    http, mundo, pg_admin_engine
):
    lv = mundo["a"]
    c = a_mano(lv, datetime.now(UTC) + timedelta(hours=4))
    rs = await asyncio.gather(
        *[_post(http, "/v1/bookings/manual", c, mundo["ha"]) for _ in range(3)]
    )
    assert sorted(r.status_code for r in rs) == [201, 409, 409], [
        (r.status_code, r.text[:150]) for r in rs
    ]
    assert all(r.json()["detail"]["code"] == "SLOT_TAKEN" for r in rs if r.status_code == 409)
    assert await _cuenta(pg_admin_engine, "bookings", mundo["ta"]) == 1


async def test_turno_a_mano_misma_clave_a_la_vez_un_solo_turno(http, mundo, pg_admin_engine):
    lv = mundo["a"]
    k = f"k-{uuid.uuid4()}"
    c = a_mano(lv, datetime.now(UTC) + timedelta(hours=4))
    rs = await asyncio.gather(
        *[_post(http, "/v1/bookings/manual", c, mundo["ha"], k) for _ in range(3)]
    )
    assert sorted(r.status_code for r in rs) == [201, 409, 409], [
        (r.status_code, r.text[:150]) for r in rs
    ]
    assert await _cuenta(pg_admin_engine, "bookings", mundo["ta"]) == 1


async def test_dos_anulaciones_simultaneas_del_mismo_cobro(http, mundo, pg_admin_engine):
    j = await _job(http, mundo)
    pago = (
        await _post(http, f"/v1/jobs/{j['id']}/payments", _pago(mundo["a"]), mundo["ha"])
    ).json()["payments"][0]["id"]
    c = {"occurred_at": _iso(datetime.now(UTC)), "reason": "Error"}
    rs = await asyncio.gather(
        *[_post(http, f"/v1/jobs/{j['id']}/payments/{pago}/void", c, mundo["ha"]) for _ in range(3)]
    )
    assert all(r.status_code < 500 for r in rs), [(r.status_code, r.text[:150]) for r in rs]
    assert sum(r.status_code == 200 for r in rs) == 1
    assert (
        await _cuenta(pg_admin_engine, "job_events", mundo["ta"], "AND event_type='PAYMENT_VOIDED'")
        == 1
    )


async def test_cobro_y_anulacion_a_la_vez_no_dejan_el_saldo_inconsistente(http, mundo):
    j = await _job(http, mundo)
    pago = (
        await _post(http, f"/v1/jobs/{j['id']}/payments", _pago(mundo["a"], 1_000_000), mundo["ha"])
    ).json()["payments"][0]["id"]
    c = {"occurred_at": _iso(datetime.now(UTC)), "reason": "Error"}
    rs = await asyncio.gather(
        _post(http, f"/v1/jobs/{j['id']}/payments/{pago}/void", c, mundo["ha"]),
        _post(http, f"/v1/jobs/{j['id']}/payments", _pago(mundo["a"], 1_000_000), mundo["ha"]),
    )
    assert all(r.status_code < 500 for r in rs)
    f = (await http.get(f"/v1/jobs/{j['id']}", headers=mundo["ha"])).json()
    vivos = sum(p["amount_cents"] for p in f["payments"] if not p["voided"])
    assert f["paid_cents"] == vivos and f["balance_due_cents"] == f["total_cents"] - vivos
    assert f["paid_cents"] <= f["total_cents"]


# ── Choque del EXCLUDE ───────────────────────────────────────────────────────


async def test_turno_a_mano_choques_y_bordes(http, mundo):
    lv, h = mundo["a"], mundo["ha"]
    t = (datetime.now(UTC) + timedelta(hours=6)).replace(second=0, microsecond=0)
    assert (await _post(http, "/v1/bookings/manual", a_mano(lv, t), h)).status_code == 201  # 60 min
    for delta, esperado in (
        (timedelta(0), 409),
        (timedelta(minutes=30), 409),
        (timedelta(minutes=59), 409),
        (timedelta(minutes=-59), 409),
        (timedelta(minutes=60), 201),
        (timedelta(minutes=-60), 201),
    ):
        r = await _post(http, "/v1/bookings/manual", a_mano(lv, t + delta), h)
        assert r.status_code == esperado, (delta, r.status_code, r.text[:200])
        if esperado == 409:
            assert r.json()["detail"]["code"] == "SLOT_TAKEN"
    otro_puesto = {**a_mano(lv, t), "resource_id": str(lv.puesto_2)}
    assert (await _post(http, "/v1/bookings/manual", otro_puesto, h)).status_code == 201


async def test_turno_a_mano_pisa_a_un_turno_de_la_web_y_a_un_hold_de_sena(http, mundo, fabrica):
    lv, h = mundo["a"], mundo["ha"]
    t = (datetime.now(UTC) + timedelta(hours=9)).replace(second=0, microsecond=0)
    async with fabrica() as s, s.begin():
        await lv.en(s).turno(inicio=t, ahora=datetime.now(UTC) - timedelta(days=1))
    r = await _post(http, "/v1/bookings/manual", a_mano(lv, t + timedelta(minutes=10)), h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SLOT_TAKEN"
    async with fabrica() as s, s.begin():
        await lv.en(s).activar_sena()
    t2 = t + timedelta(hours=5)
    hold = await _post(
        http, "/v1/bookings/manual", a_mano(lv, t2, service_ids=[str(lv.lavado_con_sena)]), h
    )
    assert hold.json()["booking"]["status"] == "PENDIENTE_SEÑA"
    r = await _post(
        http,
        "/v1/bookings/manual",
        a_mano(lv, t2 + timedelta(minutes=5), service_ids=[str(lv.lavado_con_sena)]),
        h,
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SLOT_TAKEN"


async def test_la_carga_retrospectiva_no_choca_con_nada(http, mundo):
    lv, h = mundo["a"], mundo["ha"]
    ayer = (datetime.now(CORDOBA) - timedelta(days=1)).date()
    c = retro(lv, ayer, resource_id=str(lv.puesto))
    assert (await _post(http, "/v1/jobs/retrospective", c, h)).status_code == 201
    assert (await _post(http, "/v1/jobs/retrospective", c, h)).status_code == 201


# ── Rollback real: filas auxiliares y claves ─────────────────────────────────


async def test_retro_con_medio_de_pago_inexistente_no_deja_job_ni_cliente_ni_auto(
    http, mundo, pg_admin_engine
):
    lv, h, ta = mundo["a"], mundo["ha"], mundo["ta"]
    antes = {
        t: await _cuenta(pg_admin_engine, t, ta)
        for t in ("jobs", "job_events", "customers", "vehicles", "payments", "idempotency_keys")
    }
    ayer = (datetime.now(CORDOBA) - timedelta(days=1)).date()
    c = retro(
        lv,
        ayer,
        payment={"payment_method_id": str(uuid.uuid4())},
        customer_name="Fantasma",
        customer_phone="+54 9 351 000-0666",
        plate="FA111NT",
    )
    r = await _post(http, "/v1/jobs/retrospective", c, h)
    assert r.status_code == 404, r.text
    despues = {t: await _cuenta(pg_admin_engine, t, ta) for t in antes}
    assert despues == antes, {t: (antes[t], despues[t]) for t in antes if antes[t] != despues[t]}


async def test_retro_con_medio_de_otro_negocio_no_deja_nada(http, mundo, pg_admin_engine):
    lv_a, lv_b = mundo["a"], mundo["b"]
    tb = mundo["tb"]
    antes = {
        t: await _cuenta(pg_admin_engine, t, tb)
        for t in ("jobs", "customers", "vehicles", "payments")
    }
    ayer = (datetime.now(CORDOBA) - timedelta(days=1)).date()
    c = retro(
        lv_b,
        ayer,
        payment={"payment_method_id": str(lv_a.efectivo)},
        customer_phone="+54 9 351 000-0667",
        plate="FB111NT",
    )
    r = await _post(http, "/v1/jobs/retrospective", c, mundo["hb"])
    assert r.status_code == 404
    assert {t: await _cuenta(pg_admin_engine, t, tb) for t in antes} == antes


async def test_recibir_con_servicio_inexistente_no_deja_auto_anonimo(http, mundo, pg_admin_engine):
    lv, h, ta = mundo["a"], mundo["ha"], mundo["ta"]
    antes = (
        await _cuenta(pg_admin_engine, "vehicles", ta),
        await _cuenta(pg_admin_engine, "customers", ta),
    )
    r = await _post(
        http,
        "/v1/jobs",
        {
            "occurred_at": _iso(datetime.now(UTC)),
            "vehicle_size_id": str(lv.auto),
            "service_ids": [str(uuid.uuid4())],
            "customer_name": "Huérfano",
            "customer_phone": "+54 9 351 000-0668",
        },
        h,
    )
    assert r.status_code == 404
    assert (
        await _cuenta(pg_admin_engine, "vehicles", ta),
        await _cuenta(pg_admin_engine, "customers", ta),
    ) == antes


async def test_turno_a_mano_con_medio_ajeno_no_deja_turno_ni_cliente_y_libera_la_clave(
    http, mundo, pg_admin_engine
):
    lv, lv_b, h, ta = mundo["a"], mundo["b"], mundo["ha"], mundo["ta"]
    await lv.configurar if False else None
    antes = {
        t: await _cuenta(pg_admin_engine, t, ta)
        for t in ("bookings", "customers", "vehicles", "idempotency_keys")
    }
    k = f"k-{uuid.uuid4()}"
    inicio = datetime.now(UTC) + timedelta(hours=3)
    r = await _post(
        http,
        "/v1/bookings/manual",
        a_mano(lv, inicio, deposit={"payment_method_id": str(lv_b.efectivo)}),
        h,
        k,
    )
    assert r.status_code in (404, 422), r.text
    assert {t: await _cuenta(pg_admin_engine, t, ta) for t in antes} == antes
    ok = await _post(http, "/v1/bookings/manual", a_mano(lv, inicio), h, k)
    assert (
        ok.status_code == 201
    ), f"la clave quedó quemada tras el error: {ok.status_code} {ok.text[:200]}"


async def test_la_clave_del_turno_a_mano_se_libera_tras_422_403_y_slot_taken(
    http, mundo, fabrica, pg_user_factory, pg_admin_engine
):
    lv, h, ta = mundo["a"], mundo["ha"], mundo["ta"]
    inicio = (datetime.now(UTC) + timedelta(hours=12)).replace(second=0, microsecond=0)
    # 422: pasado
    k1 = f"k-{uuid.uuid4()}"
    assert (
        await _post(
            http, "/v1/bookings/manual", a_mano(lv, datetime.now(UTC) - timedelta(hours=1)), h, k1
        )
    ).status_code == 422
    assert (
        await _post(http, "/v1/bookings/manual", a_mano(lv, inicio), h, k1)
    ).status_code == 201, "clave quemada tras 422"
    # 409 SLOT_TAKEN: choca, reintenta la misma clave en otro horario libre
    k2 = f"k-{uuid.uuid4()}"
    assert (await _post(http, "/v1/bookings/manual", a_mano(lv, inicio), h, k2)).status_code == 409
    assert (
        await _post(http, "/v1/bookings/manual", a_mano(lv, inicio + timedelta(hours=3)), h, k2)
    ).status_code == 201, "clave quemada tras SLOT_TAKEN"
    # 403 (seña sin COBROS_REGISTRAR)
    async with fabrica() as s, s.begin():
        await lv.en(s).activar_sena()
    from app.domain.roles import Role  # noqa: F401

    async with pg_admin_engine.begin() as conn:
        pid, uid = uuid.uuid4(), uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO permission_profiles (id, tenant_id, name, permissions) VALUES (:i,:t,'gestor',ARRAY['TURNOS_GESTIONAR'])"
            ),
            {"i": pid, "t": ta},
        )
        await conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, password_hash, role, permission_profile_id) VALUES (:i,:t,'f6t-gestor@example.com','x','STAFF',:p)"
            ),
            {"i": uid, "t": ta, "p": pid},
        )
    gestor = _sesion(uid, ta)
    k3 = f"k-{uuid.uuid4()}"
    c = a_mano(
        lv,
        inicio + timedelta(hours=8),
        service_ids=[str(lv.lavado_con_sena)],
        deposit={"payment_method_id": str(lv.efectivo)},
    )
    assert (await _post(http, "/v1/bookings/manual", c, gestor, k3)).status_code == 403
    r = await _post(http, "/v1/bookings/manual", c, h, k3)
    assert r.status_code == 201, f"clave quemada tras 403: {r.status_code} {r.text[:200]}"


async def test_un_occurred_at_con_offset_se_guarda_como_el_mismo_instante(http, mundo):
    j = await _job(http, mundo, "PRESENTE")
    t = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
    local = t.astimezone(timezone(timedelta(hours=-3))).isoformat()
    r = await _post(http, f"/v1/jobs/{j['id']}/start", {"occurred_at": local}, mundo["ha"])
    assert r.status_code == 200
    ev = (
        await http.get(f"/v1/jobs/{j['id']}/events", headers=mundo["ha"], params={"limit": 50})
    ).json()["items"]
    inicio = next(e for e in ev if e["event_type"] == "JOB_STARTED")
    assert datetime.fromisoformat(inicio["occurred_at"]) == t


async def test_la_misma_patente_en_otro_negocio_es_otro_auto_sin_filtrar(http, mundo):
    a, b = mundo["a"], mundo["b"]
    ja = (
        await _post(
            http,
            "/v1/jobs",
            {
                "occurred_at": _iso(datetime.now(UTC)),
                "plate": "SH123RD",
                "vehicle_size_id": str(a.auto),
                "service_ids": [str(a.lavado)],
            },
            mundo["ha"],
        )
    ).json()
    jb = await _post(
        http,
        "/v1/jobs",
        {
            "occurred_at": _iso(datetime.now(UTC)),
            "plate": "SH123RD",
            "vehicle_size_id": str(b.auto),
            "service_ids": [str(b.lavado)],
            "customer_name": "Cliente B",
        },
        mundo["hb"],
    )
    assert jb.status_code == 201, jb.text
    assert jb.json()["id"] != ja["id"] and jb.json()["customer"]["name"] == "Cliente B"
    assert (await http.get(f"/v1/jobs/{ja['id']}", headers=mundo["hb"])).status_code == 404


async def test_agenda_en_postgres_incluye_retro_en_su_dia_y_no_en_hoy(http, mundo):
    lv, h = mundo["a"], mundo["ha"]
    ayer = (datetime.now(CORDOBA) - timedelta(days=1)).date()
    c = {
        **retro(lv, ayer),
        "started_at": _iso(datetime.combine(ayer, time(23, 30), tzinfo=CORDOBA)),
        "finished_at": _iso(datetime.combine(ayer, time(23, 45), tzinfo=CORDOBA)),
    }
    j = (await _post(http, "/v1/jobs/retrospective", c, h)).json()
    d_ayer = (await http.get("/v1/agenda/day", params={"date": ayer.isoformat()}, headers=h)).json()
    d_hoy = (
        await http.get(
            "/v1/agenda/day", params={"date": datetime.now(CORDOBA).date().isoformat()}, headers=h
        )
    ).json()
    assert [e["job_id"] for e in d_ayer["entries"]] == [j["id"]] and d_hoy["entries"] == []
