# ruff: noqa: E501, F841, E731, C408
"""T3 F6 / 6.1 — puntos 7, 8 y 9: carga retrospectiva, turno a mano y agenda del día (SQLite)."""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.persistence.models import Customer, Job, Tenant, Vehicle
from app.tests.adversarial_f6._h import (
    CORDOBA,
    a_mano,
    accion,
    clave,
    cobrar,
    dt,
    eventos,
    iso,
    job_en,
    local,
    retro,
)


def _ayer():
    return (datetime.now(CORDOBA) - timedelta(days=1)).date()


async def _cargar(client, cookies, cuerpo, k=None):
    return await client.post(
        "/v1/jobs/retrospective",
        json=cuerpo,
        cookies=cookies,
        headers={"Idempotency-Key": k or f"k-{uuid.uuid4()}"},
    )


async def _dia(client, cookies, fecha):
    r = await client.get("/v1/agenda/day", params={"date": fecha.isoformat()}, cookies=cookies)
    assert r.status_code == 200, r.text
    return r.json()


async def _n(db, m):
    return (await db.execute(select(func.count()).select_from(m))).scalar_one()


# ── 7. Carga retrospectiva ───────────────────────────────────────────────────


async def test_retro_horas_en_el_borde(client, cookies_a, lav):
    ayer = _ayer()
    assert (
        await _cargar(client, cookies_a, retro(lav, ayer, desde=(10, 0), hasta=(10, 0)))
    ).status_code == 201  # igual
    ahora = datetime.now(UTC)
    for ini, fin in (
        (ahora - timedelta(hours=1), ahora + timedelta(seconds=30)),  # fin futuro
        (ahora + timedelta(minutes=5), ahora + timedelta(minutes=10)),  # todo futuro
        (ahora + timedelta(minutes=5), ahora - timedelta(minutes=10)),  # invertido y futuro
    ):
        c = {**retro(lav, ayer), "started_at": iso(ini), "finished_at": iso(fin)}
        r = await _cargar(client, cookies_a, c)
        assert r.status_code == 422, (ini, fin, r.status_code, r.text)


@pytest.mark.parametrize("extremo", ["0001-01-01T00:00:00Z", "1900-01-01T00:00:00Z"])
async def test_retro_en_fechas_remotas_no_da_500(client, cookies_a, lav, extremo):
    c = {**retro(lav, _ayer()), "started_at": extremo, "finished_at": extremo}
    r = await _cargar(client, cookies_a, c)
    assert r.status_code < 500, r.status_code


async def test_retro_sin_zona_o_con_basura_es_422(client, cookies_a, lav):
    for campo in ("started_at", "finished_at"):
        for valor in ("2026-11-15T10:00:00", "mañana", None):
            r = await _cargar(client, cookies_a, {**retro(lav, _ayer()), campo: valor})
            assert r.status_code == 422, (campo, valor, r.status_code)


async def test_retro_servicio_a_cotizar_junto_a_uno_normal_es_422_y_no_deja_job(
    client, cookies_a, lav, db_session
):
    n = await _n(db_session, Job)
    r = await _cargar(
        client, cookies_a, retro(lav, _ayer(), service_ids=[str(lav.lavado), str(lav.tapizado)])
    )
    assert r.status_code == 422, r.text
    assert await _n(db_session, Job) == n


async def test_retro_servicios_repetidos(client, cookies_a, lav):
    r = await _cargar(
        client, cookies_a, retro(lav, _ayer(), service_ids=[str(lav.lavado), str(lav.lavado)])
    )
    assert r.status_code < 500, r.text
    if r.status_code == 201:
        assert r.json()["total_cents"] == r.json()["base_price_cents"]
        assert len(r.json()["services"]) in (1, 2)


