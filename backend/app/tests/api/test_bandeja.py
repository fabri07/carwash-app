"""Bandeja del dueño (PR 5.3): `/bookings`, `/incident-marks` y `/cancellations` —
FASE-5-CONTRATO §2, §4.1, B2 y B11.

Datos sintéticos: el catálogo se arma por los servicios (`armar_catalogo`) sobre la misma sesión
que usa el cliente HTTP. El choque del `EXCLUDE` (409 `SLOT_TAKEN`) se prueba contra Postgres en
`tests/application/test_bandeja_pg.py`.
"""

import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
import pytest_asyncio

from app.domain.enums import BookingStatus
from app.domain.permissions import ALL_PERMISSIONS, Permission
from app.domain.roles import Role
from app.tests.application._armado import SENA, Lavadero, armar_catalogo
from app.tests.application._bandeja import en_revision
from app.tests.conftest import make_profile, make_user, session_cookies

HOY = datetime.now(UTC)
#: Un lunes futuro a las 10:00 de Córdoba (13:00 UTC), con horario de 09 a 13.
LUNES = (HOY + timedelta(days=(7 - HOY.weekday()) % 7 or 7)).date()
INICIO = datetime.combine(LUNES, time(13), tzinfo=UTC)


def _codigo(r) -> str:
    return r.json()["detail"]["code"]


@pytest_asyncio.fixture
async def lav(db_session, tenant_a, owner) -> Lavadero:
    lv = await armar_catalogo(db_session, tenant_a.id, owner.id)
    await lv.catalogo.add_business_hours(1, time(9, 0), time(13, 0))
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


# ── Lecturas ─────────────────────────────────────────────────────────────────


