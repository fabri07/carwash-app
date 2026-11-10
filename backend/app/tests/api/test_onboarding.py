"""Wizard de alta (PR 4.6): `GET /onboarding/status` y `POST /onboarding/complete`.

§6 y A8: 409 `ONBOARDING_INCOMPLETE` con cada faltante (también un servicio sin precio para un
tamaño), 200 con todo cargado, idempotencia (no pisa la fecha), reanudación en el primer paso
incompleto, y solo el `OWNER` (un `STAFF`, aun con todos los permisos, recibe 403). El aislamiento
entre negocios está en `tests/security/test_aislamiento_onboarding.py`. Datos sintéticos.
"""

from typing import Any

from httpx import AsyncClient

from app.tests.api._servicios_helpers import crear_servicio, crear_tamanos

PASSWORD = "correct-horse-battery"
STATUS = "/v1/onboarding/status"
COMPLETE = "/v1/onboarding/complete"

TODOS = {
    "VEHICLE_SIZE",
    "SERVICE",
    "RESOURCE",
    "BUSINESS_HOURS",
    "PAYMENT_METHOD",
}


async def _registrar(client: AsyncClient, email="alta@example.com", negocio="Lavadero Alta"):
    r = await client.post(
        "/v1/auth/register", json={"email": email, "password": PASSWORD, "tenant": negocio}
    )
    assert r.status_code == 201, r.text
    return dict(client.cookies)


def _faltan(body: dict[str, Any]) -> set[str]:
    return {m["requirement"] for m in body["missing"]}


def _pasos(body: dict[str, Any]) -> dict[str, bool]:
    return {s["step"]: s["done"] for s in body["steps"]}


