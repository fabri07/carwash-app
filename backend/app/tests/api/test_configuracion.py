"""Configuración y catálogos de operación (PR 4.2): lo funcional de la API.

La autorización (403 de STAFF sin permiso, `PATCH /settings` solo del OWNER) y el aislamiento
entre tenants los prueba el tester en `tests/security/`.
"""

PASSWORD = "correct-horse-battery"


async def _registrar(client, email="duenio@example.com", tenant="Lavadero Centro"):
    r = await client.post(
        "/v1/auth/register", json={"email": email, "password": PASSWORD, "tenant": tenant}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_el_registro_devuelve_onboarding_pendiente_y_settings_por_defecto(client):
    me = await _registrar(client)
    assert me["onboarding_completed"] is False
    r = await client.get("/v1/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "lavadero-centro"
    assert (body["deposit_enabled"], body["deposit_bps"]) == (False, 2000)
    assert (body["hold_min"], body["min_advance_min"], body["tolerance_min"]) == (30, 5, 20)


async def test_patch_normaliza_el_whatsapp_y_limpia_textos(client):
    await _registrar(client)
    r = await client.patch(
        "/v1/settings",
        json={"whatsapp_e164": "11 4444-5555", "address": "  Calle 1  ", "welcome_text": "  "},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["whatsapp_e164"] == "+5491144445555"
    assert body["address"] == "Calle 1"
    assert body["welcome_text"] is None


async def test_patch_rechaza_whatsapp_invalido_null_en_obligatorios_y_texto_largo(client):
    await _registrar(client)
    for cuerpo in (
        {"whatsapp_e164": "123"},
        {"deposit_enabled": None},
        {"hold_min": 0},
        {"welcome_text": "x" * 281},
        {"slug": "Con Espacios"},
        {"instagram_url": "instagram.com/sin-esquema"},
    ):
        r = await client.patch("/v1/settings", json=cuerpo)
        assert r.status_code == 422, (cuerpo, r.text)


async def test_slug_tomado_es_409_slug_taken(client):
    await _registrar(client, "uno@example.com", "Lavadero Uno")
    await client.post("/v1/auth/logout")
    await _registrar(client, "dos@example.com", "Lavadero Dos")
    r = await client.patch("/v1/settings", json={"slug": "lavadero-uno"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "SLUG_TAKEN"
    r = await client.patch("/v1/settings", json={"slug": "LAVADERO-DOS-SUR"})
    assert r.status_code == 200 and r.json()["slug"] == "lavadero-dos-sur"


async def test_crud_de_tamanos_con_orden_y_codigo_repetido(client):
    await _registrar(client)
    a = await client.post(
        "/v1/vehicle-sizes", json={"code": "suv", "label": "SUV", "sort_order": 2}
    )
    assert a.status_code == 201, a.text
    assert a.json()["code"] == "SUV"
    b = await client.post(
        "/v1/vehicle-sizes", json={"code": "AUTO", "label": "Auto", "sort_order": 1}
    )
    assert b.status_code == 201
    repetido = await client.post("/v1/vehicle-sizes", json={"code": "AUTO", "label": "Otro"})
    assert repetido.status_code == 409
    assert repetido.json()["detail"]["code"] == "CODE_TAKEN"
    lista = (await client.get("/v1/vehicle-sizes")).json()
    assert [x["code"] for x in lista["items"]] == ["AUTO", "SUV"]
    r = await client.patch(f"/v1/vehicle-sizes/{a.json()['id']}", json={"label": "Camioneta"})
    assert r.status_code == 200 and r.json()["label"] == "Camioneta"
    assert (await client.delete(f"/v1/vehicle-sizes/{a.json()['id']}")).status_code == 204
    assert (await client.get("/v1/vehicle-sizes")).json()["total"] == 1


async def test_franjas_validan_el_rango_tambien_al_editar(client):
    await _registrar(client)
    malo = await client.post(
        "/v1/business-hours", json={"weekday": 1, "opens_at": "13:00", "closes_at": "09:00"}
    )
    assert malo.status_code == 422
    ok = await client.post(
        "/v1/business-hours", json={"weekday": 1, "opens_at": "09:00", "closes_at": "13:00"}
    )
    assert ok.status_code == 201, ok.text
    r = await client.patch(f"/v1/business-hours/{ok.json()['id']}", json={"closes_at": "08:00"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "VALIDATION_ERROR"


async def test_medio_de_pago_borra_la_cuenta_con_null(client):
    await _registrar(client)
    r = await client.post(
        "/v1/payment-methods",
        json={
            "code": "credito",
            "label": "Crédito",
            "commission_bps": 500,
            "settlement_account": "MP",
        },
    )
    assert r.status_code == 201, r.text
    medio = r.json()
    assert (medio["code"], medio["settlement_account"]) == ("CREDITO", "MP")
    r = await client.patch(f"/v1/payment-methods/{medio['id']}", json={"label": "Tarjeta"})
    assert r.json()["settlement_account"] == "MP"
    r = await client.patch(f"/v1/payment-methods/{medio['id']}", json={"settlement_account": None})
    assert r.json()["settlement_account"] is None


async def test_puesto_con_nombre_repetido_es_409_name_taken(client):
    await _registrar(client)
    assert (await client.post("/v1/resources", json={"name": "Puesto 1"})).status_code == 201
    r = await client.post("/v1/resources", json={"name": "Puesto 1"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NAME_TAKEN"
