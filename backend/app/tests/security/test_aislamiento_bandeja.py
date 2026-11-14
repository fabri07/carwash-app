"""Tester-aislamiento del PR 5.3 (bandeja del dueño) por HTTP — FASE-5-CONTRATO §2, §4.1 y §14.

Escrito por quien NO implementó el 5.3. Suite rápida (SQLite): la red que se prueba acá es el
filtro por `tenant_id` de servicios y repositorios y la compuerta de permisos; RLS y carreras van
en `test_aislamiento_bandeja_pg.py`. Lo que ya prueba `tests/api/test_bandeja.py` (404 idéntico de
10 rutas con owner de B, 403 de un permiso por vez) no se repite: acá va lo que le falta.

1. **Matriz permiso × ruta generada desde la app**: toda ruta de `/v1/bookings`,
   `/v1/cancellations` y `/v1/incident-marks` está en la matriz (una ruta nueva sin permiso
   declarado rompe el test); sin su permiso → 403 `FORBIDDEN`; con solo ese permiso → pasa la
   compuerta; sin sesión (o con cookie basura) → 401, nunca 200/404.
2. **Ids ajenos dentro del body**: el medio de pago de A en la seña o la devolución de B, el
   puesto de A al reprogramar un turno de B → el mismo 404 que un id al azar; no se cobra nada.
3. **El 404 de B es idéntico al inexistente para TODA ruta con `{id}`** (no solo las 10 de la
   bandeja) y no depende de lo que el recurso de A tenga: estado, seña, caso abierto.
4. **Idempotencia de `confirm-deposit`**: reenvío con la misma clave = mismo turno y un solo pago;
   la clave de A no es "duplicada" para B ni le devuelve el turno de A; la misma clave sobre otro
   turno propio no cobra dos veces.
5. **Lo de A no se filtra a B**: contador, listas y detalle; un staff sin AGENDA_VER no lee por las
   rutas de acción; `DELETE /incident-marks/{id}` de B no anula la marca de A.

Datos sintéticos (`@example.com`).
"""

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute

from app.domain.enums import BookingStatus
from app.domain.permissions import ALL_PERMISSIONS, Permission
from app.domain.roles import Role
from app.main import create_app
from app.tests.application._armado import SENA, Lavadero, armar_catalogo
from app.tests.application._bandeja import en_revision
from app.tests.conftest import make_profile, make_user, session_cookies

HOY = datetime.now(UTC)
#: Un lunes futuro a las 10:00 de Córdoba (13:00 UTC), con horario de 09 a 13.
LUNES = (HOY + timedelta(days=(7 - HOY.weekday()) % 7 or 7)).date()
INICIO = datetime.combine(LUNES, time(13), tzinfo=UTC)

PREFIJOS = ("/v1/bookings", "/v1/cancellations", "/v1/incident-marks")


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


# ── 1 · Matriz permiso × ruta ────────────────────────────────────────────────

V, G, C = Permission.AGENDA_VER, Permission.TURNOS_GESTIONAR, Permission.COBROS_REGISTRAR
MATRIZ: dict[tuple[str, str], Permission] = {
    ("GET", "/v1/bookings"): V,
    ("GET", "/v1/bookings/counts"): V,
    ("GET", "/v1/bookings/{id}"): V,
    ("POST", "/v1/bookings/{id}/confirm-deposit"): C,
    ("POST", "/v1/bookings/{id}/approve"): G,
    ("POST", "/v1/bookings/{id}/reject"): G,
    ("POST", "/v1/bookings/{id}/cancel"): G,
    ("POST", "/v1/bookings/{id}/no-show"): G,
    ("POST", "/v1/bookings/{id}/reschedule"): G,
    ("POST", "/v1/bookings/{id}/incident-marks"): G,
    ("DELETE", "/v1/incident-marks/{id}"): G,
    ("GET", "/v1/cancellations"): V,
    ("POST", "/v1/cancellations/{id}/resolve"): C,
}