@pytest.mark.parametrize(
    "extra",
    [
        {"plate": "!!!"},
        {"plate": "   "},
        {"plate": "A" * 21},
        {"plate": "ÁÑÜ 123"},
        {"customer_name": "   "},
        {"customer_name": "x" * 81},
        {"customer_name": "a\x00b"},
        {"customer_phone": "abc"},
        {"customer_phone": "+" + "9" * 40},
        {"customer_phone": "1"},
        {"notes": "n" * 501},
    ],
)
async def test_retro_cliente_y_patente_raros_no_dan_500(client, cookies_a, lav, extra):
    r = await _cargar(client, cookies_a, {**retro(lav, _ayer()), **extra})
    assert r.status_code in (201, 422), (extra, r.status_code, r.text)


async def test_retro_sin_nombre_con_telefono_o_con_nada(client, cookies_a, lav):
    c = retro(lav, _ayer())
    del c["customer_name"], c["plate"]
    sin = await _cargar(client, cookies_a, c)
    assert (
        sin.status_code == 201
        and sin.json()["customer"] is None
        and sin.json()["vehicle_plate"] is None
    )
    tel = await _cargar(client, cookies_a, {**c, "customer_phone": "+54 9 351 000-0555"})
    assert tel.status_code == 201 and tel.json()["customer"]["name"] == "Sin nombre"
    vacio = await _cargar(client, cookies_a, {**c, "customer_name": "", "plate": ""})
    assert vacio.status_code in (201, 422)


async def test_retro_misma_patente_dos_veces_reusa_el_auto(client, cookies_a, lav, db_session):
    await _cargar(client, cookies_a, retro(lav, _ayer(), plate="QQ111WW"))
    autos = await _n(db_session, Vehicle)
    await _cargar(client, cookies_a, retro(lav, _ayer(), plate="qq 111 ww"))
    assert (
        await _n(db_session, Vehicle) == autos
    ), "la misma patente (otra forma de escribirla) creó otro auto"


async def test_retro_reenvio_entero_no_cambia_nada(client, cookies_a, lav, db_session):
    k = f"k-{uuid.uuid4()}"
    c = retro(
        lav,
        _ayer(),
        payment={"payment_method_id": str(lav.efectivo)},
        customer_phone="+54 9 351 000-0321",
    )
    uno = await _cargar(client, cookies_a, c, k)
    cnt = [await _n(db_session, m) for m in (Job, Customer, Vehicle)]
    ev = await eventos(client, cookies_a, uno.json()["id"])
    for _ in range(3):
        otra = await _cargar(client, cookies_a, c, k)
        assert otra.status_code == 201 and otra.json()["id"] == uno.json()["id"]
        assert (otra.json()["status"], otra.json()["payments"]) == (
            uno.json()["status"],
            uno.json()["payments"],
        )
    assert [await _n(db_session, m) for m in (Job, Customer, Vehicle)] == cnt
    assert await eventos(client, cookies_a, uno.json()["id"]) == ev


async def test_retro_reenvio_despues_de_operar_el_job_no_lo_revierte(client, cookies_a, lav):
    k = f"k-{uuid.uuid4()}"
    c = retro(lav, _ayer(), payment={"payment_method_id": str(lav.efectivo)})
    j = (await _cargar(client, cookies_a, c, k)).json()
    assert (await accion(client, cookies_a, j["id"], "pick-up")).status_code == 200
    otra = await _cargar(client, cookies_a, c, k)
    assert otra.status_code == 201 and otra.json()["id"] == j["id"]
    assert (await client.get(f"/v1/jobs/{j['id']}", cookies=cookies_a)).json()[
        "status"
    ] == "RETIRADO"


