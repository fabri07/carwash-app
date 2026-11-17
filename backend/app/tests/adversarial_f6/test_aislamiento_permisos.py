# ruff: noqa: E501, F841, E731, C408
"""T3 F6 / 6.1 — puntos 5 y 6: aislamiento (path, body, query) y permisos de cada ruta nueva."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.domain.permissions import Permission
from app.persistence.models import Booking, Customer, Job, JobEvent, Payment, Vehicle
from app.tests.adversarial_f6._h import (
    CORDOBA,
    a_mano,
    accion,
    clave,
    cobrar,
    empleado,
    iso,
    job_en,
    retro,
)


async def _conteos(db_session) -> dict[str, int]:
    out = {}
    for m in (Job, JobEvent, Payment, Customer, Vehicle, Booking):
        out[m.__name__] = (
            await db_session.execute(select(func.count()).select_from(m))
        ).scalar_one()
    return out


def _cuerpo_vacio():
    return {"occurred_at": iso(datetime.now(UTC))}


# ── 5. Aislamiento: ids de A en body/path usados por B ───────────────────────


async def _comparar(client, cookies, metodo, ruta, cuerpo_con_ajeno, cuerpo_con_azar):
    ajeno = await client.request(
        metodo, ruta, json=cuerpo_con_ajeno, cookies=cookies, headers=clave()
    )
    azar = await client.request(
        metodo, ruta, json=cuerpo_con_azar, cookies=cookies, headers=clave()
    )
    return ajeno, azar


@pytest.mark.parametrize(
    "campo",
    [
        "vehicle_id",
        "customer_id",
        "resource_id",
        "quote_id",
        "booking_id",
        "vehicle_size_id",
        "service_ids",
    ],
)
async def test_b_con_ids_de_a_en_el_body_de_recibir_es_el_mismo_404_que_con_un_id_al_azar(
    client, cookies_b, lav, lav_b, db_session, campo
):
    ahora = datetime.now(UTC)
    base = {
        "occurred_at": iso(ahora),
        "vehicle_size_id": str(lav_b.auto),
        "service_ids": [str(lav_b.lavado)],
    }
    ajeno_id = {
        "vehicle_id": lav.vehiculo,
        "customer_id": lav.cliente,
        "resource_id": lav.puesto,
        "quote_id": uuid.uuid4(),
        "booking_id": (
            await lav.turno(inicio=ahora + timedelta(hours=2), ahora=ahora - timedelta(days=1))
        ).id,
        "vehicle_size_id": lav.auto,
        "service_ids": lav.lavado,
    }[campo]
    valor = lambda v: [str(v)] if campo == "service_ids" else str(v)  # noqa: E731
    antes = await _conteos(db_session)
    ajeno, azar = await _comparar(
        client,
        cookies_b,
        "POST",
        "/v1/jobs",
        {**base, campo: valor(ajeno_id)},
        {**base, campo: valor(uuid.uuid4())},
    )
    if campo == "quote_id":  # una cotización inexistente en un servicio que no se cotiza se ignora
        assert ajeno.status_code == azar.status_code
        return
    assert ajeno.status_code == azar.status_code == 404, (campo, ajeno.status_code, ajeno.text)
    assert ajeno.json() == azar.json(), campo
    # (el rollback real de las filas auxiliares se verifica en test_pg_atomicidad_concurrencia.py)


@pytest.mark.parametrize(
    "campo", ["vehicle_size_id", "service_ids", "payment_method_id", "resource_id"]
)
async def test_b_con_ids_de_a_en_la_carga_retrospectiva(
    client, cookies_b, lav, lav_b, db_session, campo
):
    ayer = (datetime.now(CORDOBA) - timedelta(days=1)).date()
    base = retro(lav_b, ayer, payment={"payment_method_id": str(lav_b.efectivo)})
    ajeno = {
        "vehicle_size_id": lav.auto,
        "service_ids": lav.lavado,
        "payment_method_id": lav.efectivo,
        "resource_id": lav.puesto,
    }[campo]

    def armar(v):
        c = {**base, "payment": dict(base["payment"])}
        if campo == "service_ids":
            c[campo] = [str(v)]
        elif campo == "payment_method_id":
            c["payment"][campo] = str(v)
        else:
            c[campo] = str(v)
        return c

    antes = await _conteos(db_session)
    r1 = await client.post(
        "/v1/jobs/retrospective", json=armar(ajeno), cookies=cookies_b, headers=clave()
    )
    r2 = await client.post(
        "/v1/jobs/retrospective", json=armar(uuid.uuid4()), cookies=cookies_b, headers=clave()
    )
    assert r1.status_code == r2.status_code == 404, (campo, r1.status_code, r1.text)
    assert r1.json() == r2.json()
    assert "Job" not in r1.text


@pytest.mark.parametrize(
    "campo", ["resource_id", "vehicle_size_id", "service_ids", "payment_method_id"]
)
async def test_b_con_ids_de_a_en_el_turno_a_mano(client, cookies_b, lav, lav_b, campo):
    inicio = datetime.now(UTC) + timedelta(hours=3)
    base = a_mano(lav_b, inicio)
    base["resource_id"] = str(lav_b.puesto)
    ajeno = {
        "resource_id": lav.puesto,
        "vehicle_size_id": lav.auto,
        "service_ids": lav.lavado,
        "payment_method_id": lav.efectivo,
    }[campo]

    def armar(v):
        c = dict(base)
        if campo == "service_ids":
            c[campo] = [str(v)]
        elif campo == "payment_method_id":
            c["deposit"] = {"payment_method_id": str(v)}
        else:
            c[campo] = str(v)
        return c

    r1 = await client.post(
        "/v1/bookings/manual", json=armar(ajeno), cookies=cookies_b, headers=clave()
    )
    r2 = await client.post(
        "/v1/bookings/manual", json=armar(uuid.uuid4()), cookies=cookies_b, headers=clave()
    )
    assert r1.status_code == r2.status_code, (
        campo,
        r1.status_code,
        r2.status_code,
        r1.text,
        r2.text,
    )
    assert r1.status_code in (404, 422), r1.text
    assert r1.json() == r2.json(), campo


async def test_b_no_anula_ni_cobra_con_ids_cruzados(client, cookies_a, cookies_b, lav, lav_b):
    ja = await job_en(client, cookies_a, lav, "FINALIZADO")
    pa = (await cobrar(client, cookies_a, lav, ja["id"], 1000)).json()["payments"][0]["id"]
    jb = await job_en(client, cookies_b, lav_b, "FINALIZADO")
    pb = (await cobrar(client, cookies_b, lav_b, jb["id"], 1000)).json()["payments"][0]["id"]
    cuerpo = {"occurred_at": iso(datetime.now(UTC)), "reason": "x"}
    azar = uuid.uuid4()
    casos = [
        (f"/v1/jobs/{jb['id']}/payments/{pa}/void"),  # job de B + pago de A
        (f"/v1/jobs/{ja['id']}/payments/{pb}/void"),  # job de A + pago de B
        (f"/v1/jobs/{ja['id']}/payments/{pa}/void"),  # todo de A
    ]
    esperado = await client.post(
        f"/v1/jobs/{azar}/payments/{azar}/void", json=cuerpo, cookies=cookies_b, headers=clave()
    )
    for ruta in casos:
        r = await client.post(ruta, json=cuerpo, cookies=cookies_b, headers=clave())
        assert r.status_code == 404 and r.json() == esperado.json(), (ruta, r.status_code)
    # cobrar en el job de A con el medio de B, y en el de B con el medio de A
    for jid, medio in ((ja["id"], lav_b.efectivo), (jb["id"], lav.efectivo)):
        r = await client.post(
            f"/v1/jobs/{jid}/payments",
            json={
                "occurred_at": iso(datetime.now(UTC)),
                "payment_method_id": str(medio),
                "amount_cents": 1,
            },
            cookies=cookies_b,
            headers=clave(),
        )
        assert r.status_code == 404, (jid, r.status_code, r.text)
    assert (await client.get(f"/v1/jobs/{ja['id']}", cookies=cookies_a)).json()[
        "paid_cents"
    ] == 1000


async def test_tenant_id_en_el_body_o_el_query_se_ignora(
    client, cookies_a, cookies_b, lav, lav_b, db_session, tenant_a
):
    ayer = (datetime.now(CORDOBA) - timedelta(days=1)).date()
    n_a = (
        await db_session.execute(
            select(func.count()).select_from(Job).where(Job.tenant_id == tenant_a.id)
        )
    ).scalar_one()
    r = await client.post(
        f"/v1/jobs/retrospective?tenant_id={tenant_a.id}",
        json={**retro(lav_b, ayer), "tenant_id": str(tenant_a.id)},
        cookies=cookies_b,
        headers={**clave(), "X-Tenant-Id": str(tenant_a.id)},
    )
    assert r.status_code == 201, r.text
    assert (
        await db_session.execute(
            select(func.count()).select_from(Job).where(Job.tenant_id == tenant_a.id)
        )
    ).scalar_one() == n_a
    hoy = datetime.now(CORDOBA).date().isoformat()
    d = await client.get(
        "/v1/agenda/day",
        params={"date": ayer.isoformat(), "tenant_id": str(tenant_a.id)},
        cookies=cookies_b,
    )
    assert d.status_code == 200 and [e["job_id"] for e in d.json()["entries"]] == [r.json()["id"]]


async def test_el_404_de_un_job_ajeno_no_filtra_nada_en_ningun_header_ni_mensaje(
    client, cookies_a, cookies_b, lav, lav_b
):
    ja = await job_en(client, cookies_a, lav, "PRESENTE")
    ajeno = await client.get(f"/v1/jobs/{ja['id']}", cookies=cookies_b)
    azar = await client.get(f"/v1/jobs/{uuid.uuid4()}", cookies=cookies_b)
    assert ajeno.status_code == azar.status_code == 404
    mask = lambda r: {
        k: v
        for k, v in r.headers.items()
        if k.lower() not in ("x-request-id", "content-length", "date", "x-trace-id")
    }  # noqa: E731
    assert mask(ajeno) == mask(azar)
    texto = ajeno.text.replace(ja["id"], "ID")
    assert (
        texto == azar.text.replace(str(uuid.uuid4()), "ID")
        or ajeno.json()["detail"] == azar.json()["detail"]
    )
    assert "AB 123 CD" not in ajeno.text and "Cliente Demo" not in ajeno.text


async def test_los_ids_mal_formados_son_422_no_500(client, cookies_a):
    for ruta in (
        "/v1/jobs/no-es-uuid",
        "/v1/jobs/123/events",
        f"/v1/jobs/{uuid.uuid4()}/payments/xx/void",
    ):
        r = await client.request(
            "GET" if "void" not in ruta else "POST",
            ruta,
            json=_cuerpo_vacio() if "void" in ruta else None,
            cookies=cookies_a,
            headers=clave(),
        )
        assert r.status_code in (404, 405, 422), (ruta, r.status_code)


async def test_get_jobs_retrospective_no_se_confunde_con_un_id(client, cookies_a):
    r = await client.get("/v1/jobs/retrospective", cookies=cookies_a)
    assert r.status_code in (404, 405, 422)


# ── 6. Permisos ──────────────────────────────────────────────────────────────

TODOS = set(Permission)


def _rutas(lav, job_id, pago_id, booking_id=None):
    ahora = datetime.now(UTC)
    ayer = (datetime.now(CORDOBA) - timedelta(days=1)).date()
    return {
        "GET /agenda/day": (
            "GET",
            "/v1/agenda/day?date=" + ayer.isoformat(),
            None,
            Permission.AGENDA_VER,
        ),
        "GET /jobs/{id}": ("GET", f"/v1/jobs/{job_id}", None, Permission.AGENDA_VER),
        "GET /jobs/{id}/events": ("GET", f"/v1/jobs/{job_id}/events", None, Permission.AGENDA_VER),
        "POST /jobs": (
            "POST",
            "/v1/jobs",
            {
                "occurred_at": iso(ahora),
                "vehicle_size_id": str(lav.auto),
                "service_ids": [str(lav.lavado)],
            },
            Permission.JOBS_OPERAR,
        ),
        "start": (
            "POST",
            f"/v1/jobs/{job_id}/start",
            {"occurred_at": iso(ahora)},
            Permission.JOBS_OPERAR,
        ),
        "finish": (
            "POST",
            f"/v1/jobs/{job_id}/finish",
            {"occurred_at": iso(ahora)},
            Permission.JOBS_OPERAR,
        ),
        "pick-up": (
            "POST",
            f"/v1/jobs/{job_id}/pick-up",
            {"occurred_at": iso(ahora)},
            Permission.JOBS_OPERAR,
        ),
        "cancel-delay": (
            "POST",
            f"/v1/jobs/{job_id}/cancel-delay",
            {"occurred_at": iso(ahora)},
            Permission.JOBS_OPERAR,
        ),
        "inspection": (
            "POST",
            f"/v1/jobs/{job_id}/inspection",
            {"occurred_at": iso(ahora)},
            Permission.JOBS_OPERAR,
        ),
        "payments": (
            "POST",
            f"/v1/jobs/{job_id}/payments",
            {"occurred_at": iso(ahora), "payment_method_id": str(lav.efectivo)},
            Permission.COBROS_REGISTRAR,
        ),
        "void": (
            "POST",
            f"/v1/jobs/{job_id}/payments/{pago_id}/void",
            {"occurred_at": iso(ahora), "reason": "x"},
            Permission.COBROS_ANULAR,
        ),
        "retrospective": (
            "POST",
            "/v1/jobs/retrospective",
            retro(lav, ayer),
            Permission.JOBS_OPERAR,
        ),
        "manual": (
            "POST",
            "/v1/bookings/manual",
            a_mano(lav, ahora + timedelta(hours=4)),
            Permission.TURNOS_GESTIONAR,
        ),
    }


async def test_sin_sesion_toda_ruta_es_401(client, lav, cookies_a):
    job = await job_en(client, cookies_a, lav, "FINALIZADO")
    pago = (await cobrar(client, cookies_a, lav, job["id"], 1000)).json()["payments"][0]["id"]
    for nombre, (m, ruta, cuerpo, _) in _rutas(lav, job["id"], pago).items():
        r = await client.request(m, ruta, json=cuerpo, headers=clave())
        assert r.status_code == 401, (nombre, r.status_code)


async def test_cada_ruta_con_todos_los_permisos_menos_el_suyo_es_403_y_no_toca_nada(
    client, cookies_a, lav, db_session, tenant_a
):
    job = await job_en(client, cookies_a, lav, "FINALIZADO")
    pago = (await cobrar(client, cookies_a, lav, job["id"], 1000)).json()["payments"][0]["id"]
    antes = await _conteos(db_session)
    for nombre, (m, ruta, cuerpo, perm) in _rutas(lav, job["id"], pago).items():
        user = await empleado(
            db_session, tenant_a, f"Sin {nombre}".replace("/", "_"), TODOS - {perm}
        )
        r = await client.request(m, ruta, json=cuerpo, cookies=user, headers=clave())
        assert r.status_code == 403, (nombre, r.status_code, r.text)
    assert await _conteos(db_session) == antes


async def test_con_solo_agenda_ver_no_se_puede_operar(client, cookies_a, lav, db_session, tenant_a):
    job = await job_en(client, cookies_a, lav, "FINALIZADO")
    pago = (await cobrar(client, cookies_a, lav, job["id"], 1000)).json()["payments"][0]["id"]
    solo = await empleado(db_session, tenant_a, "Solo Ver Agenda", {Permission.AGENDA_VER})
    antes = await _conteos(db_session)
    for nombre, (m, ruta, cuerpo, perm) in _rutas(lav, job["id"], pago).items():
        r = await client.request(m, ruta, json=cuerpo, cookies=solo, headers=clave())
        esperado = 200 if perm == Permission.AGENDA_VER else 403
        assert r.status_code == esperado, (nombre, r.status_code)
    assert await _conteos(db_session) == antes


async def test_cada_permiso_alcanza_solo_para_su_ruta(client, cookies_a, lav, db_session, tenant_a):
    """Con SOLO el permiso de la ruta (y sin AGENDA_VER salvo en lecturas) la ruta responde distinto de 403."""
    job = await job_en(client, cookies_a, lav, "FINALIZADO")
    pago = (await cobrar(client, cookies_a, lav, job["id"], 1000)).json()["payments"][0]["id"]
    for nombre, (m, ruta, cuerpo, perm) in _rutas(lav, job["id"], pago).items():
        user = await empleado(db_session, tenant_a, f"Solo {nombre}".replace("/", "_"), {perm})
        r = await client.request(m, ruta, json=cuerpo, cookies=user, headers=clave())
        assert r.status_code != 403, (nombre, r.status_code, r.text)


async def test_retrospectiva_con_cobro_sin_cobros_registrar_es_403_y_no_crea_nada(
    client, cookies_a, lav, db_session, tenant_a
):
    ayer = (datetime.now(CORDOBA) - timedelta(days=1)).date()
    solo_jobs = await empleado(db_session, tenant_a, "Solo Operar", {Permission.JOBS_OPERAR})
    solo_cobros = await empleado(db_session, tenant_a, "Solo Cobrar", {Permission.COBROS_REGISTRAR})
    antes = await _conteos(db_session)
    con_cobro = retro(
        lav,
        ayer,
        payment={"payment_method_id": str(lav.efectivo)},
        customer_phone="+54 9 351 000-0777",
    )
    assert (
        await client.post(
            "/v1/jobs/retrospective", json=con_cobro, cookies=solo_jobs, headers=clave()
        )
    ).status_code == 403
    assert (
        await client.post(
            "/v1/jobs/retrospective", json=con_cobro, cookies=solo_cobros, headers=clave()
        )
    ).status_code == 403
    assert (
        await client.post(
            "/v1/jobs/retrospective", json=retro(lav, ayer), cookies=solo_cobros, headers=clave()
        )
    ).status_code == 403
    assert await _conteos(db_session) == antes
    # payment: null explícito no exige cobrar
    assert (
        await client.post(
            "/v1/jobs/retrospective",
            json={**retro(lav, ayer), "payment": None},
            cookies=solo_jobs,
            headers=clave(),
        )
    ).status_code == 201


async def test_403_no_revela_si_el_job_existe(client, cookies_a, lav, db_session, tenant_a):
    job = await job_en(client, cookies_a, lav, "PRESENTE")
    sin = await empleado(db_session, tenant_a, "Sin Operar", {Permission.AGENDA_VER})
    real = await accion(client, sin, job["id"], "start")
    falso = await accion(client, sin, uuid.uuid4(), "start")
    assert real.status_code == falso.status_code == 403
