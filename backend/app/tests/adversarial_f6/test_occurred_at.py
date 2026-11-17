# ruff: noqa: E501, F841, E731, C408
"""T3 F6 / 6.1 — puntos 3 y 4: occurred_at (futuro, sin zona, anterior, orden offline) y cancel-delay."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.tests.adversarial_f6._h import (
    CORDOBA,
    accion,
    clave,
    cobrar,
    codigo,
    dt,
    eventos,
    ingreso,
    iso,
    job_en,
)

# ── 3. occurred_at ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("accion_", ["start", "finish", "pick-up", "cancel-delay", "inspection"])
@pytest.mark.parametrize(
    "valor", ["2026-11-16T10:00:00", "2026-11-16 10:00:00", "ayer", 12, None, ""]
)
async def test_occurred_at_sin_zona_o_basura_es_422(client, cookies_a, lav, accion_, valor):
    job = await job_en(client, cookies_a, lav, "PRESENTE")
    r = await client.post(
        f"/v1/jobs/{job['id']}/{accion_}",
        json={"occurred_at": valor},
        cookies=cookies_a,
        headers=clave(),
    )
    # un entero se parsea como epoch UTC en pydantic lax: aceptarlo no es bug; basura sí es 422
    if valor == 12:
        assert r.status_code in (200, 409, 422)
    else:
        assert r.status_code == 422, (valor, r.status_code, r.text)


@pytest.mark.parametrize(
    "extremo", ["0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z", "0001-01-01T00:00:00+14:00"]
)
async def test_occurred_at_extremos_no_dan_500(client, cookies_a, lav, extremo):
    job = await job_en(client, cookies_a, lav, "PRESENTE")
    r = await client.post(
        f"/v1/jobs/{job['id']}/start",
        json={"occurred_at": extremo},
        cookies=cookies_a,
        headers=clave(),
    )
    assert r.status_code < 500, (extremo, r.status_code)


@pytest.mark.parametrize("accion_", ["start", "finish", "payment", "receive"])
async def test_occurred_at_del_futuro_lejano_se_acota_al_servidor(client, cookies_a, lav, accion_):
    """Un celular con el reloj mal (un año adelante) no debería envenenar la línea de tiempo ni
    mover un ingreso a la agenda de otro día. El contrato no lo dice: hallazgo de robustez."""
    futuro = datetime.now(UTC) + timedelta(days=365)
    if accion_ == "receive":
        r = await ingreso(client, cookies_a, lav, cuando=futuro)
    elif accion_ == "start":
        j = await job_en(client, cookies_a, lav, "PRESENTE")
        r = await accion(client, cookies_a, j["id"], "start", futuro)
    elif accion_ == "finish":
        j = await job_en(client, cookies_a, lav, "EN_PROCESO")
        r = await accion(client, cookies_a, j["id"], "finish", futuro)
    else:
        j = await job_en(client, cookies_a, lav, "FINALIZADO")
        r = await client.post(
            f"/v1/jobs/{j['id']}/payments",
            json={"occurred_at": iso(futuro), "payment_method_id": str(lav.efectivo)},
            cookies=cookies_a,
            headers=clave(),
        )
    # No se rechaza (perdería el toque en la cola offline): se acota a la hora del servidor.
    assert r.status_code < 300, (accion_, r.status_code, r.text)


async def test_walk_in_con_occurred_at_futuro_no_cae_en_la_agenda_de_hoy(client, cookies_a, lav):
    manana = datetime.now(UTC) + timedelta(days=2)
    r = await ingreso(client, cookies_a, lav, cuando=manana)
    hoy = datetime.now(CORDOBA).date().isoformat()
    if r.status_code == 201:
        dia = (await client.get("/v1/agenda/day", params={"date": hoy}, cookies=cookies_a)).json()
        assert any(
            e["job_id"] == r.json()["id"] for e in dia["entries"]
        ), "el job recién recibido desapareció de la agenda de hoy por un occurred_at del futuro"


@pytest.mark.xfail(
    strict=True,
    reason="X10 (ADR F3): se acepta occurred_at fuera de orden entre dispositivos; diferido, ver adenda 6.1 §12.11",
)
async def test_finalizar_antes_de_iniciar_se_rechaza(client, cookies_a, lav):
    """finished_at < started_at: línea de tiempo imposible."""
    job = await job_en(client, cookies_a, lav, "PRESENTE")
    t = datetime.now(UTC) - timedelta(minutes=30)
    assert (await accion(client, cookies_a, job["id"], "start", t)).status_code == 200
    r = await accion(client, cookies_a, job["id"], "finish", t - timedelta(minutes=20))
    assert r.status_code == 422, f"finish con hora anterior al start: {r.status_code}"


@pytest.mark.xfail(
    strict=True,
    reason="X10 (ADR F3): se acepta occurred_at fuera de orden entre dispositivos; diferido, ver adenda 6.1 §12.11",
)
async def test_iniciar_antes_de_la_llegada_se_rechaza(client, cookies_a, lav):
    t0 = datetime.now(UTC) - timedelta(minutes=30)
    job = (await ingreso(client, cookies_a, lav, cuando=t0)).json()
    r = await accion(client, cookies_a, job["id"], "start", t0 - timedelta(hours=3))
    assert r.status_code == 422, f"start 3 h antes de la llegada: {r.status_code}"


@pytest.mark.xfail(
    strict=True,
    reason="X10 (ADR F3): se acepta occurred_at fuera de orden entre dispositivos; diferido, ver adenda 6.1 §12.11",
)
async def test_cobrar_antes_de_finalizar_se_rechaza(client, cookies_a, lav):
    job = await job_en(client, cookies_a, lav, "PRESENTE")
    t = datetime.now(UTC) - timedelta(minutes=10)
    await accion(client, cookies_a, job["id"], "start", t)
    await accion(client, cookies_a, job["id"], "finish", t)
    r = await client.post(
        f"/v1/jobs/{job['id']}/payments",
        json={"occurred_at": iso(t - timedelta(hours=5)), "payment_method_id": str(lav.efectivo)},
        cookies=cookies_a,
        headers=clave(),
    )
    assert r.status_code == 422, f"cobro 5 h antes del fin: {r.status_code}"


async def test_sincronizacion_offline_fuera_de_orden_converge(client, cookies_a, lav):
    """La cola manda finish y payment ANTES que start (reconexión desordenada): 409, nada se
    pierde y al reintentar en orden la línea de tiempo queda con las horas reales."""
    t0 = datetime.now(UTC) - timedelta(minutes=60)
    job = (await ingreso(client, cookies_a, lav, cuando=t0)).json()
    k_start, k_fin, k_pago = (f"k-{uuid.uuid4()}" for _ in range(3))
    t_start, t_fin, t_pago = (
        t0 + timedelta(minutes=5),
        t0 + timedelta(minutes=45),
        t0 + timedelta(minutes=46),
    )
    r_fin = await accion(client, cookies_a, job["id"], "finish", t_fin, k=k_fin)
    assert r_fin.status_code == 409 and codigo(r_fin) == "INVALID_TRANSITION"
    assert (
        await accion(client, cookies_a, job["id"], "start", t_start, k=k_start)
    ).status_code == 200
    assert (await accion(client, cookies_a, job["id"], "finish", t_fin, k=k_fin)).status_code == 200
    assert (await cobrar(client, cookies_a, lav, job["id"], k=k_pago)).status_code == 200
    # y los reintentos tardíos de toda la cola ya aplicada: sin efectos
    for nombre, k, t in (("start", k_start, t_start), ("finish", k_fin, t_fin)):
        assert (await accion(client, cookies_a, job["id"], nombre, t, k=k)).status_code == 200
    ev = await eventos(client, cookies_a, job["id"])
    tipos = [e["event_type"] for e in ev]
    assert tipos == [
        "JOB_RECEIVED",
        "JOB_STARTED",
        "JOB_FINISHED",
        "PAYMENT_RECORDED",
        "JOB_SETTLED",
    ]
    horas = [dt(e["occurred_at"]) for e in ev]
    assert horas[:3] == [t0, t_start, t_fin]
    assert horas == sorted(horas)


async def test_los_eventos_vienen_en_el_orden_de_aceptacion_aunque_occurred_at_sea_anterior(
    client, cookies_a, lav
):
    """Orden de la línea de tiempo = orden de aceptación del servidor (docstring de la ruta)."""
    job = await job_en(client, cookies_a, lav, "PRESENTE")
    t = datetime.now(UTC) - timedelta(minutes=30)
    await accion(client, cookies_a, job["id"], "inspection", t + timedelta(minutes=10))
    await accion(client, cookies_a, job["id"], "start", t)  # tocado antes, llegó después
    tipos = [e["event_type"] for e in await eventos(client, cookies_a, job["id"])]
    assert tipos == ["JOB_RECEIVED", "INSPECTION_RECORDED", "JOB_STARTED"]


async def test_paginacion_de_eventos_invalida_es_422(client, cookies_a, lav):
    job = await job_en(client, cookies_a, lav, "EN_PROCESO")
    for params in ({"limit": 0}, {"limit": 100000}, {"offset": -1}, {"limit": "x"}):
        r = await client.get(f"/v1/jobs/{job['id']}/events", params=params, cookies=cookies_a)
        assert r.status_code == 422, (params, r.status_code)
    pag = (
        await client.get(
            f"/v1/jobs/{job['id']}/events", params={"limit": 1, "offset": 1}, cookies=cookies_a
        )
    ).json()
    assert len(pag["items"]) == 1 and pag["total"] == 2 and pag["has_more"] is False


# ── 4. cancel-delay ──────────────────────────────────────────────────────────


async def _turno_presente(client, cookies, lv, demora_min: int = 90):
    """Job recibido de un turno que empezó hace `demora_min` min. Devuelve (job, scheduled_at)."""
    ahora = datetime.now(UTC)
    inicio = (ahora - timedelta(minutes=demora_min)).replace(microsecond=0)
    turno = await lv.turno(inicio=inicio, ahora=ahora - timedelta(days=2))
    r = await client.post(
        "/v1/jobs",
        json={"occurred_at": iso(ahora), "booking_id": str(turno.id)},
        cookies=cookies,
        headers=clave(),
    )
    assert r.status_code == 201, r.text
    return r.json(), inicio


@pytest.mark.parametrize(
    ("offset", "ok"),
    [
        (timedelta(minutes=19, seconds=59), False),
        (timedelta(minutes=20), False),  # == tolerancia
        (timedelta(minutes=20, seconds=59), False),  # floor 20 no supera
        (timedelta(minutes=21), True),
        (timedelta(minutes=45), True),
        (timedelta(minutes=0), False),
        (timedelta(minutes=-30), False),  # antes del turno
    ],
)
async def test_cancel_delay_en_el_borde_de_la_tolerancia(client, cookies_a, lav, offset, ok):
    job, inicio = await _turno_presente(client, cookies_a, lav)
    r = await accion(client, cookies_a, job["id"], "cancel-delay", inicio + offset)
    if ok:
        assert r.status_code == 200 and r.json()["status"] == "CANCELADO_DEMORA", r.text
    else:
        assert r.status_code == 422 and codigo(r) == "VALIDATION_ERROR", r.text
        assert (await client.get(f"/v1/jobs/{job['id']}", cookies=cookies_a)).json()[
            "status"
        ] == "PRESENTE"


async def test_cancel_delay_coincide_con_cancel_delay_from(client, cookies_a, lav):
    job, inicio = await _turno_presente(client, cookies_a, lav)
    desde = dt(job["cancel_delay_from"])
    assert (
        await accion(client, cookies_a, job["id"], "cancel-delay", desde - timedelta(seconds=1))
    ).status_code == 422
    assert (await accion(client, cookies_a, job["id"], "cancel-delay", desde)).status_code == 200


@pytest.mark.parametrize("tolerancia", [1, 5, 60])
async def test_cancel_delay_respeta_la_tolerancia_configurada(client, cookies_a, lav, tolerancia):
    await lav.configurar(tolerance_min=tolerancia)
    job, inicio = await _turno_presente(client, cookies_a, lav, demora_min=120)
    assert job["tolerance_min"] == tolerancia
    assert (
        await accion(
            client, cookies_a, job["id"], "cancel-delay", inicio + timedelta(minutes=tolerancia)
        )
    ).status_code == 422
    assert (
        await accion(
            client, cookies_a, job["id"], "cancel-delay", inicio + timedelta(minutes=tolerancia + 1)
        )
    ).status_code == 200


async def test_cancel_delay_con_occurred_at_futuro_usa_el_reloj_del_servidor(
    client, cookies_a, lav
):
    job, inicio = await _turno_presente(
        client, cookies_a, lav, demora_min=10
    )  # el servidor ve 10 min
    for futuro in (timedelta(hours=1), timedelta(days=30), timedelta(minutes=11)):
        r = await accion(client, cookies_a, job["id"], "cancel-delay", datetime.now(UTC) + futuro)
        assert r.status_code == 422, (futuro, r.text)
    assert (await client.get(f"/v1/jobs/{job['id']}", cookies=cookies_a)).json()[
        "status"
    ] == "PRESENTE"


async def test_cancel_delay_con_occurred_at_futuro_pasada_la_tolerancia_guarda_la_hora_del_servidor(
    client, cookies_a, lav
):
    job, inicio = await _turno_presente(client, cookies_a, lav, demora_min=60)
    futuro = datetime.now(UTC) + timedelta(days=30)
    r = await accion(client, cookies_a, job["id"], "cancel-delay", futuro)
    assert r.status_code == 200, r.text
    ev = [
        e
        for e in await eventos(client, cookies_a, job["id"])
        if e["event_type"] == "JOB_CANCELLED_DELAY"
    ][0]
    assert dt(ev["occurred_at"]) < datetime.now(UTC) + timedelta(
        minutes=1
    ), "se guardó la hora del futuro"


async def test_cancel_delay_sobre_job_iniciado_es_409(client, cookies_a, lav):
    job, inicio = await _turno_presente(client, cookies_a, lav)
    await accion(client, cookies_a, job["id"], "start")
    r = await accion(client, cookies_a, job["id"], "cancel-delay")
    assert r.status_code == 409 and codigo(r) == "INVALID_TRANSITION"


async def test_cancel_delay_doble_toque_y_otra_clave(client, cookies_a, lav):
    job, inicio = await _turno_presente(client, cookies_a, lav)
    k = f"k-{uuid.uuid4()}"
    uno = await accion(client, cookies_a, job["id"], "cancel-delay", k=k, reason="No vino")
    dos = await accion(client, cookies_a, job["id"], "cancel-delay", k=k, reason="No vino")
    assert uno.status_code == dos.status_code == 200 and uno.json() == dos.json()
    otra = await accion(client, cookies_a, job["id"], "cancel-delay")
    assert otra.status_code == 409 and codigo(otra) == "INVALID_TRANSITION"
    tipos = [e["event_type"] for e in await eventos(client, cookies_a, job["id"])]
    assert tipos.count("JOB_CANCELLED_DELAY") == 1 and tipos.count("DEPOSIT_RETAINED") == 1


async def test_cancel_delay_motivo_largo_o_nulo(client, cookies_a, lav):
    job, _ = await _turno_presente(client, cookies_a, lav)
    r = await accion(client, cookies_a, job["id"], "cancel-delay", reason="x" * 100_000)
    assert r.status_code == 422
    r = await accion(client, cookies_a, job["id"], "cancel-delay", reason="a\x00b")
    assert r.status_code == 422


async def test_cobrar_o_anular_en_un_job_cancelado_por_demora_es_409(client, cookies_a, lav):
    job, _ = await _turno_presente(client, cookies_a, lav)
    assert (await accion(client, cookies_a, job["id"], "cancel-delay")).status_code == 200
    r = await cobrar(client, cookies_a, lav, job["id"], 1000)
    assert r.status_code == 409 and codigo(r) == "INVALID_TRANSITION", r.text
    for destino in ("start", "finish", "pick-up"):
        r = await accion(client, cookies_a, job["id"], destino)
        assert r.status_code == 409 and codigo(r) == "INVALID_TRANSITION", (destino, r.text)


async def test_cancel_delay_retiene_la_sena_cobrada(client, cookies_a, lav):
    await lav.activar_sena()
    ahora = datetime.now(UTC)
    inicio = ahora - timedelta(minutes=90)
    turno = await lav.turno(
        inicio=inicio, servicio=lav.lavado_con_sena, ahora=ahora - timedelta(days=2)
    )
    await lav.turnos.confirm_deposit(turno.id, lav.pago(900_000), now=ahora - timedelta(days=2))
    j = (
        await client.post(
            "/v1/jobs",
            json={"occurred_at": iso(ahora), "booking_id": str(turno.id)},
            cookies=cookies_a,
            headers=clave(),
        )
    ).json()
    assert j["paid_cents"] == 900_000
    r = await accion(client, cookies_a, j["id"], "cancel-delay")
    assert r.status_code == 200, r.text
    ev = {e["event_type"]: e for e in await eventos(client, cookies_a, j["id"])}
    assert ev["DEPOSIT_RETAINED"]["metadata"]["amount_cents"] == 900_000
