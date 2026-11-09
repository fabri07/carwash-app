"""Aislamiento de `/service-categories`, `/services` y `/templates` — FASE-4-CONTRATO A2 (PR 4.3).

(No confundir con `test_aislamiento_servicios.py`, que prueba los servicios de aplicación de F3.)

1. **404 ajeno = 404 inexistente**, byte a byte, en cada método con id (incluido `PUT .../prices`).
2. **Los listados y la búsqueda de B no traen filas de A.**
3. **`tenant_id` del cuerpo se ignora** al crear y al editar.
4. **Referencias cruzadas:** B no puede colgar un servicio de la categoría de A ni dar precio a un
   tamaño de A.
5. **Una plantilla no ve ni pisa lo del otro negocio**; `Idempotency-Key` no cruza tenants.

Datos sintéticos.
"""

import uuid

import pytest

from app.tests.api._servicios_helpers import (
    crear_servicio,
    crear_tamanos,
    cuerpo_servicio,
    precios,
)


def _normal(r, id_):
    return r.status_code, r.content.replace(str(id_).encode(), b"<id>")


@pytest.fixture
async def de_a(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    categoria = (
        await client.post("/v1/service-categories", json={"name": "Lavado"}, cookies=cookies_a)
    ).json()
    servicio = await crear_servicio(
        client, cookies_a, tamanos, "Lavado exterior", category_id=categoria["id"]
    )
    return {"tamanos": tamanos, "categoria": categoria, "servicio": servicio}


async def test_b_no_ve_edita_ni_anula_una_categoria_de_a_y_el_404_es_identico(
    client, cookies_b, de_a
):
    ajeno = f"/v1/service-categories/{de_a['categoria']['id']}"
    inexistente = f"/v1/service-categories/{uuid.uuid4()}"
    for metodo, kwargs in (("GET", {}), ("PATCH", {"json": {"name": "Pisada"}}), ("DELETE", {})):
        r_ajeno = await client.request(metodo, ajeno, cookies=cookies_b, **kwargs)
        r_nada = await client.request(metodo, inexistente, cookies=cookies_b, **kwargs)
        assert r_ajeno.status_code == 404, (metodo, r_ajeno.text)
        assert _normal(r_ajeno, de_a["categoria"]["id"]) == _normal(
            r_nada, inexistente.rsplit("/", 1)[1]
        )
        assert de_a["categoria"]["id"] not in r_ajeno.text


async def test_b_no_ve_edita_ni_anula_un_servicio_de_a_y_el_404_es_identico(
    client, cookies_a, cookies_b, de_a
):
    sid = de_a["servicio"]["id"]
    ajeno, inexistente = f"/v1/services/{sid}", f"/v1/services/{uuid.uuid4()}"
    # B tiene tamaños propios, así que el cuerpo del PUT es válido y solo el servicio es ajeno.
    tamanos_b = await crear_tamanos(client, cookies_b)
    pedidos = (
        ("GET", "", {}),
        ("PATCH", "", {"json": {"name": "Pisado"}}),
        ("DELETE", "", {}),
        ("PUT", "/prices", {"json": {"prices": precios(tamanos_b)}}),
    )
    for metodo, sufijo, kwargs in pedidos:
        r_ajeno = await client.request(metodo, ajeno + sufijo, cookies=cookies_b, **kwargs)
        r_nada = await client.request(metodo, inexistente + sufijo, cookies=cookies_b, **kwargs)
        assert r_ajeno.status_code == 404, (metodo, r_ajeno.text)
        assert _normal(r_ajeno, sid) == _normal(r_nada, inexistente.rsplit("/", 1)[-1])
        assert sid not in r_ajeno.text
    # El servicio de A sigue intacto, vivo y con sus precios.
    intacto = (await client.get(ajeno, cookies=cookies_a)).json()
    assert intacto["name"] == "Lavado exterior" and intacto["prices"] == de_a["servicio"]["prices"]


async def test_listados_y_busqueda_de_b_no_incluyen_lo_de_a(client, cookies_a, cookies_b, de_a):
    tamanos_b = await crear_tamanos(client, cookies_b)
    de_b = await crear_servicio(client, cookies_b, tamanos_b, "Lavado de B")
    ids_a = {s["id"] for s in (await client.get("/v1/services", cookies=cookies_a)).json()["items"]}
    ids_b = {s["id"] for s in (await client.get("/v1/services", cookies=cookies_b)).json()["items"]}
    assert ids_a == {de_a["servicio"]["id"]} and ids_b == {de_b["id"]}
    for url in ("/v1/services/search?q=lavado", "/v1/services?q=lavado"):
        r = (await client.get(url, cookies=cookies_b)).json()
        assert [s["id"] for s in r["items"]] == [de_b["id"]] and r["total"] == 1
    por_categoria = await client.get(
        f"/v1/services?category_id={de_a['categoria']['id']}", cookies=cookies_b
    )
    assert por_categoria.json()["total"] == 0  # filtrar por una categoría ajena no revela nada
    assert (await client.get("/v1/service-categories", cookies=cookies_b)).json()["total"] == 0


async def test_tenant_id_del_cuerpo_se_ignora(client, cookies_a, cookies_b, tenant_a, tenant_b):
    tamanos = await crear_tamanos(client, cookies_a)
    ajeno = {"tenant_id": str(tenant_b.id)}
    cat = await client.post(
        "/v1/service-categories", json={"name": "Lavado", **ajeno}, cookies=cookies_a
    )
    assert cat.status_code == 201 and cat.json()["tenant_id"] == str(tenant_a.id)
    srv = await client.post(
        "/v1/services", json={**cuerpo_servicio(tamanos), **ajeno}, cookies=cookies_a
    )
    assert srv.status_code == 201, srv.text
    assert srv.json()["tenant_id"] == str(tenant_a.id)
    sid = srv.json()["id"]
    editado = await client.patch(
        f"/v1/services/{sid}", json={"notes": "x", **ajeno}, cookies=cookies_a
    )
    assert editado.status_code == 200 and editado.json()["tenant_id"] == str(tenant_a.id)
    put = await client.put(
        f"/v1/services/{sid}/prices", json={"prices": precios(tamanos), **ajeno}, cookies=cookies_a
    )
    assert put.status_code == 200 and put.json()["tenant_id"] == str(tenant_a.id)
    assert (await client.get(f"/v1/services/{sid}", cookies=cookies_b)).status_code == 404
    assert (await client.get("/v1/services", cookies=cookies_b)).json()["total"] == 0


async def test_b_no_cuelga_un_servicio_de_la_categoria_de_a(client, cookies_b, de_a):
    tamanos_b = await crear_tamanos(client, cookies_b)
    r = await client.post(
        "/v1/services",
        json=cuerpo_servicio(tamanos_b, "Mio", category_id=de_a["categoria"]["id"]),
        cookies=cookies_b,
    )
    assert r.status_code == 404 and r.json()["detail"]["code"] == "NOT_FOUND"
    propio = await crear_servicio(client, cookies_b, tamanos_b, "Otro")
    r = await client.patch(
        f"/v1/services/{propio['id']}",
        json={"category_id": de_a["categoria"]["id"]},
        cookies=cookies_b,
    )
    assert r.status_code == 404
    assert (await client.get("/v1/services", cookies=cookies_b)).json()["total"] == 1


async def test_b_no_da_precio_a_un_tamano_de_a(client, cookies_b, de_a):
    tamanos_b = await crear_tamanos(client, cookies_b)
    cuerpo = cuerpo_servicio(tamanos_b, "Mio")
    cuerpo["prices"][0]["vehicle_size_id"] = de_a["tamanos"]["AUTO"]
    r = await client.post("/v1/services", json=cuerpo, cookies=cookies_b)
    assert r.status_code == 422
    # El 422 no confirma que el id exista en otro negocio: dice lo mismo que con un id al azar.
    cuerpo["prices"][0]["vehicle_size_id"] = str(uuid.uuid4())
    azar = await client.post("/v1/services", json=cuerpo, cookies=cookies_b)
    assert azar.status_code == 422
    assert r.json()["detail"]["code"] == azar.json()["detail"]["code"]


async def test_el_mismo_nombre_y_glossary_code_conviven_en_a_y_b(client, cookies_a, cookies_b):
    tamanos_a = await crear_tamanos(client, cookies_a)
    tamanos_b = await crear_tamanos(client, cookies_b)
    for cookies, tamanos in ((cookies_a, tamanos_a), (cookies_b, tamanos_b)):
        r = await client.post(
            "/v1/templates/apply",
            json={
                "kind": "detailing",
                "items": [{"glossary_code": "pulido", "prices": precios(tamanos)}],
            },
            cookies=cookies,
        )
        assert r.status_code == 200 and r.json()["created"][0]["glossary_code"] == "pulido"
    r = await client.post("/v1/service-categories", json={"name": "Solo de B"}, cookies=cookies_b)
    assert (
        await client.post("/v1/service-categories", json={"name": "Solo de B"}, cookies=cookies_a)
    ).status_code == 201 and r.status_code == 201


async def test_la_plantilla_de_b_no_ve_lo_de_a_y_la_misma_idempotency_key_no_cruza(
    client, cookies_a, cookies_b, de_a
):
    tamanos_a = de_a["tamanos"]
    tamanos_b = await crear_tamanos(client, cookies_b)
    await client.post(
        "/v1/templates/apply",
        json={
            "kind": "lavadero",
            "items": [
                {"glossary_code": "aspirado", "prices": precios(tamanos_a)},
            ],
        },
        cookies=cookies_a,
    )
    sugerida = (await client.get("/v1/templates/lavadero", cookies=cookies_b)).json()
    assert not any(i["already_added"] for c in sugerida["categories"] for i in c["items"])
    r = await client.post(
        "/v1/templates/apply",
        json={
            "kind": "lavadero",
            "items": [{"glossary_code": "aspirado", "prices": precios(tamanos_b)}],
        },
        cookies=cookies_b,
    )
    assert r.status_code == 200 and len(r.json()["created"]) == 1 and r.json()["skipped"] == []

    key = {"Idempotency-Key": f"k-{uuid.uuid4()}"}
    r1 = await client.post(
        "/v1/services", json=cuerpo_servicio(tamanos_a, "Con clave"), cookies=cookies_a, headers=key
    )
    r2 = await client.post(
        "/v1/services", json=cuerpo_servicio(tamanos_b, "Con clave"), cookies=cookies_b, headers=key
    )
    assert r1.status_code == r2.status_code == 201