#: Cuerpos mínimos válidos por ruta: así lo único que puede rechazar el pedido es el permiso.
CUERPOS: dict[str, Any] = {
    "confirm-deposit": {"payment_method_id": str(uuid.uuid4())},
    "reject": {"reason": "x"},
    "cancel": {"reason": "x"},
    "reschedule": {"resource_id": str(uuid.uuid4())},
    "incident-marks": {"reason": "x"},
    "resolve": {"status": "REPROGRAMADA", "reason": "x"},
}


def _rutas_de_bandeja() -> set[tuple[str, str]]:
    return {
        (metodo, ruta.path)
        for ruta in create_app().routes
        if isinstance(ruta, APIRoute) and ruta.path.startswith(PREFIJOS)
        for metodo in ruta.methods
        if metodo not in {"HEAD", "OPTIONS"}
    }


def test_la_matriz_cubre_todas_las_rutas_de_la_bandeja():
    rutas = _rutas_de_bandeja()
    assert rutas, "no hay rutas de bandeja"
    assert rutas == set(MATRIZ), (
        f"sin permiso en la matriz: {rutas - set(MATRIZ)}; "
        f"en la matriz pero sin ruta: {set(MATRIZ) - rutas}"
    )


async def _pedir(client, metodo: str, ruta: str, cookies) -> Any:
    url = ruta.replace("{id}", str(uuid.uuid4()))
    cuerpo = CUERPOS.get(ruta.rsplit("/", 1)[-1]) if metodo == "POST" else None
    cabeceras = {"Idempotency-Key": f"k-{uuid.uuid4()}"} if metodo == "POST" else None
    return await client.request(metodo, url, json=cuerpo, cookies=cookies, headers=cabeceras)


@pytest.mark.parametrize(("metodo", "ruta"), sorted(MATRIZ), ids=lambda x: str(x))
async def test_sin_el_permiso_es_403_y_con_solo_ese_pasa(
    client, db_session, tenant_a, a, metodo, ruta
):
    permiso = MATRIZ[(metodo, ruta)]
    sin = await _empleado(db_session, tenant_a, set(ALL_PERMISSIONS - {permiso}))
    r = await _pedir(client, metodo, ruta, sin)
    assert r.status_code == 403 and _codigo(r) == "FORBIDDEN", r.text
    solo = await _empleado(db_session, tenant_a, {permiso})
    r = await _pedir(client, metodo, ruta, solo)
    assert r.status_code != 403 and r.status_code != 401, r.text


@pytest.mark.parametrize(("metodo", "ruta"), sorted(MATRIZ), ids=lambda x: str(x))
async def test_sin_sesion_o_con_cookie_basura_es_401(client, a, metodo, ruta):
    for cookies in (None, {"access_token": "no-es-un-jwt"}, {"access_token": ""}):
        r = await _pedir(client, metodo, ruta, cookies)
        assert r.status_code == 401, (cookies, r.status_code, r.text)


async def test_un_perfil_vacio_no_pasa_ninguna_ruta(client, db_session, tenant_a, a):
    vacio = await _empleado(db_session, tenant_a, set())
    for metodo, ruta in sorted(MATRIZ):
        r = await _pedir(client, metodo, ruta, vacio)
        assert r.status_code == 403, (metodo, ruta, r.status_code)


async def test_la_sesion_de_un_empleado_de_a_no_abre_nada_de_b(
    client, db_session, tenant_a, tenant_b, a, b
):
    """Un empleado de A con TODOS los permisos de la bandeja pega contra un turno de B: 404."""
    turno_b = await b.turno(inicio=INICIO, ahora=HOY)
    todos = await _empleado(db_session, tenant_a, {V, G, C})
    for metodo, ruta in sorted(MATRIZ):
        if "{id}" not in ruta or ruta.startswith("/v1/incident-marks"):
            continue
        url = ruta.replace("{id}", str(turno_b.id))
        cuerpo = CUERPOS.get(ruta.rsplit("/", 1)[-1]) if metodo == "POST" else None
        r = await client.request(
            metodo,
            url,
            json=cuerpo,
            cookies=todos,
            headers={"Idempotency-Key": f"k-{uuid.uuid4()}"},
        )
        assert r.status_code == 404, (metodo, ruta, r.status_code, r.text)
    assert (await b.turnos.get(turno_b.id)).status == BookingStatus.CONFIRMADO


