"""Agenda del día y operación del job (PR 6.1): `/agenda/day`, `/jobs`, `/bookings/manual` y
`/jobs/retrospective` — FASE-6-CONTRATO §3, §5 y A1 a A6, A9.

Datos sintéticos (`@example.com`, teléfonos `+54 9 351 000-xxxx`). El catálogo se arma por los
servicios sobre la misma sesión que usa el cliente HTTP. El choque del `EXCLUDE` contra Postgres y
el aislamiento con RLS se prueban en `tests/security/test_aislamiento_operacion_pg.py`.
"""

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

from app.domain.permissions import Permission
from app.domain.roles import Role
from app.tests.application._armado import PRECIO_FIJO, Lavadero, armar_catalogo
from app.tests.conftest import make_profile, make_user, session_cookies

CORDOBA = ZoneInfo("America/Argentina/Cordoba")
TELEFONO = "+54 9 351 000-0101"


def _clave() -> dict[str, str]:
    return {"Idempotency-Key": f"k-{uuid.uuid4()}"}


def _iso(instante: datetime) -> str:
    return instante.astimezone(UTC).isoformat()


def _dt(texto: str) -> datetime:
    """El instante de una respuesta: SQLite (suite rápida) pierde la zona al releer; era UTC."""
    instante = datetime.fromisoformat(texto)
    return instante if instante.tzinfo else instante.replace(tzinfo=UTC)


def _codigo(r) -> str:
    return r.json()["detail"]["code"]


@pytest_asyncio.fixture
async def lav(db_session, tenant_a, owner) -> Lavadero:
    lv = await armar_catalogo(db_session, tenant_a.id, owner.id)
    for dia in range(1, 8):
        await lv.catalogo.add_business_hours(dia, time(0, 0), time(23, 59))
    return lv


async def _empleado(db_session, tenant, nombre: str, permisos: set[Permission]):
    perfil = await make_profile(db_session, tenant, nombre, permisos)
    user = await make_user(
        db_session,
        tenant,
        Role.STAFF,
        f"{nombre.lower().replace(' ', '')}@example.com",
        profile=perfil,
    )
    return session_cookies(user)


async def _ingreso(client, cookies, lv: Lavadero, *, cuando: datetime | None = None, **extra):
    cuerpo = {
        "occurred_at": _iso(cuando or datetime.now(UTC)),
        "plate": "AB123CD",
        "vehicle_size_id": str(lv.auto),
        "service_ids": [str(lv.lavado)],
        "customer_name": "Cliente Demo",
        "customer_phone": TELEFONO,
        **extra,
    }
    return await client.post("/v1/jobs", json=cuerpo, cookies=cookies, headers=_clave())


async def _accion(client, cookies, job_id, accion: str, cuando: datetime | None = None, **extra):
    cuerpo = {"occurred_at": _iso(cuando or datetime.now(UTC)), **extra}
    return await client.post(
        f"/v1/jobs/{job_id}/{accion}", json=cuerpo, cookies=cookies, headers=_clave()
    )


# ── El lavado completo en vivo (A2) ──────────────────────────────────────────


async def test_un_lavado_completo_en_cuatro_toques_con_la_hora_de_cada_uno(client, cookies_a, lav):
    t0 = datetime.now(UTC) - timedelta(minutes=50)
    r = await _ingreso(client, cookies_a, lav, cuando=t0)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "PRESENTE" and job["entry_mode"] == "EN_VIVO"
    assert job["customer"]["name"] == "Cliente Demo"
    assert job["vehicle_plate"].replace(" ", "") == "AB123CD"
    assert job["total_cents"] == PRECIO_FIJO and job["balance_due_cents"] == PRECIO_FIJO
    assert job["services"][0]["name"] == "Lavado completo"

    t1, t2, t3 = t0 + timedelta(minutes=5), t0 + timedelta(minutes=45), t0 + timedelta(minutes=46)
    assert (await _accion(client, cookies_a, job["id"], "start", t1)).json()[
        "status"
    ] == "EN_PROCESO"
    fin = (await _accion(client, cookies_a, job["id"], "finish", t2)).json()
    assert fin["status"] == "FINALIZADO" and fin["finished_at"] is not None
    cobro = await client.post(
        f"/v1/jobs/{job['id']}/payments",
        json={"occurred_at": _iso(t3), "payment_method_id": str(lav.efectivo)},
        cookies=cookies_a,
        headers=_clave(),
    )
    assert cobro.status_code == 200, cobro.text
    cobrado = cobro.json()
    assert cobrado["status"] == "COBRADO"
    assert cobrado["paid_cents"] == PRECIO_FIJO and cobrado["balance_due_cents"] == 0
    assert [p["amount_cents"] for p in cobrado["payments"]] == [PRECIO_FIJO]

    eventos = (await client.get(f"/v1/jobs/{job['id']}/events", cookies=cookies_a)).json()
    tipos = [e["event_type"] for e in eventos["items"]]
    assert tipos == [
        "JOB_RECEIVED",
        "JOB_STARTED",
        "JOB_FINISHED",
        "PAYMENT_RECORDED",
        "JOB_SETTLED",
    ]
    horas = [_dt(e["occurred_at"]) for e in eventos["items"]]
    assert horas[:3] == [t0, t1, t2]  # la hora del toque, no la de la sincronización
    assert all("manual_entry" not in e["metadata"] for e in eventos["items"])
    assert eventos["total"] == 5 and eventos["has_more"] is False