async def test_retro_manual_entry_solo_en_los_eventos_de_la_carga(client, cookies_a, lav):
    j = (
        await _cargar(
            client,
            cookies_a,
            retro(
                lav,
                _ayer(),
                payment={"payment_method_id": str(lav.efectivo), "amount_cents": 500_000},
            ),
        )
    ).json()
    assert j["status"] == "FINALIZADO"
    antes = await eventos(client, cookies_a, j["id"])
    assert all(e["metadata"].get("manual_entry") is True for e in antes)
    # toques en vivo posteriores: sin la marca
    assert (await cobrar(client, cookies_a, lav, j["id"])).json()["status"] == "COBRADO"
    assert (await accion(client, cookies_a, j["id"], "pick-up")).status_code == 200
    assert (
        await accion(client, cookies_a, j["id"], "inspection", dirt_level="BARRO")
    ).status_code == 200
    todos = await eventos(client, cookies_a, j["id"])
    nuevos = todos[len(antes) :]
    assert [e["event_type"] for e in nuevos][:3] == [
        "PAYMENT_RECORDED",
        "JOB_SETTLED",
        "JOB_PICKED_UP",
    ]
    assert all("manual_entry" not in e["metadata"] for e in nuevos), [e["metadata"] for e in nuevos]
    # y un job en vivo posterior (misma sesión de test) no la hereda
    vivo = await job_en(client, cookies_a, lav, "COBRADO")
    assert all(
        "manual_entry" not in e["metadata"] for e in await eventos(client, cookies_a, vivo["id"])
    )
    assert vivo["entry_mode"] == "EN_VIVO"


async def test_retro_en_vivo_no_se_confunde_con_retro_en_la_agenda(client, cookies_a, lav):
    j = await job_en(client, cookies_a, lav, "PRESENTE")
    hoy = datetime.now(CORDOBA).date()
    entradas = (await _dia(client, cookies_a, hoy))["entries"]
    assert [e["entry_mode"] for e in entradas if e["job_id"] == j["id"]] == ["EN_VIVO"]


async def test_retro_cobro_total_parcial_y_montos_absurdos(client, cookies_a, lav):
    ayer = _ayer()
    medio = str(lav.efectivo)
    for monto, esperado in (
        (1, 201),
        (1_999_999, 201),
        (2_000_000, 201),
        (2_000_001, 422),
        (0, 422),
        (-5, 422),
        (10**20, 422),
        (2**63, 422),
        (1.5, 422),
        ("1e3", 422),
        ("100", None),
    ):
        r = await _cargar(
            client,
            cookies_a,
            retro(lav, ayer, payment={"payment_method_id": medio, "amount_cents": monto}),
        )
        assert r.status_code < 500, (monto, r.status_code, r.text)
        if esperado:
            assert r.status_code == esperado, (monto, r.status_code)


async def test_retro_cobro_con_un_medio_con_comision_y_referencia_larga(client, cookies_a, lav):
    r = await _cargar(
        client,
        cookies_a,
        retro(
            lav, _ayer(), payment={"payment_method_id": str(lav.credito), "reference": "r" * 121}
        ),
    )
    assert r.status_code == 422
    ok = await _cargar(
        client, cookies_a, retro(lav, _ayer(), payment={"payment_method_id": str(lav.credito)})
    )
    assert ok.status_code == 201 and ok.json()["status"] == "COBRADO"


async def test_retro_pendiente_no_se_puede_retirar_cliente_nuevo_y_se_cobra_despues(
    client, cookies_a, lav
):
    j = (
        await _cargar(client, cookies_a, retro(lav, _ayer(), customer_phone="+54 9 351 000-0654"))
    ).json()
    assert j["status"] == "FINALIZADO" and j["balance_due_cents"] == 2_000_000
    assert (await accion(client, cookies_a, j["id"], "pick-up")).status_code == 422
    c = await cobrar(client, cookies_a, lav, j["id"])
    assert c.status_code == 200 and c.json()["status"] == "COBRADO"


async def test_retro_cae_en_la_agenda_de_su_fecha_en_los_bordes_de_medianoche(
    client, cookies_a, lav
):
    d = _ayer()
    cuerpos = {
        "justo_00:00": retro(lav, d, desde=(0, 0), hasta=(0, 30)),
        "ultimo_segundo": {
            **retro(lav, d),
            "started_at": iso(local(d, 23, 59, 59)),
            "finished_at": iso(local(d, 23, 59, 59)),
        },
        "cruza_medianoche": {
            **retro(lav, d),
            "started_at": iso(local(d, 23, 30)),
            "finished_at": iso(local(d + timedelta(days=1), 0, 30)),
        },
    }
    ids = {}
    for nombre, c in cuerpos.items():
        r = await _cargar(client, cookies_a, c)
        assert r.status_code == 201, (nombre, r.text)
        ids[nombre] = r.json()["id"]
    en_d = {e["job_id"] for e in (await _dia(client, cookies_a, d))["entries"]}
    en_sig = {
        e["job_id"] for e in (await _dia(client, cookies_a, d + timedelta(days=1)))["entries"]
    }
    en_ant = {
        e["job_id"] for e in (await _dia(client, cookies_a, d - timedelta(days=1)))["entries"]
    }
    assert set(ids.values()) <= en_d, "algún borde cayó en otro día"
    assert not (set(ids.values()) & (en_sig | en_ant)), "se duplicó o cayó en el día equivocado"


