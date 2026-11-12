"""Tester-aislamiento del PR 5.1 (agenda) por HTTP — FASE-5-CONTRATO §2, §4.1 y §12.

Escrito por quien NO implementó el 5.1. Suite rápida (SQLite): la red que se prueba acá es el
filtro por `tenant_id` de servicios y repositorios y la compuerta de permisos; RLS va en
`test_aislamiento_agenda_pg.py`.

1. **Matriz permiso × ruta** de lo nuevo, generada desde la app: toda ruta de `/v1/agenda` está
   en la matriz; sin su permiso → 403 `FORBIDDEN`, con solo ese permiso → pasa la compuerta.
   Suma el `PATCH /services/{id}` con reglas (`CATALOGO_EDITAR`) y `DELETE /resources/{id}`.
2. **Ids ajenos dentro del body o de la query**: el puesto de A en una apertura de B, el
   servicio de A con el tamaño propio de B (y al revés) en la disponibilidad → el mismo 404 que
   un id al azar.
3. **Lo de A no se filtra a B**: el bloqueo de B sobre el día de un turno de A no lo devuelve
   en `overlapping_bookings` (ni el nombre del cliente); el bloqueo de día completo de A no le
   quita horarios a B; la `Idempotency-Key` de A no es "duplicada" para B.
4. **`DELETE /resources/{id}` ajeno** con turnos futuros: 404 idéntico al inexistente, nunca el
   409 `IN_USE` (que diría que el puesto existe y está ocupado).
5. **`PATCH /services/{id}` ajeno** con reglas: 404 idéntico y las reglas de A no cambian.

Datos sintéticos (`@example.com`).
"""

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute

from app.domain.permissions import ALL_PERMISSIONS, Permission
from app.domain.roles import Role
from app.main import create_app
from app.tests.application._armado import Lavadero, armar_catalogo
from app.tests.conftest import make_profile, make_user, session_cookies

HOY = datetime.now(UTC)
#: Un lunes futuro dentro del horizonte por defecto (14 días).
LUNES = (HOY + timedelta(days=(7 - HOY.weekday()) % 7 or 7)).date()


def _codigo(r: Any) -> str:
    return r.json()["detail"]["code"]


def _sin_id(r: Any, *ids: Any) -> bytes:
    contenido = r.content
    for i in ids:
        contenido = contenido.replace(str(i).encode(), b"<id>")
    return contenido


@pytest_asyncio.fixture
async def a(db_session, tenant_a, owner) -> Lavadero:
    lv = await armar_catalogo(db_session, tenant_a.id, owner.id)
    await lv.catalogo.add_business_hours(1, time(9, 0), time(13, 0))
    return lv


@pytest_asyncio.fixture
async def b(db_session, tenant_b, owner_b) -> Lavadero:
    lv = await armar_catalogo(db_session, tenant_b.id, owner_b.id)
    await lv.catalogo.add_business_hours(1, time(9, 0), time(13, 0))
    return lv


def _dispo(lv: Lavadero, **cambios: Any) -> dict[str, Any]:
    return {
        "date": LUNES.isoformat(),
        "vehicle_size_id": str(lv.auto),
        "service_ids": [str(lv.lavado)],
        **cambios,
    }


# ── 1 · Matriz permiso × ruta ────────────────────────────────────────────────

#: (método, ruta) → permiso que la abre. La ruta lleva `{id}` donde va un id.
MATRIZ: dict[tuple[str, str], Permission] = {
    ("GET", "/v1/agenda/blocks"): Permission.AGENDA_VER,
    ("POST", "/v1/agenda/blocks"): Permission.TURNOS_GESTIONAR,
    ("DELETE", "/v1/agenda/blocks/{id}"): Permission.TURNOS_GESTIONAR,
    ("GET", "/v1/agenda/openings"): Permission.AGENDA_VER,
    ("POST", "/v1/agenda/openings"): Permission.TURNOS_GESTIONAR,
    ("DELETE", "/v1/agenda/openings/{id}"): Permission.TURNOS_GESTIONAR,
    ("GET", "/v1/agenda/availability"): Permission.AGENDA_VER,
    ("PATCH", "/v1/services/{id}"): Permission.CATALOGO_EDITAR,
    ("DELETE", "/v1/resources/{id}"): Permission.CATALOGO_EDITAR,
}