async def test_un_doble_toque_con_la_misma_clave_devuelve_lo_mismo_y_no_duplica(
    client, cookies_a, lav
):
    job = (await _ingreso(client, cookies_a, lav)).json()
    cabecera = _clave()
    cuerpo = {"occurred_at": _iso(datetime.now(UTC))}
    uno = await client.post(
        f"/v1/jobs/{job['id']}/start", json=cuerpo, cookies=cookies_a, headers=cabecera
    )
    dos = await client.post(
        f"/v1/jobs/{job['id']}/start", json=cuerpo, cookies=cookies_a, headers=cabecera
    )
    assert uno.status_code == dos.status_code == 200
    assert uno.json() == dos.json()
    eventos = (await client.get(f"/v1/jobs/{job['id']}/events", cookies=cookies_a)).json()
    assert [e["event_type"] for e in eventos["items"]].count("JOB_STARTED") == 1


async def test_una_transicion_invalida_es_409_estable_y_no_cambia_el_estado(client, cookies_a, lav):
    job = (await _ingreso(client, cookies_a, lav)).json()
    await _accion(client, cookies_a, job["id"], "start")
    otra = await _accion(client, cookies_a, job["id"], "start")  # otra clave, estado superado
    assert otra.status_code == 409 and _codigo(otra) == "INVALID_TRANSITION"
    salteada = await _accion(client, cookies_a, job["id"], "pick-up")
    assert salteada.status_code == 409 and _codigo(salteada) == "INVALID_TRANSITION"
    assert (await client.get(f"/v1/jobs/{job['id']}", cookies=cookies_a)).json()["status"] == (
        "EN_PROCESO"
    )


async def test_una_clave_usada_por_otra_operacion_es_conflicto_no_exito_silencioso(
    client, cookies_a, lav
):
    job = (await _ingreso(client, cookies_a, lav)).json()
    cabecera = _clave()
    cuerpo = {"occurred_at": _iso(datetime.now(UTC))}
    await client.post(
        f"/v1/jobs/{job['id']}/start", json=cuerpo, cookies=cookies_a, headers=cabecera
    )
    r = await client.post(
        f"/v1/jobs/{job['id']}/finish", json=cuerpo, cookies=cookies_a, headers=cabecera
    )
    assert r.status_code == 409 and _codigo(r) == "CONFLICT"  # no DUPLICATE_IDEMPOTENT


@pytest.mark.parametrize(
    ("cuerpo", "cabeceras"),
    [
        ({}, True),  # sin occurred_at
        ({"occurred_at": "2026-11-16T10:00:00"}, True),  # sin zona
        ({"occurred_at": "2026-11-16T10:00:00Z"}, False),  # sin Idempotency-Key
    ],
)
async def test_toda_accion_exige_clave_y_hora_del_toque_con_zona(
    client, cookies_a, lav, cuerpo, cabeceras
):
    job = (await _ingreso(client, cookies_a, lav)).json()
    r = await client.post(
        f"/v1/jobs/{job['id']}/start",
        json=cuerpo,
        cookies=cookies_a,
        headers=_clave() if cabeceras else {},
    )
    assert r.status_code == 422, r.text


async def test_recibir_un_turno_lo_pasa_a_recibido_y_es_idempotente_por_turno(
    client, cookies_a, lav
):
    ahora = datetime.now(UTC)
    turno = await lav.turno(inicio=ahora + timedelta(hours=3), ahora=ahora - timedelta(days=1))
    cuerpo = {"occurred_at": _iso(ahora), "booking_id": str(turno.id)}
    r = await client.post("/v1/jobs", json=cuerpo, cookies=cookies_a, headers=_clave())
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "PRESENTE" and job["booking_id"] == str(turno.id)
    assert job["booking_code"] == turno.code and job["arrival_delay_min"] < 0  # llegó antes
    otra = await client.post("/v1/jobs", json=cuerpo, cookies=cookies_a, headers=_clave())
    assert otra.json()["id"] == job["id"]  # recibir es idempotente por turno


async def test_un_ingreso_sin_turno_exige_tamano_y_servicio(client, cookies_a, lav):
    r = await client.post(
        "/v1/jobs",
        json={"occurred_at": _iso(datetime.now(UTC)), "plate": "AB123CD"},
        cookies=cookies_a,
        headers=_clave(),
    )
    assert r.status_code == 422


async def test_un_ingreso_sin_patente_ni_cliente_crea_un_auto_anonimo(client, cookies_a, lav):
    r = await client.post(
        "/v1/jobs",
        json={
            "occurred_at": _iso(datetime.now(UTC)),
            "vehicle_size_id": str(lav.auto),
            "service_ids": [str(lav.lavado)],
        },
        cookies=cookies_a,
        headers=_clave(),
    )
    assert r.status_code == 201, r.text
    assert r.json()["customer"] is None and r.json()["vehicle_plate"] is None


# ── Cancelar por demora: botón manual, con el reloj del servidor (A4, D6-2) ──