async def _post(client: AsyncClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
    r = await client.post(path, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def cargar_todo(client: AsyncClient) -> dict[str, Any]:
    """Lo mínimo de §6: un tamaño, un servicio con precio, un puesto, una franja y un medio."""
    cookies = dict(client.cookies)
    tamanos = await crear_tamanos(client, cookies, "AUTO")
    servicio = await crear_servicio(client, cookies, tamanos, "Lavado completo")
    await _post(client, "/v1/resources", {"name": "Puesto 1"})
    await _post(
        client,
        "/v1/business-hours",
        {"weekday": 1, "opens_at": "09:00:00", "closes_at": "18:00:00"},
    )
    await _post(client, "/v1/payment-methods", {"code": "EFECTIVO", "label": "Efectivo"})
    return {"tamanos": tamanos, "servicio": servicio}


async def test_un_negocio_nuevo_arranca_en_el_primer_paso_con_todo_faltando(client):
    await _registrar(client)
    r = await client.get(STATUS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["completed"] is False and body["completed_at"] is None
    assert body["resume_step"] == "BUSINESS"
    assert _faltan(body) == TODOS
    assert [s["step"] for s in body["steps"]] == [
        "BUSINESS",
        "BRANDING",
        "SIZES",
        "SERVICES",
        "SCHEDULE",
        "PAYMENTS",
        "DEPOSIT",
        "STAFF",
    ]
    requeridos = {s["step"] for s in body["steps"] if s["required"]}
    assert requeridos == {"SIZES", "SERVICES", "SCHEDULE", "PAYMENTS"}
    assert not any(_pasos(body).values())
    pasos = {m["requirement"]: m["step"] for m in body["missing"]}
    assert pasos == {
        "VEHICLE_SIZE": "SIZES",
        "SERVICE": "SERVICES",
        "RESOURCE": "SCHEDULE",
        "BUSINESS_HOURS": "SCHEDULE",
        "PAYMENT_METHOD": "PAYMENTS",
    }


async def test_completar_sin_nada_da_409_con_la_lista(client):
    await _registrar(client)
    r = await client.post(COMPLETE)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "ONBOARDING_INCOMPLETE"
    assert detail["message"]
    assert {m["requirement"] for m in detail["missing"]} == TODOS
    assert all(m["service_ids"] == [] for m in detail["missing"])
    # y no marcó nada
    assert (await client.get("/v1/auth/me")).json()["onboarding_completed"] is False


async def test_se_reanuda_en_el_primer_paso_incompleto(client):
    await _registrar(client)
    await crear_tamanos(client, dict(client.cookies), "AUTO")
    body = (await client.get(STATUS)).json()
    # cargar tamaños da por pasados el negocio y la personalización (aunque no se tocaron)
    assert _pasos(body)["BUSINESS"] and _pasos(body)["BRANDING"] and _pasos(body)["SIZES"]
    assert body["resume_step"] == "SERVICES"
    assert _faltan(body) == TODOS - {"VEHICLE_SIZE"}


async def test_un_paso_opcional_con_datos_propios_cuenta_hecho(client):
    await _registrar(client)
    r = await client.patch("/v1/settings", json={"address": "Calle Falsa 123"})
    assert r.status_code == 200, r.text
    body = (await client.get(STATUS)).json()
    assert _pasos(body)["BUSINESS"] is True and body["resume_step"] == "BRANDING"
    await client.patch("/v1/settings", json={"palette": "bosque"})
    assert (await client.get(STATUS)).json()["resume_step"] == "SIZES"


async def test_cada_faltante_por_separado(client):
    """Con todo cargado, anular una cosa por vez la vuelve a pedir (y lleva a su paso)."""
    await _registrar(client)
    datos = await cargar_todo(client)

    puestos = (await client.get("/v1/resources")).json()["items"]
    franjas = (await client.get("/v1/business-hours")).json()["items"]
    medios = (await client.get("/v1/payment-methods")).json()["items"]

    # sin puesto
    assert (await client.delete(f"/v1/resources/{puestos[0]['id']}")).status_code == 204
    r = await client.post(COMPLETE)
    assert r.status_code == 409 and {m["requirement"] for m in r.json()["detail"]["missing"]} == {
        "RESOURCE"
    }
    assert (await client.get(STATUS)).json()["resume_step"] == "SCHEDULE"
    await _post(client, "/v1/resources", {"name": "Puesto 2"})

    # sin franja
    assert (await client.delete(f"/v1/business-hours/{franjas[0]['id']}")).status_code == 204
    r = await client.post(COMPLETE)
    assert {m["requirement"] for m in r.json()["detail"]["missing"]} == {"BUSINESS_HOURS"}
    await _post(
        client,
        "/v1/business-hours",
        {"weekday": 2, "opens_at": "10:00:00", "closes_at": "14:00:00"},
    )

    # un medio de pago solo de egresos no es un medio de cobro
    await _post(
        client,
        "/v1/payment-methods",
        {
            "code": "PROVEEDOR",
            "label": "Pago a proveedor",
            "for_income": False,
            "for_expense": True,
        },
    )
    assert (await client.delete(f"/v1/payment-methods/{medios[0]['id']}")).status_code == 204
    r = await client.post(COMPLETE)
    assert {m["requirement"] for m in r.json()["detail"]["missing"]} == {"PAYMENT_METHOD"}
    assert (await client.get(STATUS)).json()["resume_step"] == "PAYMENTS"
    await _post(client, "/v1/payment-methods", {"code": "MP", "label": "Mercado Pago"})

    # sin servicio vivo
    servicio = datos["servicio"]
    assert (await client.delete(f"/v1/services/{servicio['id']}")).status_code == 204
    r = await client.post(COMPLETE)
    assert {m["requirement"] for m in r.json()["detail"]["missing"]} == {"SERVICE"}

    assert (await client.post(COMPLETE)).status_code == 409


async def test_a8_un_servicio_sin_precio_para_un_tamano_da_409(client):
    await _registrar(client)
    datos = await cargar_todo(client)
    # Un tamaño nuevo después del alta deja al servicio sin precio para él (§6).
    nuevo = await crear_tamanos(client, dict(client.cookies), "SUV")
    r = await client.post(COMPLETE)
    assert r.status_code == 409, r.text
    missing = r.json()["detail"]["missing"]
    assert missing == [
        {
            "requirement": "SERVICE_PRICES",
            "step": "SERVICES",
            "service_ids": [datos["servicio"]["id"]],
        }
    ]
    body = (await client.get(STATUS)).json()
    assert body["resume_step"] == "SERVICES" and _pasos(body)["SIZES"] is True

    # cargar el precio que falta lo resuelve
    tamanos = {**datos["tamanos"], **nuevo}
    precios = [
        {"vehicle_size_id": sid, "price_cents": 1_500_000, "duration_min": 60}
        for sid in tamanos.values()
    ]
    r = await client.put(f"/v1/services/{datos['servicio']['id']}/prices", json={"prices": precios})
    assert r.status_code == 200, r.text
    assert (await client.post(COMPLETE)).status_code == 200


async def test_un_tamano_anulado_no_exige_precio(client):
    await _registrar(client)
    await cargar_todo(client)
    extra = await crear_tamanos(client, dict(client.cookies), "MOTO")
    assert (await client.delete(f"/v1/vehicle-sizes/{extra['MOTO']}")).status_code == 204
    assert (await client.post(COMPLETE)).status_code == 200


async def test_completar_marca_la_fecha_y_es_idempotente(client):
    await _registrar(client)
    await cargar_todo(client)
    r = await client.post(COMPLETE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["completed"] is True and body["completed_at"] is not None
    assert body["missing"] == []
    primera = body["completed_at"]

    me = (await client.get("/v1/auth/me")).json()
    assert me["onboarding_completed"] is True
    settings = (await client.get("/v1/settings")).json()
    assert settings["onboarding_completed_at"] is not None

    otra = await client.post(COMPLETE)
    assert otra.status_code == 200 and otra.json()["completed_at"] == primera


async def test_completo_no_vuelve_atras_aunque_falte_algo_despues(client):
    """Terminado el alta, anular algo no la "des-termina": el estado lo informa, la fecha queda."""
    await _registrar(client)
    await cargar_todo(client)
    primera = (await client.post(COMPLETE)).json()["completed_at"]
    puesto = (await client.get("/v1/resources")).json()["items"][0]
    await client.delete(f"/v1/resources/{puesto['id']}")
    body = (await client.get(STATUS)).json()
    assert body["completed"] is True and _faltan(body) == {"RESOURCE"}
    r = await client.post(COMPLETE)
    assert r.status_code == 200 and r.json()["completed_at"] == primera


async def test_empleados_y_sena_son_opcionales(client):
    await _registrar(client)
    await cargar_todo(client)
    body = (await client.get(STATUS)).json()
    assert body["missing"] == []
    # sin seña prendida ni empleados, el wizard se reanuda en la seña
    assert body["resume_step"] == "DEPOSIT"
    assert (await client.post(COMPLETE)).status_code == 200

    await client.patch("/v1/settings", json={"deposit_enabled": True})
    assert (await client.get(STATUS)).json()["resume_step"] == "STAFF"


async def test_con_empleado_todos_los_pasos_hechos(client):
    await _registrar(client)
    await cargar_todo(client)
    perfiles = (await client.get("/v1/permission-profiles")).json()["items"]
    r = await client.post(
        "/v1/staff",
        json={
            "username": "lavador.uno",
            "password": PASSWORD,
            "permission_profile_id": perfiles[0]["id"],
        },
    )
    assert r.status_code == 201, r.text
    body = (await client.get(STATUS)).json()
    assert all(_pasos(body).values())
    assert body["resume_step"] == "STAFF"


async def test_solo_el_dueño(client, staff_cookies, cookies_a):
    """A3/A4: un empleado con todos los permisos no ve ni completa el alta."""
    for path, method in ((STATUS, "get"), (COMPLETE, "post")):
        r = await getattr(client, method)(path, cookies=staff_cookies)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "FORBIDDEN"
        assert (await getattr(client, method)(path, cookies=cookies_a)).status_code in (200, 409)
    client.cookies.clear()
    assert (await client.get(STATUS)).status_code == 401
    assert (await client.post(COMPLETE)).status_code == 401