async def test_lista_filtra_por_estado_y_dia_y_pagina(client, cookies_a, lav):
    confirmado = await lav.turno(inicio=INICIO, ahora=HOY)
    con_sena = await lav.turno_con_sena(inicio=INICIO + timedelta(hours=1), ahora=HOY)
    await lav.turno(inicio=INICIO + timedelta(days=1), ahora=HOY)
    r = await client.get(
        "/v1/bookings",
        params={"from": LUNES.isoformat(), "to": LUNES.isoformat()},
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert [b["id"] for b in cuerpo["items"]] == [str(confirmado.id), str(con_sena.id)]
    fila = cuerpo["items"][0]
    assert fila["customer"]["name"] == "Cliente Uno"
    assert fila["customer"]["phone_e164"].startswith("+54")
    assert fila["vehicle_plate"].replace(" ", "") == "AB123CD"
    assert fila["services"][0]["name"] == "Lavado completo"
    assert cuerpo["total"] == 2 and cuerpo["has_more"] is False

    solo = await client.get(
        "/v1/bookings",
        params=[("status", "PENDIENTE_SEÑA"), ("status", "VENCIDO")],
        cookies=cookies_a,
    )
    assert [b["id"] for b in solo.json()["items"]] == [str(con_sena.id)]
    assert solo.json()["items"][0]["deposit_required_cents"] > 0

    pagina = await client.get("/v1/bookings", params={"limit": 1, "offset": 2}, cookies=cookies_a)
    assert pagina.json()["total"] == 3 and len(pagina.json()["items"]) == 1


@pytest.mark.parametrize(
    "params",
    [
        {"status": "NO_EXISTE"},
        {"from": "2026-13-01"},
        {"from": "2026-11-20", "to": "2026-11-19"},
        {"from": "9999-12-31"},
        {"limit": 0},
    ],
)
async def test_filtros_invalidos_son_422(client, cookies_a, lav, params):
    r = await client.get("/v1/bookings", params=params, cookies=cookies_a)
    assert r.status_code == 422, r.text


async def test_contador_y_detalle(client, cookies_a, lav):
    turno = await lav.turno_con_sena(inicio=INICIO, ahora=HOY)
    await en_revision(lav, inicio=INICIO + timedelta(hours=1), ahora=HOY)
    c = (await client.get("/v1/bookings/counts", cookies=cookies_a)).json()
    assert c == {"pending_deposit": 1, "in_review": 1, "deposit_cases": 0, "total": 2}
    d = await client.get(f"/v1/bookings/{turno.id}", cookies=cookies_a)
    assert d.status_code == 200, d.text
    detalle = d.json()
    assert detalle["status"] == "PENDIENTE_SEÑA" and detalle["hold_expires_at"] is not None
    assert detalle["payments"] == [] and detalle["cancellation"] is None
    assert detalle["incidents"] == 0 and detalle["incident_marks"] == []


# ── Seña ─────────────────────────────────────────────────────────────────────


async def test_confirmar_sena_con_la_clave_del_pago(client, cookies_a, lav):
    turno = await lav.turno_con_sena(inicio=INICIO, ahora=HOY)
    cuerpo = {"payment_method_id": str(lav.efectivo)}
    sin_clave = await client.post(
        f"/v1/bookings/{turno.id}/confirm-deposit", json=cuerpo, cookies=cookies_a
    )
    assert sin_clave.status_code == 422
    cabecera = {"Idempotency-Key": f"sena-{uuid.uuid4()}"}
    r = await client.post(
        f"/v1/bookings/{turno.id}/confirm-deposit", json=cuerpo, cookies=cookies_a, headers=cabecera
    )
    assert r.status_code == 200, r.text
    assert r.json()["late"] is False
    assert r.json()["booking"]["status"] == "CONFIRMADO"
    assert r.json()["booking"]["deposit_paid_cents"] == SENA  # sin importe: la seña pedida
    otra = await client.post(
        f"/v1/bookings/{turno.id}/confirm-deposit", json=cuerpo, cookies=cookies_a, headers=cabecera
    )
    assert otra.status_code == 200 and otra.json()["booking"]["deposit_paid_cents"] == SENA


async def test_confirmar_sena_tarde_con_el_horario_libre(client, cookies_a, lav):
    turno = await lav.turno_con_sena(inicio=INICIO, ahora=HOY - timedelta(hours=2))
    r = await client.post(
        f"/v1/bookings/{turno.id}/confirm-deposit",
        json={"payment_method_id": str(lav.efectivo), "amount_cents": SENA},
        cookies=cookies_a,
        headers={"Idempotency-Key": "tarde-1"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["late"] is True and r.json()["booking"]["status"] == "CONFIRMADO"


async def test_confirmar_sena_de_un_turno_sin_sena_es_409(client, cookies_a, lav):
    turno = await lav.turno(inicio=INICIO, ahora=HOY)
    r = await client.post(
        f"/v1/bookings/{turno.id}/confirm-deposit",
        json={"payment_method_id": str(lav.efectivo), "amount_cents": 1000},
        cookies=cookies_a,
        headers={"Idempotency-Key": "sin-sena"},
    )
    assert r.status_code == 409 and _codigo(r) == "CONFLICT"


# ── Lista de espera ──────────────────────────────────────────────────────────


async def test_aprobar_y_rechazar(client, cookies_a, lav):
    pedido = await en_revision(lav, inicio=INICIO, ahora=HOY, puesto=lav.puesto_2)
    r = await client.post(f"/v1/bookings/{pedido.id}/approve", cookies=cookies_a)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CONFIRMADO" and r.json()["resource_id"] == str(lav.puesto)
    otra = await client.post(f"/v1/bookings/{pedido.id}/approve", cookies=cookies_a)
    assert otra.status_code == 409 and _codigo(otra) == "CONFLICT"

    rechazado = await en_revision(lav, inicio=INICIO + timedelta(hours=1), ahora=HOY)
    sin_motivo = await client.post(
        f"/v1/bookings/{rechazado.id}/reject", json={"reason": "  "}, cookies=cookies_a
    )
    assert sin_motivo.status_code == 422
    r = await client.post(
        f"/v1/bookings/{rechazado.id}/reject", json={"reason": "Sin lugar"}, cookies=cookies_a
    )
    assert r.status_code == 200, r.text
    assert r.json()["resulting_status"] == "RECHAZADO" and r.json()["reason"] == "Sin lugar"


async def test_aprobar_con_todo_bloqueado_es_409_blocked(client, cookies_a, lav):
    pedido = await en_revision(lav, inicio=INICIO, ahora=HOY)
    await client.post("/v1/agenda/blocks", json={"date": LUNES.isoformat()}, cookies=cookies_a)
    r = await client.post(f"/v1/bookings/{pedido.id}/approve", cookies=cookies_a)
    assert r.status_code == 409 and _codigo(r) == "BLOCKED"


# ── Operación ────────────────────────────────────────────────────────────────


async def test_cancelar_operativo_y_no_asistio(client, cookies_a, lav):
    turno = await lav.turno(inicio=INICIO, ahora=HOY)
    r = await client.post(
        f"/v1/bookings/{turno.id}/cancel", json={"reason": "Lluvia"}, cookies=cookies_a
    )
    assert r.status_code == 200, r.text
    assert r.json()["classification"] == "OPERATIVA"
    assert r.json()["resulting_status"] == "CANCELADO_OPERATIVO"
    futuro = await lav.turno(inicio=INICIO + timedelta(hours=1), ahora=HOY)
    antes = await client.post(f"/v1/bookings/{futuro.id}/no-show", cookies=cookies_a)
    assert antes.status_code == 422 and _codigo(antes) == "VALIDATION_ERROR"
    otro = await lav.turno(inicio=HOY - timedelta(hours=1), ahora=HOY - timedelta(days=3))
    r = await client.post(f"/v1/bookings/{otro.id}/no-show", cookies=cookies_a)
    assert r.status_code == 200 and r.json()["status"] == "NO_ASISTIO"
    dos = await client.post(f"/v1/bookings/{otro.id}/no-show", cookies=cookies_a)
    assert dos.status_code == 409 and _codigo(dos) == "CONFLICT"


async def test_reprogramar_y_cambiar_de_puesto(client, cookies_a, lav):
    turno = await lav.turno(inicio=INICIO, ahora=HOY)
    puesto = await client.post(
        f"/v1/bookings/{turno.id}/reschedule",
        json={"resource_id": str(lav.puesto_2)},
        cookies=cookies_a,
    )
    assert puesto.status_code == 200, puesto.text
    assert puesto.json()["booking"]["resource_id"] == str(lav.puesto_2)
    assert puesto.json()["outside_rules"] is False

    tarde = INICIO + timedelta(hours=5)  # 15:00 en Córdoba: fuera del horario (09–13)
    fuera = await client.post(
        f"/v1/bookings/{turno.id}/reschedule",
        json={"start_at": tarde.isoformat()},
        cookies=cookies_a,
    )
    assert fuera.status_code == 200, fuera.text
    assert fuera.json()["outside_rules"] is True
    assert fuera.json()["booking"]["start_at"].startswith(tarde.strftime("%Y-%m-%dT%H:%M"))


@pytest.mark.parametrize(
    "cuerpo",
    [
        {},
        {"start_at": "2030-01-01T10:00:00"},  # sin zona
        {"start_at": "2020-01-01T10:00:00Z"},  # pasado
        {"start_at": None, "resource_id": None},
    ],
)
async def test_reprogramar_invalido_es_422(client, cookies_a, lav, cuerpo):
    turno = await lav.turno(inicio=INICIO, ahora=HOY)
    r = await client.post(f"/v1/bookings/{turno.id}/reschedule", json=cuerpo, cookies=cookies_a)
    assert r.status_code == 422, r.text


async def test_reprogramar_sobre_un_bloqueo_es_409_blocked(client, cookies_a, lav):
    turno = await lav.turno(inicio=INICIO, ahora=HOY)
    await client.post(
        "/v1/agenda/blocks",
        json={
            "date": LUNES.isoformat(),
            "start_time": "11:00",
            "end_time": "12:00",
            "resource_id": str(lav.puesto),
        },
        cookies=cookies_a,
    )
    r = await client.post(
        f"/v1/bookings/{turno.id}/reschedule",
        json={"start_at": (INICIO + timedelta(hours=1)).isoformat()},
        cookies=cookies_a,
    )
    assert r.status_code == 409 and _codigo(r) == "BLOCKED"


async def test_marcar_y_desmarcar_incidencia(client, cookies_a, lav):
    turno = await lav.turno(inicio=INICIO, ahora=HOY)
    r = await client.post(
        f"/v1/bookings/{turno.id}/incident-marks",
        json={"reason": "No avisó", "tenant_id": str(uuid.uuid4())},
        cookies=cookies_a,
    )
    assert r.status_code == 201, r.text
    marca = r.json()
    assert marca["customer_id"] == str(lav.cliente) and marca["booking_id"] == str(turno.id)
    assert (await client.get(f"/v1/bookings/{turno.id}", cookies=cookies_a)).json()[
        "incidents"
    ] == 1
    assert (
        await client.delete(f"/v1/incident-marks/{marca['id']}", cookies=cookies_a)
    ).status_code == 204
    assert (
        await client.delete(f"/v1/incident-marks/{marca['id']}", cookies=cookies_a)
    ).status_code == 404


async def test_el_reenvio_de_una_accion_con_la_misma_clave_es_duplicate(client, cookies_a, lav):
    turno = await lav.turno(inicio=INICIO, ahora=HOY)
    cabecera = {"Idempotency-Key": "marca-1"}
    ruta = f"/v1/bookings/{turno.id}/incident-marks"
    assert (
        await client.post(ruta, json={"reason": "x"}, cookies=cookies_a, headers=cabecera)
    ).status_code == 201
    otra = await client.post(ruta, json={"reason": "x"}, cookies=cookies_a, headers=cabecera)
    assert otra.status_code == 409 and _codigo(otra) == "DUPLICATE_IDEMPOTENT"


# ── Señas a resolver ─────────────────────────────────────────────────────────


async def _cancelado_con_sena(lav: Lavadero):
    turno = await lav.turno_con_sena(inicio=INICIO, ahora=HOY)
    await lav.turnos.confirm_deposit(turno.id, lav.pago(SENA), now=HOY)
    return await lav.turnos.cancel_operational(turno.id, requested_at=HOY, reason="Lluvia")


async def test_listar_y_resolver_una_sena(client, cookies_a, lav):
    caso = await _cancelado_con_sena(lav)
    lista = (await client.get("/v1/cancellations", cookies=cookies_a)).json()
    assert lista["total"] == 1
    assert lista["items"][0]["cancellation"]["id"] == str(caso.id)
    assert lista["items"][0]["booking"]["deposit_paid_cents"] == SENA
    assert (await client.get("/v1/bookings/counts", cookies=cookies_a)).json()["deposit_cases"] == 1

    sin_pago = await client.post(
        f"/v1/cancellations/{caso.id}/resolve",
        json={"status": "DEVUELTA", "reason": "Transferido"},
        cookies=cookies_a,
    )
    assert sin_pago.status_code == 422
    sin_clave = await client.post(
        f"/v1/cancellations/{caso.id}/resolve",
        json={
            "status": "DEVUELTA",
            "reason": "Transferido",
            "refund": {"payment_method_id": str(lav.efectivo), "amount_cents": SENA},
        },
        cookies=cookies_a,
    )
    assert sin_clave.status_code == 422
    r = await client.post(
        f"/v1/cancellations/{caso.id}/resolve",
        json={
            "status": "DEVUELTA",
            "reason": "Transferido",
            "refund": {"payment_method_id": str(lav.efectivo), "amount_cents": SENA},
        },
        cookies=cookies_a,
        headers={"Idempotency-Key": "devolucion-1"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["deposit_status"] == "DEVUELTA" and r.json()["refund_payment_id"]
    assert (await client.get("/v1/cancellations", cookies=cookies_a)).json()["total"] == 0


async def test_retener_una_cancelacion_a_tiempo_es_409(client, cookies_a, lav):
    caso = await _cancelado_con_sena(lav)
    r = await client.post(
        f"/v1/cancellations/{caso.id}/resolve",
        json={"status": "RETENIDA", "reason": "No corresponde"},
        cookies=cookies_a,
    )
    assert r.status_code == 409 and _codigo(r) == "CONFLICT"
    ok = await client.post(
        f"/v1/cancellations/{caso.id}/resolve",
        json={"status": "REPROGRAMADA", "reason": "Pasa al sábado"},
        cookies=cookies_a,
    )
    assert ok.status_code == 200 and ok.json()["deposit_status"] == "REPROGRAMADA"


# ── Permisos (§2) y aislamiento (B2) ──────────────────────────────────────────


async def test_agenda_ver_lee_pero_no_actua(client, db_session, tenant_a, lav):
    turno = await lav.turno(inicio=INICIO, ahora=HOY)
    solo_ver = await _empleado(db_session, tenant_a, "Solo ver", {Permission.AGENDA_VER})
    for ruta in ("/v1/bookings", "/v1/bookings/counts", f"/v1/bookings/{turno.id}"):
        assert (await client.get(ruta, cookies=solo_ver)).status_code == 200, ruta
    assert (await client.get("/v1/cancellations", cookies=solo_ver)).status_code == 200
    for ruta, cuerpo in (
        (f"/v1/bookings/{turno.id}/cancel", {"reason": "x"}),
        (f"/v1/bookings/{turno.id}/no-show", None),
        (f"/v1/bookings/{turno.id}/reschedule", {"resource_id": str(lav.puesto_2)}),
        (f"/v1/bookings/{turno.id}/approve", None),
        (f"/v1/bookings/{turno.id}/reject", {"reason": "x"}),
        (f"/v1/bookings/{turno.id}/incident-marks", {"reason": "x"}),
    ):
        r = await client.post(ruta, json=cuerpo, cookies=solo_ver)
        assert r.status_code == 403 and _codigo(r) == "FORBIDDEN", ruta


async def test_turnos_gestionar_no_confirma_senas_y_cobros_registrar_si(
    client, db_session, tenant_a, lav
):
    turno = await lav.turno_con_sena(inicio=INICIO, ahora=HOY)
    gestiona = await _empleado(
        db_session, tenant_a, "Gestiona", set(ALL_PERMISSIONS - {Permission.COBROS_REGISTRAR})
    )
    cobra = await _empleado(db_session, tenant_a, "Cobra", {Permission.COBROS_REGISTRAR})
    cuerpo = {"payment_method_id": str(lav.efectivo)}
    no = await client.post(
        f"/v1/bookings/{turno.id}/confirm-deposit",
        json=cuerpo,
        cookies=gestiona,
        headers={"Idempotency-Key": "k-1"},
    )
    assert no.status_code == 403
    assert (
        await client.post(
            f"/v1/cancellations/{uuid.uuid4()}/resolve",
            json={"status": "REPROGRAMADA", "reason": "x"},
            cookies=gestiona,
        )
    ).status_code == 403
    si = await client.post(
        f"/v1/bookings/{turno.id}/confirm-deposit",
        json=cuerpo,
        cookies=cobra,
        headers={"Idempotency-Key": "k-2"},
    )
    assert si.status_code == 200, si.text
    # Sin AGENDA_VER no lee la bandeja.
    assert (await client.get("/v1/bookings", cookies=cobra)).status_code == 403


async def test_b_no_ve_ni_toca_los_turnos_de_a_y_el_404_es_identico(
    client, cookies_a, cookies_b, lav
):
    turno = await lav.turno(inicio=INICIO, ahora=HOY)
    caso = await _cancelado_con_sena(lav)
    marca = (
        await client.post(
            f"/v1/bookings/{turno.id}/incident-marks", json={"reason": "x"}, cookies=cookies_a
        )
    ).json()
    pedido = await en_revision(lav, inicio=INICIO + timedelta(hours=2), ahora=HOY)
    assert (await client.get("/v1/bookings", cookies=cookies_b)).json()["total"] == 0
    assert (await client.get("/v1/cancellations", cookies=cookies_b)).json()["total"] == 0
    assert (await client.get("/v1/bookings/counts", cookies=cookies_b)).json()["total"] == 0

    intentos = [
        ("GET", "/v1/bookings/{}", None, None, turno.id),
        ("POST", "/v1/bookings/{}/cancel", {"reason": "x"}, None, turno.id),
        ("POST", "/v1/bookings/{}/no-show", None, None, turno.id),
        ("POST", "/v1/bookings/{}/reschedule", {"start_at": INICIO.isoformat()}, None, turno.id),
        ("POST", "/v1/bookings/{}/incident-marks", {"reason": "x"}, None, turno.id),
        ("POST", "/v1/bookings/{}/approve", None, None, pedido.id),
        ("POST", "/v1/bookings/{}/reject", {"reason": "x"}, None, pedido.id),
        (
            "POST",
            "/v1/bookings/{}/confirm-deposit",
            {"payment_method_id": str(lav.efectivo), "amount_cents": SENA},
            {"Idempotency-Key": "k"},
            turno.id,
        ),
        ("DELETE", "/v1/incident-marks/{}", None, None, uuid.UUID(marca["id"])),
        (
            "POST",
            "/v1/cancellations/{}/resolve",
            {"status": "REPROGRAMADA", "reason": "x"},
            None,
            caso.id,
        ),
    ]
    for metodo, ruta, cuerpo, cabeceras, id_a in intentos:
        ajeno = await client.request(
            metodo, ruta.format(id_a), json=cuerpo, cookies=cookies_b, headers=cabeceras
        )
        nada = await client.request(
            metodo, ruta.format(uuid.uuid4()), json=cuerpo, cookies=cookies_b, headers=cabeceras
        )
        assert ajeno.status_code == nada.status_code == 404, (ruta, ajeno.text)
        assert ajeno.content == nada.content
    # B tampoco mueve el turno de A a un puesto suyo ni el de A a uno de A desde B.
    assert (await lav.turnos.get(turno.id)).status == BookingStatus.CONFIRMADO
    assert (await lav.turnos.get(pedido.id)).status == BookingStatus.PENDIENTE_REVISION


async def test_un_puesto_ajeno_al_reprogramar_es_404(client, cookies_a, db_session, lav):
    from app.tests.application._armado import armar  # noqa: PLC0415

    otro = await armar(db_session, "Lavadero C")
    turno = await lav.turno(inicio=INICIO, ahora=HOY)
    ajeno = await client.post(
        f"/v1/bookings/{turno.id}/reschedule",
        json={"resource_id": str(otro.puesto)},
        cookies=cookies_a,
    )
    nada = await client.post(
        f"/v1/bookings/{turno.id}/reschedule",
        json={"resource_id": str(uuid.uuid4())},
        cookies=cookies_a,
    )
    assert ajeno.status_code == nada.status_code == 404 and ajeno.content == nada.content
    assert (await lav.turnos.get(turno.id)).resource_id == lav.puesto