# ── 2 · Ids ajenos dentro del body ───────────────────────────────────────────


async def test_medio_de_pago_de_a_en_la_sena_de_b_es_404_y_no_cobra(client, cookies_b, a, b):
    turno = await b.turno_con_sena(inicio=INICIO, ahora=HOY)
    azar = uuid.uuid4()
    ajeno = await client.post(
        f"/v1/bookings/{turno.id}/confirm-deposit",
        json={"payment_method_id": str(a.efectivo)},
        cookies=cookies_b,
        headers={"Idempotency-Key": "k-ajeno"},
    )
    nada = await client.post(
        f"/v1/bookings/{turno.id}/confirm-deposit",
        json={"payment_method_id": str(azar)},
        cookies=cookies_b,
        headers={"Idempotency-Key": "k-nada"},
    )
    assert ajeno.status_code == nada.status_code == 404, (ajeno.text, nada.text)
    assert _sin_id(ajeno, a.efectivo, turno.id) == _sin_id(nada, azar, turno.id)
    assert (await b.turnos.get(turno.id)).status == BookingStatus.PENDIENTE_SENA
    detalle = (await client.get(f"/v1/bookings/{turno.id}", cookies=cookies_b)).json()
    assert detalle["payments"] == []


async def test_medio_de_pago_de_a_en_la_devolucion_de_b_es_404_y_el_caso_sigue_abierto(
    client, cookies_b, b, a
):
    turno = await b.turno_con_sena(inicio=INICIO, ahora=HOY)
    await b.turnos.confirm_deposit(turno.id, b.pago(SENA), now=HOY)
    caso = await b.turnos.cancel_operational(turno.id, requested_at=HOY, reason="Lluvia")
    ajeno = await client.post(
        f"/v1/cancellations/{caso.id}/resolve",
        json={
            "status": "DEVUELTA",
            "reason": "x",
            "refund": {"payment_method_id": str(a.efectivo), "amount_cents": SENA},
        },
        cookies=cookies_b,
        headers={"Idempotency-Key": "k-dev"},
    )
    assert ajeno.status_code == 404, ajeno.text
    lista = (await client.get("/v1/cancellations", cookies=cookies_b)).json()
    assert lista["total"] == 1
    detalle = (await client.get(f"/v1/bookings/{turno.id}", cookies=cookies_b)).json()
    assert [p["amount_cents"] for p in detalle["payments"]] == [SENA]  # solo la seña


async def test_puesto_de_a_al_reprogramar_el_turno_de_b_es_404(client, cookies_b, a, b):
    turno = await b.turno(inicio=INICIO, ahora=HOY)
    azar = uuid.uuid4()
    ajeno = await client.post(
        f"/v1/bookings/{turno.id}/reschedule",
        json={"resource_id": str(a.puesto)},
        cookies=cookies_b,
    )
    nada = await client.post(
        f"/v1/bookings/{turno.id}/reschedule", json={"resource_id": str(azar)}, cookies=cookies_b
    )
    assert ajeno.status_code == nada.status_code == 404, ajeno.text
    assert _sin_id(ajeno, a.puesto, turno.id) == _sin_id(nada, azar, turno.id)
    assert (await b.turnos.get(turno.id)).resource_id == b.puesto


# ── 3 · 404 idéntico, también con lo que A tiene adentro ─────────────────────