def test_la_matriz_cubre_todas_las_rutas_de_agenda():
    rutas = {
        (metodo, ruta.path)
        for ruta in create_app().routes
        if isinstance(ruta, APIRoute) and ruta.path.startswith("/v1/agenda")
        for metodo in ruta.methods
    }
    assert rutas, "no hay rutas de agenda"
    assert rutas <= set(MATRIZ), f"rutas de agenda sin permiso en la matriz: {rutas - set(MATRIZ)}"


async def _pedir(client, metodo: str, ruta: str, lv: Lavadero, cookies) -> Any:
    """Un pedido válido para la ruta (con ids propios): solo el permiso decide."""
    if ruta == "/v1/services/{id}":
        return await client.patch(
            f"/v1/services/{lv.lavado}",
            json={
                "schedule_rules": [{"weekday": 1, "window_start": "09:00", "last_start": "10:00"}]
            },
            cookies=cookies,
        )
    if ruta == "/v1/resources/{id}":
        return await client.delete(f"/v1/resources/{lv.puesto_2}", cookies=cookies)
    if ruta == "/v1/agenda/availability":
        return await client.get(ruta, params=_dispo(lv), cookies=cookies)
    url = ruta.replace("{id}", str(uuid.uuid4()))
    if metodo == "POST":
        return await client.post(url, json={"date": LUNES.isoformat()}, cookies=cookies)
    return await client.request(metodo, url, cookies=cookies)


async def _empleado(db_session, tenant, permisos: set[Permission]) -> dict[str, str]:
    perfil = await make_profile(db_session, tenant, f"Perfil {uuid.uuid4().hex[:8]}", permisos)
    user = await make_user(
        db_session,
        tenant,
        Role.STAFF,
        f"empleado-{uuid.uuid4().hex[:8]}@example.com",
        profile=perfil,
    )
    return session_cookies(user)


@pytest.mark.parametrize(("metodo", "ruta"), sorted(MATRIZ), ids=lambda x: str(x))
async def test_sin_el_permiso_es_403_y_con_solo_ese_pasa(
    client, db_session, tenant_a, a, metodo, ruta
):
    permiso = MATRIZ[(metodo, ruta)]
    sin = await _empleado(db_session, tenant_a, set(ALL_PERMISSIONS - {permiso}))
    r = await _pedir(client, metodo, ruta, a, sin)
    assert r.status_code == 403 and _codigo(r) == "FORBIDDEN", r.text
    solo = await _empleado(db_session, tenant_a, {permiso})
    r = await _pedir(client, metodo, ruta, a, solo)
    assert r.status_code != 403, r.text


async def test_settings_de_reservas_no_las_toca_un_staff_con_todos_los_permisos(
    client, db_session, tenant_a, a
):
    todos = await _empleado(db_session, tenant_a, set(ALL_PERMISSIONS))
    for cuerpo in (
        {"booking_horizon_days": 60},
        {"slot_step_min": 15},
        {"deposit_instructions": "Alias: demo.sintetico"},
    ):
        r = await client.patch("/v1/settings", json=cuerpo, cookies=todos)
        assert r.status_code == 403, r.text


# ── 2 · Ids ajenos en body y query ───────────────────────────────────────────


async def test_apertura_de_b_sobre_el_puesto_de_a_es_el_404_de_uno_inexistente(
    client, cookies_b, a, b
):
    azar = uuid.uuid4()
    ajeno = await client.post(
        "/v1/agenda/openings",
        json={"date": LUNES.isoformat(), "resource_id": str(a.puesto)},
        cookies=cookies_b,
    )
    nada = await client.post(
        "/v1/agenda/openings",
        json={"date": LUNES.isoformat(), "resource_id": str(azar)},
        cookies=cookies_b,
    )
    assert ajeno.status_code == nada.status_code == 404
    assert _sin_id(ajeno, a.puesto) == _sin_id(nada, azar)
    assert (await client.get("/v1/agenda/openings", cookies=cookies_b)).json()["total"] == 0


async def test_bloqueo_de_b_sobre_el_puesto_de_a_es_el_404_de_uno_inexistente(
    client, cookies_b, a, b
):
    azar = uuid.uuid4()
    ajeno = await client.post(
        "/v1/agenda/blocks",
        json={"date": LUNES.isoformat(), "resource_id": str(a.puesto)},
        cookies=cookies_b,
    )
    nada = await client.post(
        "/v1/agenda/blocks",
        json={"date": LUNES.isoformat(), "resource_id": str(azar)},
        cookies=cookies_b,
    )
    assert ajeno.status_code == nada.status_code == 404
    assert _sin_id(ajeno, a.puesto) == _sin_id(nada, azar)
    assert (await client.get("/v1/agenda/blocks", cookies=cookies_b)).json()["total"] == 0


