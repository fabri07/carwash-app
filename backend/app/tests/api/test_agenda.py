"""Agenda (PR 5.1): `/agenda/*`, los campos nuevos de `/settings` y `/services`, y el 409 de
`DELETE /resources/{id}` — FASE-5-CONTRATO §4.1, B2.

Datos sintéticos: el catálogo se arma por los servicios (`armar_catalogo`) sobre la misma sesión
que usa el cliente HTTP.
"""

import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
import pytest_asyncio

from app.domain.enums import BookingStatus
from app.domain.permissions import ALL_PERMISSIONS, Permission
from app.domain.roles import Role
from app.tests.application._armado import Lavadero, armar_catalogo
from app.tests.conftest import make_profile, make_user, session_cookies

#: Un lunes futuro: la agenda ofrece desde hoy hasta el horizonte.
HOY = datetime.now(UTC)
LUNES = (HOY + timedelta(days=(7 - HOY.weekday()) % 7 or 7)).date()


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


def _disponibilidad(lav: Lavadero, dia=LUNES) -> dict:
    return {
        "date": dia.isoformat(),
        "vehicle_size_id": str(lav.auto),
        "service_ids": [str(lav.lavado)],
    }


# ── Bloqueos y aperturas ──────────────────────────────────────────────────────


async def test_crear_listar_y_anular_un_bloqueo_con_los_turnos_que_pisa(client, cookies_a, lav):
    turno = await lav.turno(
        inicio=datetime.combine(LUNES, time(13), tzinfo=UTC), ahora=HOY
    )  # 10:00 en Córdoba
    r = await client.post(
        "/v1/agenda/blocks",
        json={
            "date": LUNES.isoformat(),
            "start_time": "09:00",
            "end_time": "12:00",
            "resource_id": str(lav.puesto),
            "reason": "Mantenimiento",
            "tenant_id": str(uuid.uuid4()),  # se ignora
        },
        cookies=cookies_a,
    )
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    assert cuerpo["block"]["reason"] == "Mantenimiento"
    assert cuerpo["block"]["starts_at"].startswith(f"{LUNES.isoformat()}T12:00")  # 09 local
    assert [b["id"] for b in cuerpo["overlapping_bookings"]] == [str(turno.id)]
    assert cuerpo["overlapping_bookings"][0]["customer_name"] == "Cliente Uno"
    assert cuerpo["overlapping_bookings"][0]["status"] == BookingStatus.CONFIRMADO.value

    lista = (await client.get("/v1/agenda/blocks", cookies=cookies_a)).json()
    assert lista["total"] == 1 and lista["items"][0]["id"] == cuerpo["block"]["id"]
    borrar = await client.delete(f"/v1/agenda/blocks/{cuerpo['block']['id']}", cookies=cookies_a)
    assert borrar.status_code == 204
    assert (await client.get("/v1/agenda/blocks", cookies=cookies_a)).json()["total"] == 0
    assert (await lav.turnos.get(turno.id)).status == BookingStatus.CONFIRMADO


async def test_un_bloqueo_de_dia_completo_sin_puesto(client, cookies_a, lav):
    r = await client.post("/v1/agenda/blocks", json={"date": LUNES.isoformat()}, cookies=cookies_a)
    assert r.status_code == 201, r.text
    assert r.json()["block"]["resource_id"] is None
    assert r.json()["block"]["reason"] == "Bloqueo operativo"
    dispo = await client.get(
        "/v1/agenda/availability", params=_disponibilidad(lav), cookies=cookies_a
    )
    assert dispo.status_code == 200 and dispo.json()["slots"] == []