async def _presente(client, cookies, lv: Lavadero, *, minutos_de_demora: int) -> dict:
    """Un turno que empezó hace `minutos_de_demora` y cuyo cliente acaba de llegar."""
    ahora = datetime.now(UTC)
    turno = await lv.turno(
        inicio=ahora - timedelta(minutes=minutos_de_demora), ahora=ahora - timedelta(days=2)
    )
    r = await client.post(
        "/v1/jobs",
        json={"occurred_at": _iso(ahora), "booking_id": str(turno.id)},
        cookies=cookies,
        headers=_clave(),
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_cancelar_por_demora_se_rechaza_antes_de_la_tolerancia(client, cookies_a, lav):
    job = await _presente(client, cookies_a, lav, minutos_de_demora=10)
    assert job["tolerance_min"] == 20
    desde = _dt(job["cancel_delay_from"])
    assert desde == _dt(job["scheduled_at"]) + timedelta(minutes=21)
    r = await _accion(client, cookies_a, job["id"], "cancel-delay")
    assert r.status_code == 422 and _codigo(r) == "VALIDATION_ERROR"
    assert (await client.get(f"/v1/jobs/{job['id']}", cookies=cookies_a)).json()["status"] == (
        "PRESENTE"
    )  # nada se cancela solo


async def test_un_occurred_at_del_futuro_no_adelanta_la_tolerancia(client, cookies_a, lav):
    job = await _presente(client, cookies_a, lav, minutos_de_demora=10)
    futuro = datetime.now(UTC) + timedelta(hours=2)
    r = await _accion(client, cookies_a, job["id"], "cancel-delay", futuro)
    assert r.status_code == 422


async def test_cancelar_por_demora_pasada_la_tolerancia_retiene_la_sena(client, cookies_a, lav):
    job = await _presente(client, cookies_a, lav, minutos_de_demora=30)
    r = await _accion(client, cookies_a, job["id"], "cancel-delay", reason="No contestó")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CANCELADO_DEMORA" and r.json()["cancel_delay_from"] is None
    eventos = (await client.get(f"/v1/jobs/{job['id']}/events", cookies=cookies_a)).json()["items"]
    assert [e["event_type"] for e in eventos][-2:] == ["JOB_CANCELLED_DELAY", "DEPOSIT_RETAINED"]
    assert eventos[-2]["metadata"]["reason"] == "No contestó"


async def test_un_ingreso_sin_turno_no_se_cancela_por_demora(client, cookies_a, lav):
    job = (await _ingreso(client, cookies_a, lav)).json()
    assert job["cancel_delay_from"] is None
    assert (await _accion(client, cookies_a, job["id"], "cancel-delay")).status_code == 422


# ── Cobros (A2) ──────────────────────────────────────────────────────────────


async def _finalizado(client, cookies, lv: Lavadero) -> dict:
    job = (
        await _ingreso(client, cookies, lv, cuando=datetime.now(UTC) - timedelta(hours=1))
    ).json()
    await _accion(client, cookies, job["id"], "start")
    return (await _accion(client, cookies, job["id"], "finish")).json()


async def _cobrar(client, cookies, lv, job_id, monto: int | None = None, clave: str | None = None):
    cuerpo: dict[str, Any] = {
        "occurred_at": _iso(datetime.now(UTC)),
        "payment_method_id": str(lv.efectivo),
    }
    if monto is not None:
        cuerpo["amount_cents"] = monto
    return await client.post(
        f"/v1/jobs/{job_id}/payments",
        json=cuerpo,
        cookies=cookies,
        headers={"Idempotency-Key": clave or f"k-{uuid.uuid4()}"},
    )


async def test_un_cobro_parcial_deja_deuda_y_el_siguiente_suma(client, cookies_a, lav):
    job = await _finalizado(client, cookies_a, lav)
    uno = (await _cobrar(client, cookies_a, lav, job["id"], 500_000)).json()
    assert uno["status"] == "FINALIZADO" and uno["balance_due_cents"] == PRECIO_FIJO - 500_000
    dos = (await _cobrar(client, cookies_a, lav, job["id"])).json()  # sin importe: el saldo
    assert dos["status"] == "COBRADO" and dos["balance_due_cents"] == 0
    assert len(dos["payments"]) == 2


async def test_el_cobro_idempotente_no_se_cobra_dos_veces(client, cookies_a, lav):
    job = await _finalizado(client, cookies_a, lav)
    clave = f"cobro-{uuid.uuid4()}"
    uno = await _cobrar(client, cookies_a, lav, job["id"], 500_000, clave)
    dos = await _cobrar(client, cookies_a, lav, job["id"], 500_000, clave)
    assert uno.status_code == dos.status_code == 200
    assert len(dos.json()["payments"]) == 1


async def test_cobrar_de_mas_o_en_un_estado_que_no_corresponde(client, cookies_a, lav):
    job = await _finalizado(client, cookies_a, lav)
    de_mas = await _cobrar(client, cookies_a, lav, job["id"], PRECIO_FIJO + 1)
    assert de_mas.status_code == 422 and _codigo(de_mas) == "VALIDATION_ERROR"
    sin_importe = await client.post(
        f"/v1/jobs/{job['id']}/payments",
        json={
            "occurred_at": _iso(datetime.now(UTC)),
            "payment_method_id": str(lav.efectivo),
            "amount_cents": 0,
        },
        cookies=cookies_a,
        headers=_clave(),
    )
    assert sin_importe.status_code == 422


async def test_anular_un_cobro_deja_la_deuda_y_solo_anula_cobros_del_mismo_job(
    client, cookies_a, lav
):
    job = await _finalizado(client, cookies_a, lav)
    cobrado = (await _cobrar(client, cookies_a, lav, job["id"])).json()
    pago = cobrado["payments"][0]["id"]
    otro = await _finalizado(client, cookies_a, lav)
    ajeno = await client.post(
        f"/v1/jobs/{otro['id']}/payments/{pago}/void",
        json={"occurred_at": _iso(datetime.now(UTC)), "reason": "Error"},
        cookies=cookies_a,
        headers=_clave(),
    )
    assert ajeno.status_code == 404  # el cobro es de otro job: el mismo 404
    r = await client.post(
        f"/v1/jobs/{job['id']}/payments/{pago}/void",
        json={"occurred_at": _iso(datetime.now(UTC)), "reason": "Error de carga"},
        cookies=cookies_a,
        headers=_clave(),
    )
    assert r.status_code == 200, r.text
    anulado = r.json()
    assert anulado["payments"][0]["voided"] is True
    assert anulado["balance_due_cents"] == PRECIO_FIJO and anulado["status"] == "COBRADO"
    sin_motivo = await client.post(
        f"/v1/jobs/{job['id']}/payments/{pago}/void",
        json={"occurred_at": _iso(datetime.now(UTC)), "reason": "  "},
        cookies=cookies_a,
        headers=_clave(),
    )
    assert sin_motivo.status_code == 422


async def test_retirar_un_job_cobrado(client, cookies_a, lav):
    job = await _finalizado(client, cookies_a, lav)
    await _cobrar(client, cookies_a, lav, job["id"])
    r = await _accion(client, cookies_a, job["id"], "pick-up")
    assert r.status_code == 200 and r.json()["status"] == "RETIRADO"


async def test_retirar_con_deuda_siendo_cliente_nuevo_es_422(client, cookies_a, lav):
    job = await _finalizado(client, cookies_a, lav)
    r = await _accion(client, cookies_a, job["id"], "pick-up")
    assert r.status_code == 422, r.text


# ── Recepción opcional (D-007) ───────────────────────────────────────────────


async def test_la_recepcion_es_opcional_y_la_segunda_actualiza_la_primera(client, cookies_a, lav):
    job = (await _ingreso(client, cookies_a, lav)).json()
    assert job["inspection"] is None
    uno = await _accion(client, cookies_a, job["id"], "inspection", dirt_level="BARRO")
    assert uno.status_code == 200, uno.text
    assert uno.json()["inspection"]["dirt_level"] == "BARRO"
    dos = await _accion(
        client,
        cookies_a,
        job["id"],
        "inspection",
        dirt_level="NORMAL",
        pre_existing_damage="Rayón en la puerta",
        photo_consent=True,
        checklist={"espejos": True},
    )
    assert dos.json()["inspection"]["pre_existing_damage"] == "Rayón en la puerta"
    assert dos.json()["inspection"]["dirt_level"] == "NORMAL"
    invalida = await _accion(client, cookies_a, job["id"], "inspection", dirt_level="LODO")
    assert invalida.status_code == 422


# ── Agenda del día (A1) ──────────────────────────────────────────────────────


def _local(dia, hora: int, minuto: int = 0) -> datetime:
    return datetime.combine(dia, time(hora, minuto), tzinfo=CORDOBA)


async def test_el_dia_trae_turnos_y_jobs_por_puesto_en_la_zona_del_negocio(client, cookies_a, lav):
    hoy_local = datetime.now(CORDOBA).date()
    manana = hoy_local + timedelta(days=1)
    ahora = datetime.now(UTC)
    sin_job = await lav.turno(
        inicio=_local(manana, 10).astimezone(UTC), ahora=ahora - timedelta(days=1)
    )
    con_job = await lav.turno(
        inicio=_local(manana, 12).astimezone(UTC),
        puesto=lav.puesto_2,
        ahora=ahora - timedelta(days=1),
    )
    await lav.jobs.receive(
        idempotency_key=f"rec-{uuid.uuid4()}", arrived_at=ahora, booking_id=con_job.id
    )
    # 23:30 local = 02:30 UTC del día siguiente: sigue siendo el día local de `manana`.
    tarde = await lav.turno(
        inicio=_local(manana, 23, 30).astimezone(UTC), ahora=ahora - timedelta(days=1)
    )
    otro_dia = await lav.turno(
        inicio=_local(manana + timedelta(days=1), 0, 30).astimezone(UTC),
        ahora=ahora - timedelta(days=1),
    )

    r = await client.get("/v1/agenda/day", params={"date": manana.isoformat()}, cookies=cookies_a)
    assert r.status_code == 200, r.text
    dia = r.json()
    assert dia["timezone"] == "America/Argentina/Cordoba" and dia["tolerance_min"] == 20
    assert [x["name"] for x in dia["resources"]] == ["Puesto 1", "Puesto 2"]
    ids = [(e["kind"], e["id"]) for e in dia["entries"]]
    assert ids == [
        ("BOOKING", str(sin_job.id)),
        ("JOB", ids[1][1]),  # el job del turno de las 12
        ("BOOKING", str(tarde.id)),
    ]
    assert str(otro_dia.id) not in [i for _, i in ids]  # 00:30 del día siguiente
    turno, job, _ = dia["entries"]
    assert turno["booking_status"] == "CONFIRMADO" and turno["code"] == sin_job.code
    assert turno["customer"]["name"] == "Cliente Uno" and turno["services"] == ["Lavado completo"]
    assert job["job_status"] == "PRESENTE" and job["booking_id"] == str(con_job.id)
    assert job["resource_id"] == str(lav.puesto_2) and job["balance_due_cents"] == PRECIO_FIJO
    assert dia["truncated"] is False


async def test_el_dia_no_muestra_el_turno_que_ya_tiene_job_ni_los_cancelados(
    client, cookies_a, lav
):
    hoy_local = datetime.now(CORDOBA).date()
    ahora = datetime.now(UTC)
    inicio = _local(hoy_local, 15).astimezone(UTC)
    turno = await lav.turno(inicio=inicio, ahora=ahora - timedelta(days=2))
    cancelado = await lav.turno(inicio=inicio + timedelta(hours=2), ahora=ahora - timedelta(days=2))
    await lav.turnos.cancel_operational(cancelado.id, requested_at=ahora, reason="Cliente avisó")
    await lav.jobs.receive(
        idempotency_key=f"rec-{uuid.uuid4()}", arrived_at=ahora, booking_id=turno.id
    )
    r = await client.get(
        "/v1/agenda/day", params={"date": hoy_local.isoformat()}, cookies=cookies_a
    )
    tipos = [(e["kind"], e["booking_id"]) for e in r.json()["entries"]]
    assert tipos == [("JOB", str(turno.id))]


@pytest.mark.parametrize("fecha", ["no-es-fecha", "2026-13-01", "9999-12-31", ""])
async def test_el_dia_invalido_es_422(client, cookies_a, lav, fecha):
    r = await client.get("/v1/agenda/day", params={"date": fecha}, cookies=cookies_a)
    assert r.status_code == 422


async def test_el_dia_sin_el_parametro_es_422(client, cookies_a, lav):
    assert (await client.get("/v1/agenda/day", cookies=cookies_a)).status_code == 422


# ── Turno "solo a mano" (A5) ─────────────────────────────────────────────────


def _a_mano(lv: Lavadero, inicio: datetime, **extra):
    return {
        "start_at": _iso(inicio),
        "resource_id": str(lv.puesto),
        "vehicle_size_id": str(lv.auto),
        "service_ids": [str(lv.lavado)],
        "customer_name": "Cliente Mostrador",
        "customer_phone": "+54 9 351 000-0202",
        **extra,
    }


async def test_un_turno_a_mano_nace_interno_y_confirmado(client, cookies_a, lav):
    inicio = datetime.now(UTC) + timedelta(minutes=10)  # dentro de la anticipación del turnero
    r = await client.post(
        "/v1/bookings/manual", json=_a_mano(lav, inicio), cookies=cookies_a, headers=_clave()
    )
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    turno = cuerpo["booking"]
    assert turno["source"] == "AGENDA_INTERNA" and turno["channel"] == "CARGA_MANUAL"
    assert turno["code"].startswith("INT-") and turno["status"] == "CONFIRMADO"
    assert turno["customer"]["name"] == "Cliente Mostrador"
    assert cuerpo["deposit_paid_cents"] == 0


async def test_un_turno_a_mano_sin_nombre_ni_patente(client, cookies_a, lav):
    inicio = datetime.now(UTC) + timedelta(hours=1)
    cuerpo = _a_mano(lav, inicio)
    del cuerpo["customer_name"], cuerpo["customer_phone"]
    r = await client.post("/v1/bookings/manual", json=cuerpo, cookies=cookies_a, headers=_clave())
    assert r.status_code == 201, r.text
    assert r.json()["booking"]["customer"]["name"] == "Sin nombre"
    assert r.json()["booking"]["vehicle_plate"] is None


async def test_un_turno_a_mano_con_sena_cobrada_o_pendiente(client, cookies_a, lav):
    await lav.activar_sena()
    inicio = datetime.now(UTC) + timedelta(hours=2)
    pendiente = await client.post(
        "/v1/bookings/manual",
        json=_a_mano(lav, inicio, service_ids=[str(lav.lavado_con_sena)]),
        cookies=cookies_a,
        headers=_clave(),
    )
    assert pendiente.json()["booking"]["status"] == "PENDIENTE_SEÑA"
    assert pendiente.json()["booking"]["hold_expires_at"] is not None
    cobrada = await client.post(
        "/v1/bookings/manual",
        json=_a_mano(
            lav,
            inicio + timedelta(hours=3),
            service_ids=[str(lav.lavado_con_sena)],
            deposit={"payment_method_id": str(lav.efectivo)},
        ),
        cookies=cookies_a,
        headers=_clave(),
    )
    assert cobrada.status_code == 201, cobrada.text
    assert cobrada.json()["booking"]["status"] == "CONFIRMADO"
    assert cobrada.json()["deposit_paid_cents"] > 0


async def test_una_sena_en_un_turno_que_no_la_pide_es_422(client, cookies_a, lav):
    r = await client.post(
        "/v1/bookings/manual",
        json=_a_mano(
            lav,
            datetime.now(UTC) + timedelta(hours=2),
            deposit={"payment_method_id": str(lav.efectivo)},
        ),
        cookies=cookies_a,
        headers=_clave(),
    )
    assert r.status_code == 422, r.text


async def test_un_turno_a_mano_en_el_pasado_es_422_y_va_por_la_carga_retrospectiva(
    client, cookies_a, lav
):
    r = await client.post(
        "/v1/bookings/manual",
        json=_a_mano(lav, datetime.now(UTC) - timedelta(hours=1)),
        cookies=cookies_a,
        headers=_clave(),
    )
    assert r.status_code == 422, r.text


async def test_un_reenvio_de_la_clave_del_turno_a_mano_es_409_duplicado(client, cookies_a, lav):
    cabecera = _clave()
    cuerpo = _a_mano(lav, datetime.now(UTC) + timedelta(hours=1))
    uno = await client.post("/v1/bookings/manual", json=cuerpo, cookies=cookies_a, headers=cabecera)
    dos = await client.post("/v1/bookings/manual", json=cuerpo, cookies=cookies_a, headers=cabecera)
    assert uno.status_code == 201
    assert dos.status_code == 409 and _codigo(dos) == "DUPLICATE_IDEMPOTENT"


async def test_un_puesto_ajeno_es_404(client, cookies_b, lav, owner_b, db_session, tenant_b):
    inicio = datetime.now(UTC) + timedelta(hours=1)
    r = await client.post(
        "/v1/bookings/manual", json=_a_mano(lav, inicio), cookies=cookies_b, headers=_clave()
    )
    assert r.status_code == 404


# ── Carga retrospectiva (A6, D6-3) ───────────────────────────────────────────


def _retro(lv: Lavadero, dia, desde=(10, 0), hasta=(11, 0), **extra):
    return {
        "started_at": _iso(_local(dia, *desde)),
        "finished_at": _iso(_local(dia, *hasta)),
        "vehicle_size_id": str(lv.auto),
        "service_ids": [str(lv.lavado)],
        "customer_name": "Cliente Ayer",
        "plate": "XY987ZW",
        **extra,
    }


async def _cargar(client, cookies, cuerpo, clave: str | None = None):
    return await client.post(
        "/v1/jobs/retrospective",
        json=cuerpo,
        cookies=cookies,
        headers={"Idempotency-Key": clave or f"k-{uuid.uuid4()}"},
    )


async def test_un_lavado_de_ayer_cobrado_cae_en_la_agenda_de_ayer_con_la_hora_real(
    client, cookies_a, lav
):
    ayer = datetime.now(CORDOBA).date() - timedelta(days=1)
    r = await _cargar(
        client,
        cookies_a,
        _retro(lav, ayer, payment={"payment_method_id": str(lav.efectivo)}),
    )
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["entry_mode"] == "RETROSPECTIVA" and job["status"] == "COBRADO"
    assert job["balance_due_cents"] == 0 and job["paid_cents"] == PRECIO_FIJO
    assert _dt(job["started_at"]) == _local(ayer, 10)
    assert _dt(job["finished_at"]) == _local(ayer, 11)

    eventos = (await client.get(f"/v1/jobs/{job['id']}/events", cookies=cookies_a)).json()["items"]
    assert [e["event_type"] for e in eventos] == [
        "JOB_RECEIVED",
        "JOB_STARTED",
        "JOB_FINISHED",
        "PAYMENT_RECORDED",
        "JOB_SETTLED",
    ]
    assert all(e["metadata"]["manual_entry"] is True for e in eventos)
    assert _dt(eventos[1]["occurred_at"]) == _local(ayer, 10)
    assert _dt(eventos[2]["occurred_at"]) == _local(ayer, 11)

    en_ayer = (
        await client.get("/v1/agenda/day", params={"date": ayer.isoformat()}, cookies=cookies_a)
    ).json()
    assert [(e["kind"], e["job_id"]) for e in en_ayer["entries"]] == [("JOB", job["id"])]
    assert en_ayer["entries"][0]["entry_mode"] == "RETROSPECTIVA"
    hoy = datetime.now(CORDOBA).date()
    en_hoy = (
        await client.get("/v1/agenda/day", params={"date": hoy.isoformat()}, cookies=cookies_a)
    ).json()
    assert en_hoy["entries"] == []  # no en la de hoy


async def test_un_lavado_de_ayer_con_cobro_pendiente_queda_finalizado_y_debe(
    client, cookies_a, lav
):
    ayer = datetime.now(CORDOBA).date() - timedelta(days=1)
    r = await _cargar(client, cookies_a, _retro(lav, ayer))
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "FINALIZADO" and r.json()["balance_due_cents"] == PRECIO_FIJO
    en_ayer = (
        await client.get("/v1/agenda/day", params={"date": ayer.isoformat()}, cookies=cookies_a)
    ).json()
    assert en_ayer["entries"][0]["balance_due_cents"] == PRECIO_FIJO  # "debe"


async def test_un_cobro_parcial_en_la_carga_retrospectiva(client, cookies_a, lav):
    ayer = datetime.now(CORDOBA).date() - timedelta(days=1)
    r = await _cargar(
        client,
        cookies_a,
        _retro(
            lav,
            ayer,
            payment={"payment_method_id": str(lav.efectivo), "amount_cents": 500_000},
        ),
    )
    assert r.json()["status"] == "FINALIZADO"
    assert r.json()["balance_due_cents"] == PRECIO_FIJO - 500_000


async def test_la_carga_retrospectiva_sin_nombre_ni_patente(client, cookies_a, lav):
    ayer = datetime.now(CORDOBA).date() - timedelta(days=1)
    cuerpo = _retro(lav, ayer)
    del cuerpo["customer_name"], cuerpo["plate"]
    r = await _cargar(client, cookies_a, cuerpo)
    assert r.status_code == 201, r.text
    assert r.json()["customer"] is None and r.json()["vehicle_plate"] is None


async def test_la_carga_retrospectiva_registra_el_puesto_sin_validar_la_ocupacion(
    client, cookies_a, lav
):
    ayer = datetime.now(CORDOBA).date() - timedelta(days=1)
    cuerpo = _retro(lav, ayer, resource_id=str(lav.puesto))
    uno = await _cargar(client, cookies_a, cuerpo)
    dos = await _cargar(client, cookies_a, cuerpo)  # misma hora y puesto, otra clave: es historia
    assert uno.status_code == dos.status_code == 201
    assert uno.json()["resource_id"] == str(lav.puesto)


async def test_la_carga_retrospectiva_idempotente_no_duplica(client, cookies_a, lav):
    ayer = datetime.now(CORDOBA).date() - timedelta(days=1)
    clave = f"retro-{uuid.uuid4()}"
    cuerpo = _retro(lav, ayer, payment={"payment_method_id": str(lav.efectivo)})
    uno = await _cargar(client, cookies_a, cuerpo, clave)
    dos = await _cargar(client, cookies_a, cuerpo, clave)
    assert uno.status_code == dos.status_code == 201
    assert uno.json()["id"] == dos.json()["id"]
    assert uno.json()["status"] == dos.json()["status"] == "COBRADO"
    en_ayer = (
        await client.get("/v1/agenda/day", params={"date": ayer.isoformat()}, cookies=cookies_a)
    ).json()
    assert len(en_ayer["entries"]) == 1
    assert len(dos.json()["payments"]) == 1


async def test_la_carga_retrospectiva_rechaza_horas_invalidas(client, cookies_a, lav):
    ayer = datetime.now(CORDOBA).date() - timedelta(days=1)
    invertida = _retro(lav, ayer, desde=(11, 0), hasta=(10, 0))
    assert (await _cargar(client, cookies_a, invertida)).status_code == 422
    manana = datetime.now(CORDOBA).date() + timedelta(days=1)
    futura = _retro(lav, manana)
    r = await _cargar(client, cookies_a, futura)
    assert r.status_code == 422 and _codigo(r) == "VALIDATION_ERROR"
    sin_zona = {**_retro(lav, ayer), "started_at": "2026-11-15T10:00:00"}
    assert (await _cargar(client, cookies_a, sin_zona)).status_code == 422
    sin_servicio = {**_retro(lav, ayer), "service_ids": []}
    assert (await _cargar(client, cookies_a, sin_servicio)).status_code == 422


async def test_la_carga_retrospectiva_no_acepta_un_servicio_a_cotizar(client, cookies_a, lav):
    ayer = datetime.now(CORDOBA).date() - timedelta(days=1)
    r = await _cargar(client, cookies_a, _retro(lav, ayer, service_ids=[str(lav.tapizado)]))
    assert r.status_code == 422, r.text


# ── Permisos y aislamiento (A9) ──────────────────────────────────────────────


async def test_sin_sesion_es_401_y_sin_permiso_es_403(client, db_session, tenant_a, lav):
    assert (await client.get("/v1/agenda/day", params={"date": "2026-11-16"})).status_code == 401
    solo_ver = await _empleado(db_session, tenant_a, "Solo Ver", {Permission.AGENDA_VER})
    ver = await client.get("/v1/agenda/day", params={"date": "2026-11-16"}, cookies=solo_ver)
    assert ver.status_code == 200
    assert (await _ingreso(client, solo_ver, lav)).status_code == 403
    sin_nada = await _empleado(db_session, tenant_a, "Sin Nada", set())
    assert (
        await client.get("/v1/agenda/day", params={"date": "2026-11-16"}, cookies=sin_nada)
    ).status_code == 403


async def test_cada_ruta_pide_su_permiso(client, db_session, tenant_a, lav, cookies_a):
    job = (await _ingreso(client, cookies_a, lav)).json()
    lavador = await _empleado(
        db_session, tenant_a, "Lavador Demo", {Permission.AGENDA_VER, Permission.JOBS_OPERAR}
    )
    assert (await _accion(client, lavador, job["id"], "start")).status_code == 200
    cobro = await _cobrar(client, lavador, lav, job["id"])
    assert cobro.status_code == 403  # COBROS_REGISTRAR
    anular = await client.post(
        f"/v1/jobs/{job['id']}/payments/{uuid.uuid4()}/void",
        json={"occurred_at": _iso(datetime.now(UTC)), "reason": "x"},
        cookies=lavador,
        headers=_clave(),
    )
    assert anular.status_code == 403  # COBROS_ANULAR
    a_mano = await client.post(
        "/v1/bookings/manual",
        json=_a_mano(lav, datetime.now(UTC) + timedelta(hours=1)),
        cookies=lavador,
        headers=_clave(),
    )
    assert a_mano.status_code == 403  # TURNOS_GESTIONAR
    sin_ver = await _empleado(db_session, tenant_a, "Sin Ver", {Permission.JOBS_OPERAR})
    assert (await client.get(f"/v1/jobs/{job['id']}", cookies=sin_ver)).status_code == 403
    assert (await client.get(f"/v1/jobs/{job['id']}/events", cookies=sin_ver)).status_code == 403


async def test_la_carga_retrospectiva_con_cobro_exige_tambien_cobrar(
    client, db_session, tenant_a, lav
):
    ayer = datetime.now(CORDOBA).date() - timedelta(days=1)
    operador = await _empleado(
        db_session, tenant_a, "Operador Demo", {Permission.AGENDA_VER, Permission.JOBS_OPERAR}
    )
    con_cobro = _retro(lav, ayer, payment={"payment_method_id": str(lav.efectivo)})
    assert (await _cargar(client, operador, con_cobro)).status_code == 403
    assert (await _cargar(client, operador, _retro(lav, ayer))).status_code == 201  # pendiente
    cajero = await _empleado(
        db_session,
        tenant_a,
        "Cajero Demo",
        {Permission.AGENDA_VER, Permission.JOBS_OPERAR, Permission.COBROS_REGISTRAR},
    )
    assert (await _cargar(client, cajero, con_cobro)).status_code == 201


async def test_un_turno_a_mano_con_sena_exige_tambien_cobrar(client, db_session, tenant_a, lav):
    await lav.activar_sena()
    gestor = await _empleado(
        db_session, tenant_a, "Gestor Demo", {Permission.AGENDA_VER, Permission.TURNOS_GESTIONAR}
    )
    r = await client.post(
        "/v1/bookings/manual",
        json=_a_mano(
            lav,
            datetime.now(UTC) + timedelta(hours=2),
            service_ids=[str(lav.lavado_con_sena)],
            deposit={"payment_method_id": str(lav.efectivo)},
        ),
        cookies=gestor,
        headers=_clave(),
    )
    assert r.status_code == 403


async def test_b_no_ve_ni_opera_lo_de_a_y_el_404_es_identico(client, cookies_a, cookies_b, lav):
    job = (await _ingreso(client, cookies_a, lav)).json()
    pago_job = await _finalizado(client, cookies_a, lav)
    pago = (await _cobrar(client, cookies_a, lav, pago_job["id"])).json()["payments"][0]["id"]
    inexistente = uuid.uuid4()
    cuerpo = {"occurred_at": _iso(datetime.now(UTC))}
    rutas = [
        ("get", "/v1/jobs/{}", None),
        ("get", "/v1/jobs/{}/events", None),
        ("post", "/v1/jobs/{}/start", cuerpo),
        ("post", "/v1/jobs/{}/finish", cuerpo),
        ("post", "/v1/jobs/{}/pick-up", cuerpo),
        ("post", "/v1/jobs/{}/cancel-delay", cuerpo),
        ("post", "/v1/jobs/{}/inspection", cuerpo),
        (
            "post",
            "/v1/jobs/{}/payments",
            {**cuerpo, "payment_method_id": str(lav.efectivo), "amount_cents": 1000},
        ),
        ("post", "/v1/jobs/{}/payments/" + pago + "/void", {**cuerpo, "reason": "x"}),
    ]
    for metodo, ruta, json in rutas:
        ajeno = await client.request(
            metodo.upper(),
            ruta.format(job["id"]),
            json=json,
            cookies=cookies_b,
            headers=_clave(),
        )
        nada = await client.request(
            metodo.upper(),
            ruta.format(inexistente),
            json=json,
            cookies=cookies_b,
            headers=_clave(),
        )
        assert ajeno.status_code == nada.status_code == 404, (metodo, ruta, ajeno.text)
        assert ajeno.json() == nada.json(), (metodo, ruta)
    # Y A sigue con su job intacto.
    assert (await client.get(f"/v1/jobs/{job['id']}", cookies=cookies_a)).json()["status"] == (
        "PRESENTE"
    )


async def test_el_dia_de_b_no_trae_nada_de_a(client, cookies_a, cookies_b, lav):
    await _ingreso(client, cookies_a, lav)
    hoy = datetime.now(CORDOBA).date().isoformat()
    de_a = (await client.get("/v1/agenda/day", params={"date": hoy}, cookies=cookies_a)).json()
    de_b = (await client.get("/v1/agenda/day", params={"date": hoy}, cookies=cookies_b)).json()
    assert len(de_a["entries"]) == 1
    assert de_b["entries"] == [] and de_b["resources"] == []


async def test_recibir_el_turno_de_otro_negocio_o_con_servicios_ajenos_es_404(
    client, cookies_a, cookies_b, lav
):
    ahora = datetime.now(UTC)
    turno = await lav.turno(inicio=ahora + timedelta(hours=3), ahora=ahora - timedelta(days=1))
    r = await client.post(
        "/v1/jobs",
        json={"occurred_at": _iso(ahora), "booking_id": str(turno.id)},
        cookies=cookies_b,
        headers=_clave(),
    )
    assert r.status_code == 404
    servicio_ajeno = await _ingreso(client, cookies_b, lav)  # catálogo de A usado desde B
    assert servicio_ajeno.status_code == 404