async def test_el_404_de_b_no_cambia_con_el_estado_del_recurso_de_a(client, cookies_b, a, b):
    """Si el cuerpo del error variara con el estado (409 `CONFLICT`, 422 de guarda) delataría que
    el turno existe: cada acción, contra un turno de A en el estado que más tienta a responder
    otra cosa, es el 404 del id al azar."""
    confirmado = await a.turno(inicio=INICIO, ahora=HOY)
    cancelado = await a.turno(inicio=INICIO + timedelta(hours=1), ahora=HOY)
    await a.turnos.cancel_operational(cancelado.id, requested_at=HOY, reason="Lluvia")
    pasado = await a.turno(inicio=INICIO + timedelta(hours=2), ahora=HOY)
    revision = await en_revision(a, inicio=INICIO + timedelta(hours=3), ahora=HOY)
    marca = await a.turnos.get(confirmado.id)  # relleno: solo para tener un id vivo
    assert marca is not None
    acciones = [
        ("approve", None),
        ("reject", {"reason": "x"}),
        ("cancel", {"reason": "x"}),
        ("no-show", None),
        ("reschedule", {"start_at": INICIO.isoformat()}),
        ("incident-marks", {"reason": "x"}),
        ("confirm-deposit", {"payment_method_id": str(b.efectivo)}),
    ]
    for turno in (confirmado, cancelado, pasado, revision):
        for accion, cuerpo in acciones:
            azar = uuid.uuid4()
            ajeno = await client.post(
                f"/v1/bookings/{turno.id}/{accion}",
                json=cuerpo,
                cookies=cookies_b,
                headers={"Idempotency-Key": f"k-{uuid.uuid4()}"},
            )
            nada = await client.post(
                f"/v1/bookings/{azar}/{accion}",
                json=cuerpo,
                cookies=cookies_b,
                headers={"Idempotency-Key": f"k-{uuid.uuid4()}"},
            )
            assert ajeno.status_code == nada.status_code == 404, (accion, ajeno.text)
            assert _sin_id(ajeno, turno.id) == _sin_id(nada, azar), accion


async def test_id_mal_formado_es_422_igual_para_todos(client, cookies_a, a):
    for ruta in ("/v1/bookings/{}", "/v1/bookings/{}/approve", "/v1/incident-marks/{}"):
        r = await client.request(
            "DELETE" if "incident" in ruta else ("GET" if ruta.endswith("{}") else "POST"),
            ruta.format("no-es-uuid"),
            cookies=cookies_a,
        )
        assert r.status_code == 422, (ruta, r.status_code)


async def test_el_listado_de_b_no_se_deja_filtrar_por_estado_ni_dia_de_a(client, cookies_b, a, b):
    await a.turno(inicio=INICIO, ahora=HOY)
    await a.turno_con_sena(inicio=INICIO + timedelta(hours=1), ahora=HOY)
    await en_revision(a, inicio=INICIO + timedelta(hours=2), ahora=HOY)
    params = [
        {},
        {"from": LUNES.isoformat(), "to": LUNES.isoformat()},
        {"status": "PENDIENTE_REVISION"},
        {"status": "CONFIRMADO", "limit": 100},
    ]
    for p in params:
        r = await client.get("/v1/bookings", params=p, cookies=cookies_b)
        assert r.status_code == 200 and r.json()["total"] == 0 and r.json()["items"] == [], p
    contador = (await client.get("/v1/bookings/counts", cookies=cookies_b)).json()
    assert contador == {"pending_deposit": 0, "in_review": 0, "deposit_cases": 0, "total": 0}


# ── 4 · Idempotencia de confirm-deposit ──────────────────────────────────────


async def test_confirm_deposit_con_la_misma_clave_cobra_una_sola_vez(client, cookies_a, a):
    turno = await a.turno_con_sena(inicio=INICIO, ahora=HOY)
    url = f"/v1/bookings/{turno.id}/confirm-deposit"
    cuerpo = {"payment_method_id": str(a.efectivo)}
    cab = {"Idempotency-Key": "k-unica"}
    primero = await client.post(url, json=cuerpo, cookies=cookies_a, headers=cab)
    assert primero.status_code == 200, primero.text
    for _ in range(3):
        otra = await client.post(url, json=cuerpo, cookies=cookies_a, headers=cab)
        assert otra.status_code == 200, otra.text
        assert otra.json()["booking"]["id"] == primero.json()["booking"]["id"]
        assert (
            otra.json()["booking"]["deposit_paid_cents"]
            == primero.json()["booking"]["deposit_paid_cents"]
        )
    detalle = (await client.get(f"/v1/bookings/{turno.id}", cookies=cookies_a)).json()
    assert len(detalle["payments"]) == 1