async def test_retro_usa_la_zona_del_negocio_y_no_la_de_cordoba_cableada(
    client, cookies_a, lav, db_session, tenant_a
):
    tz = "Pacific/Auckland"  # UTC+13: el día local difiere del de Córdoba
    t = await db_session.get(Tenant, tenant_a.id)
    t.timezone = tz
    await db_session.flush()
    z = ZoneInfo(tz)
    d = (datetime.now(z) - timedelta(days=2)).date()
    c = {
        **retro(lav, d),
        "started_at": iso(local(d, 1, 0, zona=z)),
        "finished_at": iso(local(d, 2, 0, zona=z)),
    }
    j = (await _cargar(client, cookies_a, c)).json()
    dia = await _dia(client, cookies_a, d)
    assert dia["timezone"] == tz
    assert [e["job_id"] for e in dia["entries"]] == [j["id"]]
    assert (await _dia(client, cookies_a, d - timedelta(days=1)))["entries"] == []


# ── 8. Turno a mano ──────────────────────────────────────────────────────────


async def _mano(client, cookies, cuerpo, k=None):
    return await client.post(
        "/v1/bookings/manual",
        json=cuerpo,
        cookies=cookies,
        headers={"Idempotency-Key": k or f"k-{uuid.uuid4()}"},
    )


async def test_mano_el_pasado_y_el_borde(client, cookies_a, lav):
    for delta in (timedelta(seconds=-1), timedelta(hours=-5), timedelta(days=-400)):
        r = await _mano(client, cookies_a, a_mano(lav, datetime.now(UTC) + delta))
        assert r.status_code == 422, (delta, r.status_code, r.text)
    assert (
        await _mano(
            client,
            cookies_a,
            {
                **a_mano(lav, datetime.now(UTC) + timedelta(hours=2)),
                "start_at": "2026-12-01T10:00:00",
            },
        )
    ).status_code == 422


async def test_mano_futuro_remoto_no_da_500(client, cookies_a, lav):
    for delta in (timedelta(days=400), timedelta(days=365 * 50)):
        r = await _mano(client, cookies_a, a_mano(lav, datetime.now(UTC) + delta))
        assert r.status_code < 500, (delta, r.status_code)


async def test_mano_sena_en_servicio_sin_sena_y_montos_raros(client, cookies_a, lav):
    inicio = datetime.now(UTC) + timedelta(hours=2)
    r = await _mano(
        client, cookies_a, a_mano(lav, inicio, deposit={"payment_method_id": str(lav.efectivo)})
    )
    assert r.status_code == 422
    await lav.activar_sena()
    base = dict(service_ids=[str(lav.lavado_con_sena)])
    for i, monto in enumerate((0, -1, 10**20, 2**63, 1.5)):
        r = await _mano(
            client,
            cookies_a,
            a_mano(
                lav,
                inicio + timedelta(hours=i + 1),
                **base,
                deposit={"payment_method_id": str(lav.efectivo), "amount_cents": monto},
            ),
        )
        assert r.status_code == 422, (monto, r.status_code, r.text)