async def test_crear_listar_y_anular_una_apertura(client, cookies_a, lav):
    domingo = LUNES + timedelta(days=6)
    vacio = await client.get(
        "/v1/agenda/availability", params=_disponibilidad(lav, domingo), cookies=cookies_a
    )
    assert vacio.json()["slots"] == []
    r = await client.post(
        "/v1/agenda/openings",
        json={"date": domingo.isoformat(), "start_time": "10:00", "end_time": "12:00"},
        cookies=cookies_a,
    )
    assert r.status_code == 201, r.text
    assert r.json()["reason"] == "Apertura especial"
    dispo = (
        await client.get(
            "/v1/agenda/availability", params=_disponibilidad(lav, domingo), cookies=cookies_a
        )
    ).json()
    assert len(dispo["slots"]) == 3
    assert dispo["slots"][0]["resource_ids"] == [str(lav.puesto), str(lav.puesto_2)]
    lista = (await client.get("/v1/agenda/openings", cookies=cookies_a)).json()
    assert lista["total"] == 1
    assert (
        await client.delete(f"/v1/agenda/openings/{r.json()['id']}", cookies=cookies_a)
    ).status_code == 204
    assert (await client.get("/v1/agenda/openings", cookies=cookies_a)).json()["total"] == 0


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"start_time": "12:00", "end_time": "09:00"},
        {"end_date": "2000-01-01"},
        {"start_time": "09:00Z"},
        {"reason": "x" * 161},
    ],
)
async def test_un_rango_invalido_es_422(client, cookies_a, lav, cuerpo):
    for ruta in ("/v1/agenda/blocks", "/v1/agenda/openings"):
        r = await client.post(ruta, json={"date": LUNES.isoformat(), **cuerpo}, cookies=cookies_a)
        assert r.status_code == 422, (ruta, r.text)


async def test_un_bloqueo_con_puesto_inexistente_es_404(client, cookies_a, lav):
    r = await client.post(
        "/v1/agenda/blocks",
        json={"date": LUNES.isoformat(), "resource_id": str(uuid.uuid4())},
        cookies=cookies_a,
    )
    assert r.status_code == 404 and _codigo(r) == "NOT_FOUND"


async def test_el_reenvio_con_la_misma_clave_es_duplicate(client, cookies_a, lav):
    cabecera = {"Idempotency-Key": "bloqueo-1"}
    cuerpo = {"date": LUNES.isoformat()}
    primero = await client.post(
        "/v1/agenda/blocks", json=cuerpo, cookies=cookies_a, headers=cabecera
    )
    assert primero.status_code == 201
    otra = await client.post("/v1/agenda/blocks", json=cuerpo, cookies=cookies_a, headers=cabecera)
    assert otra.status_code == 409 and _codigo(otra) == "DUPLICATE_IDEMPOTENT"


# ── Disponibilidad ────────────────────────────────────────────────────────────


async def test_la_disponibilidad_devuelve_inicios_fin_y_zona(client, cookies_a, lav):
    r = await client.get("/v1/agenda/availability", params=_disponibilidad(lav), cookies=cookies_a)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["timezone"] == "America/Argentina/Cordoba" and cuerpo["duration_min"] == 60
    primero = cuerpo["slots"][0]
    assert primero["start_at"].startswith(f"{LUNES.isoformat()}T12:00")
    assert primero["end_at"].startswith(f"{LUNES.isoformat()}T13:00")
    assert len(cuerpo["slots"]) == 7


async def test_la_disponibilidad_con_un_servicio_sin_precio_es_422(client, cookies_a, lav):
    from app.domain.enums import PricingMode  # noqa: PLC0415

    sin_precio = await lav.catalogo.create_service("Pulido", PricingMode.PRECIO_FIJO)
    params = {**_disponibilidad(lav), "service_ids": [str(sin_precio.id)]}
    r = await client.get("/v1/agenda/availability", params=params, cookies=cookies_a)
    assert r.status_code == 422 and _codigo(r) == "VALIDATION_ERROR"


async def test_la_disponibilidad_pide_al_menos_un_servicio(client, cookies_a, lav):
    params = {"date": LUNES.isoformat(), "vehicle_size_id": str(lav.auto)}
    r = await client.get("/v1/agenda/availability", params=params, cookies=cookies_a)
    assert r.status_code == 422


# ── Permisos (§2) y aislamiento (B2) ──────────────────────────────────────────


