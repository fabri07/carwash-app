"""Aislamiento del wizard de alta (PR 4.6, SQLite).

`/onboarding/*` no recibe ids: el negocio sale siempre del token. Lo que importa es que el catálogo
y el alta de un negocio no cuenten para otro:

- lo que carga A no le completa requisitos a B (B sigue con todo faltando);
- terminar el alta de A no marca la de B (ni su `/auth/me`);
- el servicio sin precio de B no aparece en los faltantes de A;
- un `tenant_id` en el cuerpo no cambia de negocio.

Datos sintéticos.
"""

from app.tests.api._servicios_helpers import crear_servicio, crear_tamanos

STATUS = "/v1/onboarding/status"
COMPLETE = "/v1/onboarding/complete"


async def _cargar_todo(client, cookies) -> dict:
    tamanos = await crear_tamanos(client, cookies, "AUTO")
    servicio = await crear_servicio(client, cookies, tamanos, "Lavado básico")
    for path, body in (
        ("/v1/resources", {"name": "Puesto 1"}),
        ("/v1/business-hours", {"weekday": 3, "opens_at": "08:00:00", "closes_at": "12:00:00"}),
        ("/v1/payment-methods", {"code": "EFECTIVO", "label": "Efectivo"}),
    ):
        r = await client.post(path, json=body, cookies=cookies)
        assert r.status_code == 201, r.text
    return {"tamanos": tamanos, "servicio": servicio}


def _faltan(r) -> set[str]:
    return {m["requirement"] for m in r.json()["missing"]}


async def test_lo_de_un_negocio_no_completa_al_otro(client, cookies_a, cookies_b):
    await _cargar_todo(client, cookies_a)
    assert (await client.get(STATUS, cookies=cookies_a)).json()["missing"] == []

    b = await client.get(STATUS, cookies=cookies_b)
    assert b.status_code == 200
    assert _faltan(b) == {"VEHICLE_SIZE", "SERVICE", "RESOURCE", "BUSINESS_HOURS", "PAYMENT_METHOD"}
    assert b.json()["resume_step"] == "BUSINESS"
    r = await client.post(COMPLETE, cookies=cookies_b)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ONBOARDING_INCOMPLETE"


async def test_terminar_a_no_termina_b(client, cookies_a, cookies_b):
    await _cargar_todo(client, cookies_a)
    assert (await client.post(COMPLETE, cookies=cookies_a)).status_code == 200
    assert (await client.get("/v1/auth/me", cookies=cookies_a)).json()["onboarding_completed"]

    me_b = await client.get("/v1/auth/me", cookies=cookies_b)
    assert me_b.json()["onboarding_completed"] is False
    b = (await client.get(STATUS, cookies=cookies_b)).json()
    assert b["completed"] is False and b["completed_at"] is None


async def test_los_faltantes_de_b_no_aparecen_en_a(client, cookies_a, cookies_b):
    await _cargar_todo(client, cookies_a)
    datos_b = await _cargar_todo(client, cookies_b)
    await crear_tamanos(client, cookies_b, "SUV")  # deja al servicio de B sin precio para SUV

    b = (await client.get(STATUS, cookies=cookies_b)).json()
    assert b["missing"] == [
        {
            "requirement": "SERVICE_PRICES",
            "step": "SERVICES",
            "service_ids": [datos_b["servicio"]["id"]],
        }
    ]
    assert (await client.get(STATUS, cookies=cookies_a)).json()["missing"] == []
    assert (await client.post(COMPLETE, cookies=cookies_a)).status_code == 200
    assert (await client.post(COMPLETE, cookies=cookies_b)).status_code == 409


async def test_el_tenant_id_del_cuerpo_se_ignora(client, cookies_a, cookies_b, tenant_b):
    await _cargar_todo(client, cookies_a)
    r = await client.post(COMPLETE, json={"tenant_id": str(tenant_b.id)}, cookies=cookies_a)
    assert r.status_code == 200
    assert (await client.get(STATUS, cookies=cookies_b)).json()["completed"] is False