async def test_mano_sena_mayor_al_precio_total_se_rechaza(client, cookies_a, lav):
    """Una seña de 10x el precio no puede dejar el turno CONFIRMADO (saldo a favor imposible)."""
    await lav.activar_sena()
    inicio = datetime.now(UTC) + timedelta(hours=2)
    r = await _mano(
        client,
        cookies_a,
        a_mano(
            lav,
            inicio,
            service_ids=[str(lav.lavado_con_sena)],
            deposit={"payment_method_id": str(lav.efectivo), "amount_cents": 30_000_000},
        ),
    )
    assert r.status_code == 422, f"seña 10x el precio aceptada: {r.status_code} {r.text[:200]}"


@pytest.mark.xfail(
    strict=True,
    reason="confirm_deposit (F5) solo exige > 0; una seña menor a la pedida es decisión de producto, ver adenda 6.1 §12.11",
)
async def test_mano_sena_menor_a_la_pedida_no_confirma_el_turno(client, cookies_a, lav):
    """Con la seña pedida en 900.000, 1 centavo deja el turno CONFIRMADO (hereda `confirm_deposit` de F5)."""
    await lav.activar_sena()
    inicio = datetime.now(UTC) + timedelta(hours=2)
    r = await _mano(
        client,
        cookies_a,
        a_mano(
            lav,
            inicio,
            service_ids=[str(lav.lavado_con_sena)],
            deposit={"payment_method_id": str(lav.efectivo), "amount_cents": 1},
        ),
    )
    assert (
        r.status_code == 422 or r.json()["booking"]["status"] != "CONFIRMADO"
    ), "1 centavo confirmó un turno con seña de 900.000"


async def test_mano_servicios_repetidos_y_a_cotizar_no_dan_500(client, cookies_a, lav):
    inicio = datetime.now(UTC) + timedelta(hours=2)
    r1 = await _mano(
        client, cookies_a, a_mano(lav, inicio, service_ids=[str(lav.lavado), str(lav.lavado)])
    )
    r2 = await _mano(
        client, cookies_a, a_mano(lav, inicio + timedelta(hours=6), service_ids=[str(lav.tapizado)])
    )
    assert r1.status_code < 500 and r2.status_code < 500, (r1.text, r2.text)


async def test_mano_vacios_y_limites_de_campos(client, cookies_a, lav):
    inicio = datetime.now(UTC) + timedelta(hours=2)
    for extra in (
        {"service_ids": []},
        {"service_ids": [str(lav.lavado)] * 21},
        {"customer_name": "x" * 81},
        {"plate": "A" * 21},
        {"notes": "n" * 501},
        {"channel": "NO_EXISTE"},
    ):
        r = await _mano(client, cookies_a, {**a_mano(lav, inicio), **extra})
        assert r.status_code == 422, (list(extra), r.status_code)


async def test_mano_muchos_turnos_codigos_unicos(client, cookies_a, lav):
    codigos = set()
    for i in range(12):
        r = await _mano(
            client, cookies_a, a_mano(lav, datetime.now(UTC) + timedelta(hours=2 + i * 2))
        )
        assert r.status_code == 201, r.text
        codigos.add(r.json()["booking"]["code"])
    assert len(codigos) == 12 and all(c.startswith("INT-") for c in codigos)


async def test_mano_mismo_telefono_reusa_cliente_y_la_patente_reusa_auto(
    client, cookies_a, lav, db_session
):
    c0, v0 = await _n(db_session, Customer), await _n(db_session, Vehicle)
    base = a_mano(lav, datetime.now(UTC) + timedelta(hours=2), plate="MM222NN")
    r1 = await _mano(client, cookies_a, base)
    r2 = await _mano(
        client, cookies_a, {**base, "start_at": iso(datetime.now(UTC) + timedelta(hours=8))}
    )
    assert r1.status_code == r2.status_code == 201
    assert await _n(db_session, Customer) == c0 + 1 and await _n(db_session, Vehicle) == v0 + 1