async def test_confirm_deposit_sin_la_clave_o_con_clave_vacia_no_cobra(client, cookies_a, a):
    turno = await a.turno_con_sena(inicio=INICIO, ahora=HOY)
    url = f"/v1/bookings/{turno.id}/confirm-deposit"
    cuerpo = {"payment_method_id": str(a.efectivo)}
    sin = await client.post(url, json=cuerpo, cookies=cookies_a)
    vacia = await client.post(url, json=cuerpo, cookies=cookies_a, headers={"Idempotency-Key": ""})
    larga = await client.post(
        url, json=cuerpo, cookies=cookies_a, headers={"Idempotency-Key": "k" * 201}
    )
    assert sin.status_code == vacia.status_code == larga.status_code == 422
    detalle = (await client.get(f"/v1/bookings/{turno.id}", cookies=cookies_a)).json()
    assert detalle["payments"] == [] and detalle["status"] == "PENDIENTE_SEÑA"


async def test_la_clave_de_la_sena_de_a_no_es_duplicada_para_b_ni_le_da_el_turno_de_a(
    client, cookies_a, cookies_b, a, b
):
    turno_a = await a.turno_con_sena(inicio=INICIO, ahora=HOY)
    turno_b = await b.turno_con_sena(inicio=INICIO, ahora=HOY)
    cab = {"Idempotency-Key": "k-compartida"}
    ra = await client.post(
        f"/v1/bookings/{turno_a.id}/confirm-deposit",
        json={"payment_method_id": str(a.efectivo)},
        cookies=cookies_a,
        headers=cab,
    )
    assert ra.status_code == 200, ra.text
    rb = await client.post(
        f"/v1/bookings/{turno_b.id}/confirm-deposit",
        json={"payment_method_id": str(b.efectivo)},
        cookies=cookies_b,
        headers=cab,
    )
    assert rb.status_code == 200, rb.text
    assert rb.json()["booking"]["id"] == str(turno_b.id)
    assert str(turno_a.id) not in rb.text
    assert (await b.turnos.get(turno_b.id)).status == BookingStatus.CONFIRMADO
    # y B contra el turno de A con esa misma clave sigue siendo el 404 de siempre.
    cruzada = await client.post(
        f"/v1/bookings/{turno_a.id}/confirm-deposit",
        json={"payment_method_id": str(b.efectivo)},
        cookies=cookies_b,
        headers=cab,
    )
    assert cruzada.status_code == 404, cruzada.text


async def test_la_misma_clave_sobre_otro_turno_propio_no_cobra_dos_veces(client, cookies_a, a):
    uno = await a.turno_con_sena(inicio=INICIO, ahora=HOY)
    dos = await a.turno_con_sena(inicio=INICIO + timedelta(hours=2), ahora=HOY)
    cab = {"Idempotency-Key": "k-reusada"}
    cuerpo = {"payment_method_id": str(a.efectivo)}
    r1 = await client.post(
        f"/v1/bookings/{uno.id}/confirm-deposit", json=cuerpo, cookies=cookies_a, headers=cab
    )
    assert r1.status_code == 200, r1.text
    r2 = await client.post(
        f"/v1/bookings/{dos.id}/confirm-deposit", json=cuerpo, cookies=cookies_a, headers=cab
    )
    assert r2.status_code in {409, 422}, r2.text
    assert (await a.turnos.get(dos.id)).status == BookingStatus.PENDIENTE_SENA
    detalle = (await client.get(f"/v1/bookings/{dos.id}", cookies=cookies_a)).json()
    assert detalle["payments"] == []


async def test_reenviar_la_sena_con_otro_importe_no_suma_un_segundo_pago(client, cookies_a, a):
    turno = await a.turno_con_sena(inicio=INICIO, ahora=HOY)
    url = f"/v1/bookings/{turno.id}/confirm-deposit"
    cab = {"Idempotency-Key": "k-importe"}
    r1 = await client.post(
        url,
        json={"payment_method_id": str(a.efectivo), "amount_cents": SENA},
        cookies=cookies_a,
        headers=cab,
    )
    assert r1.status_code == 200, r1.text
    r2 = await client.post(
        url,
        json={"payment_method_id": str(a.efectivo), "amount_cents": SENA * 5},
        cookies=cookies_a,
        headers=cab,
    )
    detalle = (await client.get(f"/v1/bookings/{turno.id}", cookies=cookies_a)).json()
    assert [p["amount_cents"] for p in detalle["payments"]] == [SENA], r2.text


