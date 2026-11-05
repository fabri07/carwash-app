"""Configuración (PR 4.2a): `/settings` y el ABM del catálogo. Lo funcional.

Autorización y aislamiento entre tenants: `tests/security/test_aislamiento_configuracion.py`.
"""

PASSWORD = "correct-horse-battery"


async def _registrar(client, email="duenio@example.com", negocio="Lavadero Demo"):
    r = await client.post(
        "/v1/auth/register", json={"email": email, "password": PASSWORD, "tenant": negocio}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_me_informa_si_el_alta_termino(client):
    me = await _registrar(client)
    assert me["onboarding_completed"] is False
    assert (await client.get("/v1/auth/me")).json()["onboarding_completed"] is False


async def test_settings_nacen_con_los_defaults_y_el_slug_del_nombre(client):
    await _registrar(client)
    r = await client.get("/v1/settings")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["slug"] == "lavadero-demo" and s["name"] == "Lavadero Demo"
    assert s["deposit_enabled"] is False and s["deposit_bps"] == 2000
    assert s["quote_validity_hours"] == 24 and s["min_advance_min"] == 5
    assert s["onboarding_completed_at"] is None


async def test_patch_cambia_solo_lo_que_se_manda(client):
    await _registrar(client)
    r = await client.patch(
        "/v1/settings",
        json={"deposit_enabled": True, "deposit_bps": 3000, "whatsapp_e164": "+5493515551234"},
    )
    assert r.status_code == 200, r.text
    s = r.json()
    assert (s["deposit_enabled"], s["deposit_bps"], s["whatsapp_e164"]) == (
        True,
        3000,
        "+5493515551234",
    )
    assert s["tolerance_min"] == 20  # no tocado
    again = (await client.get("/v1/settings")).json()
    assert again["deposit_bps"] == 3000


async def test_patch_con_null_borra_un_opcional(client):
    await _registrar(client)
    await client.patch("/v1/settings", json={"address": "Calle 1"})
    r = await client.patch("/v1/settings", json={"address": None})
    assert r.status_code == 200 and r.json()["address"] is None


async def test_el_dueño_edita_su_slug(client):
    await _registrar(client)
    r = await client.patch("/v1/settings", json={"slug": "Mi-Lavadero"})
    assert r.status_code == 200 and r.json()["slug"] == "mi-lavadero"


async def test_slug_reservado_o_tomado(client):
    await _registrar(client, "a@example.com", "Negocio A")
    assert (await client.patch("/v1/settings", json={"slug": "admin"})).json()["detail"][
        "code"
    ] == "SLUG_RESERVED"
    otro = client.__class__(transport=client._transport, base_url=str(client.base_url))
    try:
        await otro.post(
            "/v1/auth/register",
            json={"email": "b@example.com", "password": PASSWORD, "tenant": "Negocio B"},
        )
        r = await otro.patch("/v1/settings", json={"slug": "negocio-a"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "SLUG_TAKEN"
    finally:
        await otro.aclose()


async def test_ciclo_de_un_puesto(client):
    await _registrar(client)
    creado = await client.post("/v1/resources", json={"name": "Puesto 1", "sort_order": 1})
    assert creado.status_code == 201, creado.text
    rid = creado.json()["id"]
    dup = await client.post("/v1/resources", json={"name": "Puesto 1"})
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "NAME_TAKEN"
    r = await client.patch(f"/v1/resources/{rid}", json={"name": "Box A"})
    assert r.status_code == 200 and r.json()["name"] == "Box A"
    assert (await client.get("/v1/resources")).json()["total"] == 1
    assert (await client.delete(f"/v1/resources/{rid}")).status_code == 204
    assert (await client.get("/v1/resources")).json()["total"] == 0
    assert (await client.get(f"/v1/resources/{rid}")).status_code == 404


async def test_tamanos_y_medios_de_pago(client):
    await _registrar(client)
    t = await client.post("/v1/vehicle-sizes", json={"code": "suv", "label": "SUV"})
    assert t.status_code == 201 and t.json()["code"] == "SUV"
    dup = await client.post("/v1/vehicle-sizes", json={"code": "SUV", "label": "Otra"})
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "CODE_TAKEN"

    m = await client.post(
        "/v1/payment-methods",
        json={
            "code": "MP",
            "label": "Mercado Pago",
            "commission_bps": 450,
            "settlement_account": "CVU",
        },
    )
    assert m.status_code == 201, m.text
    mid = m.json()["id"]
    r = await client.patch(f"/v1/payment-methods/{mid}", json={"settlement_account": None})
    assert r.status_code == 200 and r.json()["settlement_account"] is None
    assert r.json()["commission_bps"] == 450
    bad = await client.patch(f"/v1/payment-methods/{mid}", json={"commission_bps": 10001})
    assert bad.status_code == 422


async def test_horarios_validan_la_franja(client):
    await _registrar(client)
    ok = await client.post(
        "/v1/business-hours", json={"weekday": 1, "opens_at": "09:00:00", "closes_at": "18:00:00"}
    )
    assert ok.status_code == 201, ok.text
    hid = ok.json()["id"]
    invertida = await client.post(
        "/v1/business-hours", json={"weekday": 1, "opens_at": "18:00:00", "closes_at": "09:00:00"}
    )
    assert invertida.status_code == 422
    # editar solo la apertura y dejarla después del cierre: la guarda cruza con lo guardado
    mal = await client.patch(f"/v1/business-hours/{hid}", json={"opens_at": "19:00:00"})
    assert mal.status_code == 422 and mal.json()["detail"]["code"] == "VALIDATION_ERROR"
    bien = await client.patch(f"/v1/business-hours/{hid}", json={"closes_at": "20:00:00"})
    assert bien.status_code == 200 and bien.json()["closes_at"] == "20:00:00"


async def test_un_texto_en_blanco_borra_el_opcional(client):
    await _registrar(client)
    await client.patch("/v1/settings", json={"address": "Calle 1"})
    r = await client.patch("/v1/settings", json={"address": "   "})
    assert r.status_code == 200 and r.json()["address"] is None


async def test_los_listados_respetan_el_orden_configurado(client):
    await _registrar(client)
    for nombre, orden in (("Z", 1), ("A", 3), ("M", 2)):
        await client.post("/v1/resources", json={"name": nombre, "sort_order": orden})
    nombres = [i["name"] for i in (await client.get("/v1/resources")).json()["items"]]
    assert nombres == ["Z", "M", "A"]
    for dia, hora in ((3, "10:00:00"), (1, "12:00:00"), (1, "08:00:00")):
        await client.post(
            "/v1/business-hours", json={"weekday": dia, "opens_at": hora, "closes_at": "23:00:00"}
        )
    franjas = [
        (i["weekday"], i["opens_at"])
        for i in (await client.get("/v1/business-hours")).json()["items"]
    ]
    assert franjas == [(1, "08:00:00"), (1, "12:00:00"), (3, "10:00:00")]


async def test_whatsapp_e_instagram_en_blanco_se_borran(client):
    await _registrar(client)
    await client.patch(
        "/v1/settings",
        json={"whatsapp_e164": "+5493515551234", "instagram_url": "https://instagram.com/lavadero"},
    )
    r = await client.patch("/v1/settings", json={"whatsapp_e164": "", "instagram_url": "  "})
    assert r.status_code == 200, r.text
    assert r.json()["whatsapp_e164"] is None and r.json()["instagram_url"] is None


async def test_horario_con_zona_horaria_es_422_no_500(client):
    await _registrar(client)
    mezcla = await client.post(
        "/v1/business-hours", json={"weekday": 1, "opens_at": "08:00:00Z", "closes_at": "18:00"}
    )
    assert mezcla.status_code == 422, mezcla.text
    ok = await client.post(
        "/v1/business-hours", json={"weekday": 1, "opens_at": "08:00", "closes_at": "18:00"}
    )
    hid = ok.json()["id"]
    parche = await client.patch(f"/v1/business-hours/{hid}", json={"closes_at": "19:00:00+02:00"})
    assert parche.status_code == 422, parche.text


async def test_no_se_anula_el_ultimo_medio_de_pago(client):
    await _registrar(client)
    unico = (
        await client.post("/v1/payment-methods", json={"code": "EFECTIVO", "label": "Efectivo"})
    ).json()
    r = await client.delete(f"/v1/payment-methods/{unico['id']}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "IN_USE"
    otro = await client.post("/v1/payment-methods", json={"code": "TARJETA", "label": "Tarjeta"})
    assert otro.status_code == 201
    assert (await client.delete(f"/v1/payment-methods/{unico['id']}")).status_code == 204


async def test_si_se_agotan_los_slugs_el_registro_no_dice_email_tomado(client, monkeypatch):
    monkeypatch.setattr(
        "app.application.services.tenant_provisioning.candidate_slug",
        lambda name, suffix=None: "siempre-igual",
    )
    await _registrar(client, email="uno@example.com", negocio="Uno")
    r = await client.post(
        "/v1/auth/register",
        json={"email": "dos@example.com", "password": PASSWORD, "tenant": "Dos"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "SLUG_TAKEN"