async def test_agenda_ver_lee_pero_no_bloquea(client, db_session, tenant_a, lav):
    solo_ver = await _empleado(db_session, tenant_a, "Solo ver", {Permission.AGENDA_VER})
    assert (await client.get("/v1/agenda/blocks", cookies=solo_ver)).status_code == 200
    assert (await client.get("/v1/agenda/openings", cookies=solo_ver)).status_code == 200
    dispo = await client.get(
        "/v1/agenda/availability", params=_disponibilidad(lav), cookies=solo_ver
    )
    assert dispo.status_code == 200
    r = await client.post("/v1/agenda/blocks", json={"date": LUNES.isoformat()}, cookies=solo_ver)
    assert r.status_code == 403 and _codigo(r) == "FORBIDDEN"


async def test_sin_agenda_ver_no_lee_y_turnos_gestionar_escribe(client, db_session, tenant_a, lav):
    sin_agenda = await _empleado(
        db_session, tenant_a, "Sin agenda", set(ALL_PERMISSIONS - {Permission.AGENDA_VER})
    )
    assert (await client.get("/v1/agenda/blocks", cookies=sin_agenda)).status_code == 403
    creado = await client.post(
        "/v1/agenda/openings", json={"date": LUNES.isoformat()}, cookies=sin_agenda
    )
    assert creado.status_code == 201, creado.text


async def test_b_no_ve_ni_anula_lo_de_a_y_el_404_es_identico(client, cookies_a, cookies_b, lav):
    bloqueo = (
        await client.post("/v1/agenda/blocks", json={"date": LUNES.isoformat()}, cookies=cookies_a)
    ).json()["block"]
    apertura = (
        await client.post(
            "/v1/agenda/openings", json={"date": LUNES.isoformat()}, cookies=cookies_a
        )
    ).json()
    for ruta, id_ in (("blocks", bloqueo["id"]), ("openings", apertura["id"])):
        ajeno = await client.delete(f"/v1/agenda/{ruta}/{id_}", cookies=cookies_b)
        nada = await client.delete(f"/v1/agenda/{ruta}/{uuid.uuid4()}", cookies=cookies_b)
        assert ajeno.status_code == nada.status_code == 404
        assert ajeno.content == nada.content
        assert (await client.get(f"/v1/agenda/{ruta}", cookies=cookies_b)).json()["total"] == 0
        assert (await client.get(f"/v1/agenda/{ruta}", cookies=cookies_a)).json()["total"] == 1
    # La disponibilidad de B con el tamaño o el servicio de A: el mismo 404 que uno inexistente.
    ajeno = await client.get(
        "/v1/agenda/availability", params=_disponibilidad(lav), cookies=cookies_b
    )
    params = {**_disponibilidad(lav), "vehicle_size_id": str(uuid.uuid4())}
    nada = await client.get("/v1/agenda/availability", params=params, cookies=cookies_b)
    assert ajeno.status_code == nada.status_code == 404 and ajeno.content == nada.content
    # Un bloqueo de B sobre el puesto de A tampoco entra.
    cruzado = await client.post(
        "/v1/agenda/blocks",
        json={"date": LUNES.isoformat(), "resource_id": str(lav.puesto)},
        cookies=cookies_b,
    )
    assert cruzado.status_code == 404


# ── /settings: horizonte, paso e instrucciones (§3.4) ────────────────────────


async def test_settings_trae_los_defaults_de_reservas_y_el_owner_los_edita(client, cookies_a, lav):
    s = (await client.get("/v1/settings", cookies=cookies_a)).json()
    assert (s["booking_horizon_days"], s["slot_step_min"], s["deposit_instructions"]) == (
        14,
        30,
        None,
    )
    r = await client.patch(
        "/v1/settings",
        json={
            "booking_horizon_days": 30,
            "slot_step_min": 15,
            "deposit_instructions": "  Alias: lavadero.demo  ",
        },
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    s = r.json()
    assert (s["booking_horizon_days"], s["slot_step_min"]) == (30, 15)
    assert s["deposit_instructions"] == "Alias: lavadero.demo"
    vacio = await client.patch(
        "/v1/settings", json={"deposit_instructions": "   "}, cookies=cookies_a
    )
    assert vacio.json()["deposit_instructions"] is None


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"booking_horizon_days": 0},
        {"booking_horizon_days": 91},
        {"slot_step_min": 4},
        {"slot_step_min": 121},
        {"booking_horizon_days": None},
        {"slot_step_min": None},
        {"deposit_instructions": "x" * 501},
    ],
)
async def test_settings_de_reservas_fuera_de_rango_es_422(client, cookies_a, lav, cuerpo):
    r = await client.patch("/v1/settings", json=cuerpo, cookies=cookies_a)
    assert r.status_code == 422, r.text