async def test_la_accion_con_clave_repetida_es_duplicate_y_no_la_repite(client, cookies_a, a):
    turno = await a.turno(inicio=INICIO, ahora=HOY)
    cab = {"Idempotency-Key": "k-marca"}
    r1 = await client.post(
        f"/v1/bookings/{turno.id}/incident-marks",
        json={"reason": "x"},
        cookies=cookies_a,
        headers=cab,
    )
    assert r1.status_code == 201, r1.text
    r2 = await client.post(
        f"/v1/bookings/{turno.id}/incident-marks",
        json={"reason": "x"},
        cookies=cookies_a,
        headers=cab,
    )
    assert _codigo(r2) == "DUPLICATE_IDEMPOTENT", r2.text
    detalle = (await client.get(f"/v1/bookings/{turno.id}", cookies=cookies_a)).json()
    assert len(detalle["incident_marks"]) == 1


# ── 5 · Marcas de incidencia y señas de A no se filtran ──────────────────────


async def test_b_no_anula_la_marca_de_a_y_el_detalle_de_b_no_la_ve(
    client, cookies_a, cookies_b, a, b
):
    turno = await a.turno(inicio=INICIO, ahora=HOY)
    marca = (
        await client.post(
            f"/v1/bookings/{turno.id}/incident-marks", json={"reason": "x"}, cookies=cookies_a
        )
    ).json()
    azar = uuid.uuid4()
    ajeno = await client.delete(f"/v1/incident-marks/{marca['id']}", cookies=cookies_b)
    nada = await client.delete(f"/v1/incident-marks/{azar}", cookies=cookies_b)
    assert ajeno.status_code == nada.status_code == 404
    assert _sin_id(ajeno, marca["id"]) == _sin_id(nada, azar)
    detalle = (await client.get(f"/v1/bookings/{turno.id}", cookies=cookies_a)).json()
    assert [m["id"] for m in detalle["incident_marks"]] == [marca["id"]]
    # El dueño sí puede desmarcar, y repetir no es un error de existencia.
    assert (
        await client.delete(f"/v1/incident-marks/{marca['id']}", cookies=cookies_a)
    ).status_code == 204


async def test_un_cliente_con_el_mismo_telefono_en_b_no_hereda_las_incidencias_de_a(
    client, cookies_a, cookies_b, a, b
):
    """El mismo WhatsApp en dos negocios son dos clientes: las marcas de A no cuentan en B."""
    ta = await a.turno(inicio=INICIO, ahora=HOY)
    tb = await b.turno(inicio=INICIO, ahora=HOY)
    for _ in range(3):
        r = await client.post(
            f"/v1/bookings/{ta.id}/incident-marks", json={"reason": "x"}, cookies=cookies_a
        )
        assert r.status_code == 201, r.text
    detalle_b = (await client.get(f"/v1/bookings/{tb.id}", cookies=cookies_b)).json()
    assert detalle_b["incidents"] == 0 and detalle_b["incident_marks"] == []


async def test_las_senas_a_resolver_de_a_no_estan_en_la_lista_ni_el_contador_de_b(
    client, cookies_a, cookies_b, a, b
):
    turno = await a.turno_con_sena(inicio=INICIO, ahora=HOY)
    await a.turnos.confirm_deposit(turno.id, a.pago(SENA), now=HOY)
    await a.turnos.cancel_operational(turno.id, requested_at=HOY, reason="Lluvia")
    assert (await client.get("/v1/cancellations", cookies=cookies_a)).json()["total"] == 1
    assert (await client.get("/v1/cancellations", cookies=cookies_b)).json()["total"] == 0
    cb = (await client.get("/v1/bookings/counts", cookies=cookies_b)).json()
    assert cb["deposit_cases"] == 0 and cb["total"] == 0