@pytest.mark.parametrize("campo", ["service", "size"])
async def test_disponibilidad_de_b_con_un_id_de_a_mezclado_es_404(client, cookies_b, a, b, campo):
    """Un id ajeno mezclado con ids propios válidos: no alcanza con que el resto sea de B."""
    azar = uuid.uuid4()
    if campo == "service":
        ajeno_p = _dispo(b, service_ids=[str(b.lavado), str(a.lavado)])
        nada_p = _dispo(b, service_ids=[str(b.lavado), str(azar)])
        ajeno_id = a.lavado
    else:
        ajeno_p = _dispo(b, vehicle_size_id=str(a.auto))
        nada_p = _dispo(b, vehicle_size_id=str(azar))
        ajeno_id = a.auto
    ajeno = await client.get("/v1/agenda/availability", params=ajeno_p, cookies=cookies_b)
    nada = await client.get("/v1/agenda/availability", params=nada_p, cookies=cookies_b)
    assert ajeno.status_code == nada.status_code == 404, ajeno.text
    assert _sin_id(ajeno, ajeno_id) == _sin_id(nada, azar)


# ── 3 · Lo de A no se filtra a B ─────────────────────────────────────────────


async def test_el_bloqueo_de_b_no_devuelve_los_turnos_de_a_que_pisaria(
    client, cookies_a, cookies_b, a, b
):
    turno_a = await a.turno(inicio=datetime.combine(LUNES, time(13), tzinfo=UTC), ahora=HOY)
    # Control: el mismo bloqueo en A sí lo devuelve.
    propio = await client.post(
        "/v1/agenda/blocks", json={"date": LUNES.isoformat()}, cookies=cookies_a
    )
    assert [x["id"] for x in propio.json()["overlapping_bookings"]] == [str(turno_a.id)]
    r = await client.post("/v1/agenda/blocks", json={"date": LUNES.isoformat()}, cookies=cookies_b)
    assert r.status_code == 201, r.text
    assert r.json()["overlapping_bookings"] == []
    assert "Cliente Uno" not in r.text and turno_a.code not in r.text


async def test_lo_que_ocupa_y_bloquea_en_a_no_le_quita_horarios_a_b(
    client, cookies_a, cookies_b, a, b
):
    antes = (
        await client.get("/v1/agenda/availability", params=_dispo(b), cookies=cookies_b)
    ).json()
    assert antes["slots"], "control: B tiene horarios ese lunes"
    await a.turno(inicio=datetime.combine(LUNES, time(12), tzinfo=UTC), ahora=HOY)
    assert (
        await client.post("/v1/agenda/blocks", json={"date": LUNES.isoformat()}, cookies=cookies_a)
    ).status_code == 201
    a_dispo = (
        await client.get("/v1/agenda/availability", params=_dispo(a), cookies=cookies_a)
    ).json()
    assert a_dispo["slots"] == []  # control: en A el bloqueo pega
    despues = (
        await client.get("/v1/agenda/availability", params=_dispo(b), cookies=cookies_b)
    ).json()
    assert despues == antes
    # Y ningún puesto de A aparece entre los libres de B.
    puestos = {p for s in despues["slots"] for p in s["resource_ids"]}
    assert puestos <= {str(b.puesto), str(b.puesto_2)}


async def test_la_apertura_de_a_no_abre_horarios_en_b(client, cookies_a, cookies_b, a, b):
    domingo = LUNES + timedelta(days=6)
    cuerpo = {"date": domingo.isoformat(), "start_time": "10:00", "end_time": "12:00"}
    assert (
        await client.post("/v1/agenda/openings", json=cuerpo, cookies=cookies_a)
    ).status_code == 201
    en_a = await client.get(
        "/v1/agenda/availability", params=_dispo(a, date=domingo.isoformat()), cookies=cookies_a
    )
    assert en_a.json()["slots"], "control: la apertura abre en A"
    en_b = await client.get(
        "/v1/agenda/availability", params=_dispo(b, date=domingo.isoformat()), cookies=cookies_b
    )
    assert en_b.status_code == 200 and en_b.json()["slots"] == []