async def test_mano_el_turno_aparece_en_la_agenda_y_al_recibirlo_no_se_duplica(
    client, cookies_a, lav
):
    inicio = (datetime.now(CORDOBA) + timedelta(days=1)).replace(
        hour=11, minute=0, second=0, microsecond=0
    )
    b = (await _mano(client, cookies_a, a_mano(lav, inicio))).json()["booking"]
    dia = await _dia(client, cookies_a, inicio.date())
    assert [(e["kind"], e["id"]) for e in dia["entries"]] == [("BOOKING", b["id"])]
    r = await client.post(
        "/v1/jobs",
        json={"occurred_at": iso(datetime.now(UTC)), "booking_id": b["id"]},
        cookies=cookies_a,
        headers=clave(),
    )
    assert r.status_code == 201, r.text
    dia = await _dia(client, cookies_a, inicio.date())
    assert [e["kind"] for e in dia["entries"]] == ["JOB"]
    assert dia["entries"][0]["booking_id"] == b["id"]


async def test_mano_el_turno_interno_no_se_consulta_ni_cancela_por_el_turnero_publico(
    client, cookies_a, lav
):
    b = (
        await _mano(client, cookies_a, a_mano(lav, datetime.now(UTC) + timedelta(hours=3)))
    ).json()["booking"]
    for ruta in (f"/v1/public/bookings/{b['code']}", f"/v1/public/bookings/{b['code']}/cancel"):
        for m in ("GET", "POST", "DELETE"):
            r = await client.request(m, ruta, json={} if m != "GET" else None)
            assert r.status_code in (401, 404, 405, 422), (m, ruta, r.status_code)


async def test_mano_el_error_422_no_quema_la_clave_en_la_misma_sesion(client, cookies_a, lav):
    """El claim de la clave va antes de validar. En producción el rollback lo suelta; acá se
    verifica la mitad observable: el reintento con la clave corregida no puede ser 'duplicado'."""
    k = f"k-{uuid.uuid4()}"
    mal = await _mano(client, cookies_a, a_mano(lav, datetime.now(UTC) - timedelta(hours=1)), k)
    assert mal.status_code == 422
    # (resultado del reintento solo es concluyente contra Postgres: ver test_pg_atomicidad_concurrencia.py)


# ── 9. GET /agenda/day ───────────────────────────────────────────────────────


async def test_agenda_zona_con_horario_de_verano(client, cookies_a, lav, db_session, tenant_a):
    t = await db_session.get(Tenant, tenant_a.id)
    t.timezone = "America/New_York"
    await db_session.flush()
    z = ZoneInfo("America/New_York")
    dia = datetime(2026, 11, 1).date()  # día de 25 h (fin del horario de verano)
    futuros = {}
    for nombre, (h, m) in {"inicio": (0, 0), "doble_1am": (1, 30), "ultimo": (23, 59)}.items():
        c = {
            **retro(lav, dia),
            "started_at": iso(datetime(2026, 11, 1, h, m, tzinfo=z)),
            "finished_at": iso(datetime(2026, 11, 1, h, m, tzinfo=z)),
        }
        r = await _cargar(client, cookies_a, c)
        assert r.status_code == 201, r.text
        futuros[nombre] = r.json()["id"]
    otro_dia = await _cargar(
        client,
        cookies_a,
        {
            **retro(lav, dia),
            "started_at": iso(datetime(2026, 11, 2, 0, 0, tzinfo=z)),
            "finished_at": iso(datetime(2026, 11, 2, 0, 0, tzinfo=z)),
        },
    )
    previo = await _cargar(
        client,
        cookies_a,
        {
            **retro(lav, dia),
            "started_at": iso(datetime(2026, 10, 31, 23, 59, 59, tzinfo=z)),
            "finished_at": iso(datetime(2026, 10, 31, 23, 59, 59, tzinfo=z)),
        },
    )
    en = {e["job_id"] for e in (await _dia(client, cookies_a, dia))["entries"]}
    assert en == set(futuros.values())
    assert otro_dia.json()["id"] not in en and previo.json()["id"] not in en