async def test_settings_de_reservas_son_solo_del_owner(client, db_session, tenant_a, lav):
    encargado = await _empleado(db_session, tenant_a, "Encargado", set(ALL_PERMISSIONS))
    r = await client.patch("/v1/settings", json={"slot_step_min": 15}, cookies=encargado)
    assert r.status_code == 403


# ── /services: modo de reserva y reglas (§3.1–3.2) ───────────────────────────


async def test_un_servicio_nace_en_turnero_sin_reglas_y_se_edita(client, cookies_a, lav):
    s = (await client.get(f"/v1/services/{lav.lavado}", cookies=cookies_a)).json()
    assert s["booking_mode"] == "TURNERO" and s["schedule_rules"] == []
    r = await client.patch(
        f"/v1/services/{lav.lavado}",
        json={
            "booking_mode": "SOLO_A_MANO",
            "schedule_rules": [
                {"weekday": 6, "window_start": "09:00", "last_start": "16:00"},
                {"weekday": 5, "window_start": "14:00", "last_start": "16:00"},
            ],
        },
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["booking_mode"] == "SOLO_A_MANO"
    assert [(x["weekday"], x["window_start"]) for x in s["schedule_rules"]] == [
        (5, "14:00:00"),
        (6, "09:00:00"),
    ]
    sin_reglas = await client.patch(
        f"/v1/services/{lav.lavado}", json={"schedule_rules": []}, cookies=cookies_a
    )
    assert sin_reglas.json()["schedule_rules"] == []
    assert sin_reglas.json()["booking_mode"] == "SOLO_A_MANO"


async def test_un_servicio_se_crea_con_modo_y_reglas(client, cookies_a, lav):
    r = await client.post(
        "/v1/services",
        json={
            "name": "Lavado de camión",
            "pricing_mode": "PRECIO_FIJO",
            "prices": [
                {"vehicle_size_id": str(lav.auto), "price_cents": 1_000_000, "duration_min": 60},
                {"vehicle_size_id": str(lav.suv), "price_cents": 1_500_000, "duration_min": 90},
            ],
            "booking_mode": "SOLO_A_MANO",
            "schedule_rules": [{"weekday": 5, "window_start": "08:00", "last_start": "10:00"}],
        },
        cookies=cookies_a,
    )
    assert r.status_code == 201, r.text
    assert r.json()["booking_mode"] == "SOLO_A_MANO"
    assert r.json()["schedule_rules"][0]["last_start"] == "10:00:00"


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"booking_mode": None},
        {"booking_mode": "A_VECES"},
        {"schedule_rules": None},
        {"schedule_rules": [{"weekday": 8, "window_start": "09:00", "last_start": "10:00"}]},
        {"schedule_rules": [{"weekday": 1, "window_start": "11:00", "last_start": "10:00"}]},
        {"schedule_rules": [{"weekday": 1, "window_start": "09:00Z", "last_start": "10:00"}]},
    ],
)
async def test_modo_o_reglas_invalidas_son_422(client, cookies_a, lav, cuerpo):
    r = await client.patch(f"/v1/services/{lav.lavado}", json=cuerpo, cookies=cookies_a)
    assert r.status_code == 422, r.text


# ── DELETE /resources con turnos futuros (§4.1) ──────────────────────────────


async def test_no_se_anula_un_puesto_con_turnos_futuros(client, cookies_a, lav):
    await lav.turno(inicio=HOY + timedelta(days=2), ahora=HOY, puesto=lav.puesto_2)
    r = await client.delete(f"/v1/resources/{lav.puesto_2}", cookies=cookies_a)
    assert r.status_code == 409 and _codigo(r) == "IN_USE"
    assert (
        await client.delete(f"/v1/resources/{lav.puesto}", cookies=cookies_a)
    ).status_code == 204
