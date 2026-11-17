# ruff: noqa: E501, F841, E731, C408
"""T3 F6 / 6.1 — puntos 1 y 2: doble toque, clave reusada, estado superado."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.persistence.models import Customer, Job, Payment, Vehicle
from app.tests.adversarial_f6._h import (
    a_mano,
    accion,
    clave,
    cobrar,
    codigo,
    eventos,
    iso,
    job_en,
    retro,
)


async def _n(db_session, modelo) -> int:
    return (await db_session.execute(select(func.count()).select_from(modelo))).scalar_one()


# ── 1. Misma clave, mismo resultado, sin efectos duplicados ──────────────────


@pytest.mark.parametrize(
    ("accion_", "previo", "evento"),
    [
        ("start", "PRESENTE", "JOB_STARTED"),
        ("finish", "EN_PROCESO", "JOB_FINISHED"),
        ("pick-up", "COBRADO", "JOB_PICKED_UP"),
    ],
)
async def test_doble_toque_misma_clave_no_duplica(
    client, cookies_a, lav, db_session, accion_, previo, evento
):
    job = await job_en(client, cookies_a, lav, previo)
    k = f"k-{uuid.uuid4()}"
    uno = await accion(client, cookies_a, job["id"], accion_, k=k)
    dos = await accion(client, cookies_a, job["id"], accion_, k=k)
    tres = await accion(client, cookies_a, job["id"], accion_, k=k)
    assert uno.status_code == dos.status_code == tres.status_code == 200, (uno.text, dos.text)
    assert uno.json() == dos.json() == tres.json()
    assert [e["event_type"] for e in await eventos(client, cookies_a, job["id"])].count(evento) == 1


async def test_reenvio_de_start_tras_avanzar_el_estado_es_200_sin_efectos(client, cookies_a, lav):
    """El toque viejo de la cola llega cuando el job ya avanzó: no rompe ni retrocede nada."""
    job = await job_en(client, cookies_a, lav, "PRESENTE")
    k = f"k-{uuid.uuid4()}"
    assert (await accion(client, cookies_a, job["id"], "start", k=k)).status_code == 200
    await accion(client, cookies_a, job["id"], "finish")
    antes = await eventos(client, cookies_a, job["id"])
    viejo = await accion(client, cookies_a, job["id"], "start", k=k)
    assert viejo.status_code == 200, viejo.text
    assert viejo.json()["status"] == "FINALIZADO"
    assert await eventos(client, cookies_a, job["id"]) == antes


async def test_cobro_sin_importe_doble_toque_misma_clave(client, cookies_a, lav, db_session):
    """Cobrar el saldo (sin `amount_cents`): el reenvío calcula el saldo DESPUÉS de cobrar (0)."""
    job = await job_en(client, cookies_a, lav, "FINALIZADO")
    k = f"k-{uuid.uuid4()}"
    uno = await cobrar(client, cookies_a, lav, job["id"], k=k)
    dos = await cobrar(client, cookies_a, lav, job["id"], k=k)
    assert uno.status_code == 200, uno.text
    assert dos.status_code == 200, dos.text
    assert dos.json()["payments"] == uno.json()["payments"]
    assert await _n(db_session, Payment) == 1


async def test_cobro_sin_importe_reenviado_con_el_job_ya_retirado(
    client, cookies_a, lav, db_session
):
    job = await job_en(client, cookies_a, lav, "FINALIZADO")
    k = f"k-{uuid.uuid4()}"
    assert (await cobrar(client, cookies_a, lav, job["id"], k=k)).status_code == 200
    assert (await accion(client, cookies_a, job["id"], "pick-up")).status_code == 200
    dos = await cobrar(client, cookies_a, lav, job["id"], k=k)
    assert dos.status_code == 200, dos.text
    assert await _n(db_session, Payment) == 1


async def test_cobro_parcial_con_importe_distinto_en_el_reenvio_devuelve_el_original(
    client, cookies_a, lav, db_session
):
    job = await job_en(client, cookies_a, lav, "FINALIZADO")
    k = f"k-{uuid.uuid4()}"
    uno = await cobrar(client, cookies_a, lav, job["id"], 500_000, k=k)
    dos = await cobrar(client, cookies_a, lav, job["id"], 700_000, k=k)
    assert uno.status_code == dos.status_code == 200
    assert [p["amount_cents"] for p in dos.json()["payments"]] == [500_000]
    assert await _n(db_session, Payment) == 1


async def test_anulacion_doble_toque_misma_clave(client, cookies_a, lav):
    job = await job_en(client, cookies_a, lav, "FINALIZADO")
    pago = (await cobrar(client, cookies_a, lav, job["id"])).json()["payments"][0]["id"]
    k = f"k-{uuid.uuid4()}"
    cuerpo = {"occurred_at": iso(datetime.now(UTC)), "reason": "Error"}
    uno = await client.post(
        f"/v1/jobs/{job['id']}/payments/{pago}/void",
        json=cuerpo,
        cookies=cookies_a,
        headers={"Idempotency-Key": k},
    )
    dos = await client.post(
        f"/v1/jobs/{job['id']}/payments/{pago}/void",
        json=cuerpo,
        cookies=cookies_a,
        headers={"Idempotency-Key": k},
    )
    assert uno.status_code == dos.status_code == 200, (uno.text, dos.text)
    tipos = [e["event_type"] for e in await eventos(client, cookies_a, job["id"])]
    assert tipos.count("PAYMENT_VOIDED") == 1


async def test_anular_dos_veces_con_claves_distintas_no_rompe(client, cookies_a, lav):
    job = await job_en(client, cookies_a, lav, "FINALIZADO")
    pago = (await cobrar(client, cookies_a, lav, job["id"])).json()["payments"][0]["id"]
    cuerpo = {"occurred_at": iso(datetime.now(UTC)), "reason": "Error"}
    await client.post(
        f"/v1/jobs/{job['id']}/payments/{pago}/void",
        json=cuerpo,
        cookies=cookies_a,
        headers=clave(),
    )
    otra = await client.post(
        f"/v1/jobs/{job['id']}/payments/{pago}/void",
        json=cuerpo,
        cookies=cookies_a,
        headers=clave(),
    )
    assert otra.status_code in (404, 409, 422), otra.text  # nunca 200 doble ni 500
    tipos = [e["event_type"] for e in await eventos(client, cookies_a, job["id"])]
    assert tipos.count("PAYMENT_VOIDED") == 1


async def test_recibir_misma_clave_dos_veces_un_solo_job_un_solo_auto(
    client, cookies_a, lav, db_session
):
    k = f"k-{uuid.uuid4()}"
    cuerpo = {
        "occurred_at": iso(datetime.now(UTC)),
        "plate": "ZZ999TT",
        "vehicle_size_id": str(lav.auto),
        "service_ids": [str(lav.lavado)],
        "customer_name": "Nuevo Cliente",
        "customer_phone": "+54 9 351 000-0999",
    }
    jobs0, autos0, cli0 = (
        await _n(db_session, Job),
        await _n(db_session, Vehicle),
        await _n(db_session, Customer),
    )
    uno = await client.post(
        "/v1/jobs", json=cuerpo, cookies=cookies_a, headers={"Idempotency-Key": k}
    )
    dos = await client.post(
        "/v1/jobs", json=cuerpo, cookies=cookies_a, headers={"Idempotency-Key": k}
    )
    assert uno.status_code == dos.status_code == 201
    assert uno.json()["id"] == dos.json()["id"]
    assert await _n(db_session, Job) == jobs0 + 1
    assert await _n(db_session, Vehicle) == autos0 + 1
    assert await _n(db_session, Customer) == cli0 + 1


async def test_recibir_misma_clave_con_otro_cuerpo_no_crea_otro_cliente_ni_auto(
    client, cookies_a, lav, db_session
):
    k = f"k-{uuid.uuid4()}"
    base = {
        "occurred_at": iso(datetime.now(UTC)),
        "vehicle_size_id": str(lav.auto),
        "service_ids": [str(lav.lavado)],
    }
    uno = await client.post(
        "/v1/jobs",
        json={**base, "plate": "AA111BB"},
        cookies=cookies_a,
        headers={"Idempotency-Key": k},
    )
    autos, cli = await _n(db_session, Vehicle), await _n(db_session, Customer)
    dos = await client.post(
        "/v1/jobs",
        json={
            **base,
            "plate": "CC222DD",
            "customer_name": "Otro",
            "customer_phone": "+54 9 351 000-0888",
        },
        cookies=cookies_a,
        headers={"Idempotency-Key": k},
    )
    assert dos.status_code == 201 and dos.json()["id"] == uno.json()["id"]
    assert (await _n(db_session, Vehicle), await _n(db_session, Customer)) == (autos, cli)


# ── Otra clave sobre estado superado ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("estado", "accion_"),
    [
        ("EN_PROCESO", "start"),
        ("FINALIZADO", "start"),
        ("FINALIZADO", "finish"),
        ("COBRADO", "finish"),
        ("RETIRADO", "pick-up"),
        ("RETIRADO", "start"),
        ("PRESENTE", "finish"),
        ("PRESENTE", "pick-up"),
        ("EN_PROCESO", "pick-up"),
    ],
)
async def test_otra_clave_sobre_estado_superado_es_409_y_no_cambia_nada(
    client, cookies_a, lav, estado, accion_
):
    job = await job_en(client, cookies_a, lav, estado)
    antes = await eventos(client, cookies_a, job["id"])
    r = await accion(client, cookies_a, job["id"], accion_)
    assert r.status_code == 409 and codigo(r) == "INVALID_TRANSITION", r.text
    despues = (await client.get(f"/v1/jobs/{job['id']}", cookies=cookies_a)).json()
    assert (despues["status"], despues["paid_cents"], despues["started_at"] is None) == (
        job["status"],
        job["paid_cents"],
        job["started_at"] is None,
    )
    assert await eventos(client, cookies_a, job["id"]) == antes


async def test_cobrar_de_mas_o_con_saldo_cero_no_es_exito_silencioso(
    client, cookies_a, lav, db_session
):
    job = await job_en(client, cookies_a, lav, "COBRADO")
    r = await cobrar(client, cookies_a, lav, job["id"], 1000)
    assert r.status_code in (409, 422), r.text
    r2 = await cobrar(client, cookies_a, lav, job["id"])  # saldo 0 sin importe
    assert r2.status_code in (409, 422), r2.text
    assert await _n(db_session, Payment) == 1


@pytest.mark.parametrize("origen", ["start", "finish", "payment", "receive", "inspection"])
async def test_clave_de_una_operacion_usada_en_otra_es_conflict_y_no_muta(
    client, cookies_a, lav, origen
):
    k = f"k-{uuid.uuid4()}"
    if origen == "start":
        j = await job_en(client, cookies_a, lav, "PRESENTE")
        assert (await accion(client, cookies_a, j["id"], "start", k=k)).status_code == 200
    elif origen == "finish":
        j = await job_en(client, cookies_a, lav, "EN_PROCESO")
        assert (await accion(client, cookies_a, j["id"], "finish", k=k)).status_code == 200
    elif origen == "payment":
        j = await job_en(client, cookies_a, lav, "FINALIZADO")
        assert (await cobrar(client, cookies_a, lav, j["id"], 1000, k=k)).status_code == 200
    elif origen == "inspection":
        j = await job_en(client, cookies_a, lav, "PRESENTE")
        assert (await accion(client, cookies_a, j["id"], "inspection", k=k)).status_code == 200
    else:
        r = await client.post(
            "/v1/jobs",
            json={
                "occurred_at": iso(datetime.now(UTC)),
                "vehicle_size_id": str(lav.auto),
                "service_ids": [str(lav.lavado)],
            },
            cookies=cookies_a,
            headers={"Idempotency-Key": k},
        )
        assert r.status_code == 201
        j = r.json()
    otro = await job_en(client, cookies_a, lav, "PRESENTE")  # otro job en PRESENTE
    antes = (await client.get(f"/v1/jobs/{otro['id']}", cookies=cookies_a)).json()
    n_antes = len(await eventos(client, cookies_a, otro["id"]))
    for destino in ("start", "inspection", "cancel-delay"):
        r = await accion(client, cookies_a, otro["id"], destino, k=k)
        assert r.status_code == 409 and codigo(r) == "CONFLICT", (origen, destino, r.text)
        assert codigo(r) != "DUPLICATE_IDEMPOTENT"
    assert (await client.get(f"/v1/jobs/{otro['id']}", cookies=cookies_a)).json() == antes
    assert len(await eventos(client, cookies_a, otro["id"])) == n_antes


async def test_clave_de_cobro_usada_en_el_cobro_de_otro_job_es_conflict(
    client, cookies_a, lav, db_session
):
    a = await job_en(client, cookies_a, lav, "FINALIZADO")
    b = await job_en(client, cookies_a, lav, "FINALIZADO")
    k = f"k-{uuid.uuid4()}"
    assert (await cobrar(client, cookies_a, lav, a["id"], 1000, k=k)).status_code == 200
    r = await cobrar(client, cookies_a, lav, b["id"], 1000, k=k)
    assert r.status_code == 409 and codigo(r) == "CONFLICT", r.text
    assert await _n(db_session, Payment) == 1
    assert (await client.get(f"/v1/jobs/{b['id']}", cookies=cookies_a)).json()["paid_cents"] == 0


async def test_clave_de_start_usada_en_el_start_de_otro_job_es_conflict(client, cookies_a, lav):
    a = await job_en(client, cookies_a, lav, "PRESENTE")
    b = await job_en(client, cookies_a, lav, "PRESENTE")
    k = f"k-{uuid.uuid4()}"
    assert (await accion(client, cookies_a, a["id"], "start", k=k)).status_code == 200
    r = await accion(client, cookies_a, b["id"], "start", k=k)
    assert r.status_code == 409 and codigo(r) == "CONFLICT", r.text
    assert (await client.get(f"/v1/jobs/{b['id']}", cookies=cookies_a)).json()[
        "status"
    ] == "PRESENTE"


async def test_recibir_un_turno_con_la_clave_de_otro_turno_es_conflict(client, cookies_a, lav):
    ahora = datetime.now(UTC)
    t1 = await lav.turno(inicio=ahora + timedelta(hours=3), ahora=ahora - timedelta(days=1))
    t2 = await lav.turno(inicio=ahora + timedelta(hours=5), ahora=ahora - timedelta(days=1))
    k = f"k-{uuid.uuid4()}"
    r1 = await client.post(
        "/v1/jobs",
        json={"occurred_at": iso(ahora), "booking_id": str(t1.id)},
        cookies=cookies_a,
        headers={"Idempotency-Key": k},
    )
    assert r1.status_code == 201
    r2 = await client.post(
        "/v1/jobs",
        json={"occurred_at": iso(ahora), "booking_id": str(t2.id)},
        cookies=cookies_a,
        headers={"Idempotency-Key": k},
    )
    assert r2.status_code == 409 and codigo(r2) == "CONFLICT", (r2.status_code, r2.text)


async def test_clave_de_start_usada_para_recibir_es_conflict(client, cookies_a, lav):
    j = await job_en(client, cookies_a, lav, "PRESENTE")
    k = f"k-{uuid.uuid4()}"
    assert (await accion(client, cookies_a, j["id"], "start", k=k)).status_code == 200
    r = await client.post(
        "/v1/jobs",
        json={
            "occurred_at": iso(datetime.now(UTC)),
            "vehicle_size_id": str(lav.auto),
            "service_ids": [str(lav.lavado)],
        },
        cookies=cookies_a,
        headers={"Idempotency-Key": k},
    )
    assert r.status_code == 409 and codigo(r) == "CONFLICT", (r.status_code, r.text)


async def test_clave_de_la_carga_retrospectiva_usada_en_otra_operacion_es_conflict(
    client, cookies_a, lav
):
    ayer = (datetime.now(UTC) - timedelta(days=1)).date()
    k = f"k-{uuid.uuid4()}"
    r = await client.post(
        "/v1/jobs/retrospective",
        json=retro(lav, ayer),
        cookies=cookies_a,
        headers={"Idempotency-Key": k},
    )
    assert r.status_code == 201
    j = await job_en(client, cookies_a, lav, "PRESENTE")
    # derivadas de la retro: K:started pertenece a OTRO job
    otra = await accion(client, cookies_a, j["id"], "start", k=f"{k}:started")
    assert otra.status_code == 409 and codigo(otra) == "CONFLICT", otra.text
    assert (await client.get(f"/v1/jobs/{j['id']}", cookies=cookies_a)).json()[
        "status"
    ] == "PRESENTE"


async def test_clave_de_cobro_del_job_no_choca_con_la_clave_derivada_settled(
    client, cookies_a, lav
):
    """El cobro completo encadena `K:settled`; un cliente que mande esa clave en otra acción no la pisa."""
    j = await job_en(client, cookies_a, lav, "FINALIZADO")
    k = f"k-{uuid.uuid4()}"
    assert (await cobrar(client, cookies_a, lav, j["id"], k=k)).json()["status"] == "COBRADO"
    otro = await job_en(client, cookies_a, lav, "PRESENTE")
    r = await accion(client, cookies_a, otro["id"], "start", k=f"{k}:settled")
    assert r.status_code == 409 and codigo(r) == "CONFLICT", r.text


async def test_clave_ajena_de_otro_negocio_no_choca(client, cookies_a, cookies_b, lav, lav_b):
    """La clave es por negocio: la misma clave en B no es conflicto con A ni revela nada."""
    k = f"k-{uuid.uuid4()}"
    ja = await job_en(client, cookies_a, lav, "PRESENTE")
    jb = await job_en(client, cookies_b, lav_b, "PRESENTE")
    assert (await accion(client, cookies_a, ja["id"], "start", k=k)).status_code == 200
    r = await accion(client, cookies_b, jb["id"], "start", k=k)
    assert r.status_code == 200, r.text


async def test_clave_vacia_o_larga(client, cookies_a, lav):
    j = await job_en(client, cookies_a, lav, "PRESENTE")
    for k in ("", " ", "x" * 201):
        r = await accion(client, cookies_a, j["id"], "start", k=k)
        assert r.status_code == 422, (repr(k[:5]), r.status_code, r.text)
    assert (await client.get(f"/v1/jobs/{j['id']}", cookies=cookies_a)).json()[
        "status"
    ] == "PRESENTE"


async def test_turno_a_mano_reenvio_es_409_duplicado_y_no_crea_otro(
    client, cookies_a, lav, db_session
):
    from app.persistence.models import Booking

    k = f"k-{uuid.uuid4()}"
    cuerpo = a_mano(lav, datetime.now(UTC) + timedelta(hours=1))
    n0 = await _n(db_session, Booking)
    uno = await client.post(
        "/v1/bookings/manual", json=cuerpo, cookies=cookies_a, headers={"Idempotency-Key": k}
    )
    dos = await client.post(
        "/v1/bookings/manual", json=cuerpo, cookies=cookies_a, headers={"Idempotency-Key": k}
    )
    assert uno.status_code == 201 and dos.status_code == 409
    assert await _n(db_session, Booking) == n0 + 1