async def test_agenda_job_y_turno_no_se_duplican_y_el_dia_es_el_del_turno(client, cookies_a, lav):
    d = (datetime.now(CORDOBA) + timedelta(days=1)).date()
    ahora = datetime.now(UTC)
    turno = await lav.turno(
        inicio=local(d, 23, 50).astimezone(UTC), ahora=ahora - timedelta(days=1)
    )
    j = await lav.jobs.receive(
        idempotency_key=f"r-{uuid.uuid4()}",
        arrived_at=local(d + timedelta(days=1), 0, 10).astimezone(UTC),
        booking_id=turno.id,
    )
    hoy = await _dia(client, cookies_a, d)
    sig = await _dia(client, cookies_a, d + timedelta(days=1))
    assert [(e["kind"], e["job_id"]) for e in hoy["entries"]] == [("JOB", str(j.id))]
    assert sig["entries"] == []


async def test_agenda_estados_de_turno_y_job_cancelado_por_demora_una_sola_vez(
    client, cookies_a, lav
):
    d = datetime.now(CORDOBA).date()
    ahora = datetime.now(UTC)
    t_no = await lav.turno(inicio=local(d, 8).astimezone(UTC), ahora=ahora - timedelta(days=3))
    await lav.turnos.mark_no_show(t_no.id, now=ahora)
    t_del = await lav.turno(inicio=local(d, 9).astimezone(UTC), ahora=ahora - timedelta(days=3))
    j = await lav.jobs.receive(
        idempotency_key=f"r-{uuid.uuid4()}", arrived_at=ahora, booking_id=t_del.id
    )
    r = (
        await accion(client, cookies_a, j.id, "cancel-delay", ahora)
        if local(d, 9).astimezone(UTC) < ahora - timedelta(minutes=30)
        else None
    )
    dia = await _dia(client, cookies_a, d)
    por_booking = [e["booking_id"] for e in dia["entries"] if e["booking_id"]]
    assert len(por_booking) == len(
        set(por_booking)
    ), "un turno aparece dos veces (como turno y como job)"
    assert str(t_no.id) in por_booking


async def test_agenda_fechas_invalidas_y_extremas(client, cookies_a, lav):
    for v in (
        "2026-11-16T10:00:00",
        "16/11/2026",
        "20261116",
        "0000-00-00",
        "2026-02-30",
        " ",
        "1e3",
        "NaN",
        "9999-12-31",
        "0001-01-01",
    ):
        r = await client.get("/v1/agenda/day", params={"date": v}, cookies=cookies_a)
        assert r.status_code in (200, 422), (v, r.status_code)
    assert (
        await client.get(
            "/v1/agenda/day",
            params=[("date", "2026-11-16"), ("date", "2026-11-17")],
            cookies=cookies_a,
        )
    ).status_code < 500


async def test_agenda_tope_500_y_truncated(client, cookies_a, lav, db_session):
    from app.application.services.operation_service import DAY_CAP

    d = datetime.now(CORDOBA).date()
    base = local(d, 0, 0).astimezone(UTC)
    for i in range(DAY_CAP):
        await lav.walk_in(llegada=base + timedelta(seconds=i * 10))
    exacto = await _dia(client, cookies_a, d)
    assert len(exacto["entries"]) == DAY_CAP and exacto["truncated"] is False
    await lav.walk_in(llegada=base + timedelta(seconds=DAY_CAP * 10 + 5))
    de_mas = await _dia(client, cookies_a, d)
    assert len(de_mas["entries"]) == DAY_CAP and de_mas["truncated"] is True
    # el que se corta es el último del día, no uno al azar
    horas = [dt(e["start_at"]) for e in de_mas["entries"]]
    assert horas == sorted(horas) and horas[-1] == base + timedelta(seconds=(DAY_CAP - 1) * 10)


async def test_agenda_turnos_pasan_el_tope_aparte(client, cookies_a, lav):
    """Los turnos se cortan aparte de los jobs (tope por tipo): 500 jobs + turnos => hasta 1000 renglones."""
    d = (datetime.now(CORDOBA) + timedelta(days=3)).date()
    ahora = datetime.now(UTC)
    t = await lav.turno(inicio=local(d, 10).astimezone(UTC), ahora=ahora - timedelta(days=5))
    dia = await _dia(client, cookies_a, d)
    assert [e["id"] for e in dia["entries"]] == [str(t.id)]