async def test_las_reglas_de_a_no_restringen_el_mismo_servicio_de_b(
    client, cookies_a, cookies_b, a, b
):
    antes = (
        await client.get("/v1/agenda/availability", params=_dispo(b), cookies=cookies_b)
    ).json()
    r = await client.patch(
        f"/v1/services/{a.lavado}",
        json={"schedule_rules": [{"weekday": 2, "window_start": "09:00", "last_start": "09:00"}]},
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    despues = (
        await client.get("/v1/agenda/availability", params=_dispo(b), cookies=cookies_b)
    ).json()
    assert despues == antes
    servicio_b = (await client.get(f"/v1/services/{b.lavado}", cookies=cookies_b)).json()
    assert servicio_b["schedule_rules"] == []


async def test_la_misma_idempotency_key_en_a_no_es_duplicada_para_b(
    client, cookies_a, cookies_b, a, b
):
    cabecera = {"Idempotency-Key": "agenda-misma-clave"}
    for ruta in ("/v1/agenda/blocks", "/v1/agenda/openings"):
        for cookies in (cookies_a, cookies_b):
            r = await client.post(
                ruta, json={"date": LUNES.isoformat()}, cookies=cookies, headers=cabecera
            )
            assert r.status_code == 201, (ruta, r.text)
        cabecera = {"Idempotency-Key": "agenda-misma-clave-2"}


# ── 4 · DELETE /resources ajeno con turnos futuros ───────────────────────────


async def test_anular_el_puesto_ocupado_de_a_desde_b_es_404_nunca_in_use(
    client, cookies_a, cookies_b, a, b
):
    await a.turno(inicio=HOY + timedelta(days=2), ahora=HOY, puesto=a.puesto_2)
    control = await client.delete(f"/v1/resources/{a.puesto_2}", cookies=cookies_a)
    assert control.status_code == 409 and _codigo(control) == "IN_USE"
    azar = uuid.uuid4()
    ajeno = await client.delete(f"/v1/resources/{a.puesto_2}", cookies=cookies_b)
    nada = await client.delete(f"/v1/resources/{azar}", cookies=cookies_b)
    assert ajeno.status_code == nada.status_code == 404
    assert _sin_id(ajeno, a.puesto_2) == _sin_id(nada, azar)
    # Y el de A sigue vivo.
    puestos = (await client.get("/v1/resources", cookies=cookies_a)).json()
    assert str(a.puesto_2) in {p["id"] for p in puestos["items"]}


# ── 5 · PATCH /services ajeno con reglas ─────────────────────────────────────


async def test_patch_de_reglas_sobre_el_servicio_de_a_desde_b_es_404_y_no_cambia_nada(
    client, cookies_a, cookies_b, a, b
):
    regla = [{"weekday": 1, "window_start": "09:00", "last_start": "10:00"}]
    assert (
        await client.patch(
            f"/v1/services/{a.lavado}", json={"schedule_rules": regla}, cookies=cookies_a
        )
    ).status_code == 200
    antes = (await client.get(f"/v1/services/{a.lavado}", cookies=cookies_a)).json()
    azar = uuid.uuid4()
    cuerpo = {"booking_mode": "SOLO_A_MANO", "schedule_rules": []}
    ajeno = await client.patch(f"/v1/services/{a.lavado}", json=cuerpo, cookies=cookies_b)
    nada = await client.patch(f"/v1/services/{azar}", json=cuerpo, cookies=cookies_b)
    assert ajeno.status_code == nada.status_code == 404
    assert _sin_id(ajeno, a.lavado) == _sin_id(nada, azar)
    despues = (await client.get(f"/v1/services/{a.lavado}", cookies=cookies_a)).json()
    assert despues == antes
    assert despues["booking_mode"] == "TURNERO" and len(despues["schedule_rules"]) == 1


# ── 6 · Entradas hostiles: nunca un 500 ──────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG T3-5.1: date=9999-12-31 desborda `day + timedelta(days=1)` en "
        "agenda_service.py (LocalRange.instants y availability) → OverflowError → 500. "
        "Debería ser 422."
    ),
)
@pytest.mark.parametrize("ruta", ["availability", "blocks", "openings"])
async def test_una_fecha_en_el_borde_del_calendario_es_422_no_500(client, cookies_a, a, ruta):
    if ruta == "availability":
        r = await client.get(
            "/v1/agenda/availability", params=_dispo(a, date="9999-12-31"), cookies=cookies_a
        )
    else:
        r = await client.post(f"/v1/agenda/{ruta}", json={"date": "9999-12-31"}, cookies=cookies_a)
    assert r.status_code == 422, r.text
