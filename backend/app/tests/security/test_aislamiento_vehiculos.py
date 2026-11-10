"""Aislamiento del catálogo de vehículos — FASE-4-CONTRATO A2 y A10 (PR 4.4, SQLite).

Los dos negocios tienen la base copiada (como al registrarse). Sin RLS, lo único que los separa acá
es el filtro por `tenant_id` del servicio: la red de Postgres está en
`test_aislamiento_vehiculos_pg.py` y en el poblador genérico (`_poblar_dominio`).

1. **404 ajeno = 404 inexistente**, byte a byte, en cada método con id y en `?make_id=` ajeno.
2. **Lo que suma A no aparece en B** (A10): ni en los listados, ni en la búsqueda.
3. **`tenant_id` del cuerpo se ignora** al crear y al editar.
4. **Referencias cruzadas:** B no cuelga un modelo de la marca de A ni mapea una carrocería a un
   tamaño de A (422 igual que con un id al azar). La misma clave sirve en los dos negocios.

Datos sintéticos.
"""

import uuid

import pytest

from app.application.services.vehicle_catalog_service import copy_vehicle_base
from app.tests.api._servicios_helpers import crear_tamanos


def _normal(r, id_):
    return r.status_code, r.content.replace(str(id_).encode(), b"<id>")


@pytest.fixture
async def catalogos(db_session, tenant_a, tenant_b):
    await copy_vehicle_base(db_session, tenant_a.id)
    await copy_vehicle_base(db_session, tenant_b.id)
    await db_session.flush()


@pytest.fixture
async def de_a(client, cookies_a, catalogos):
    marca = await client.post("/v1/vehicle-makes", json={"name": "Marca de A"}, cookies=cookies_a)
    modelo = await client.post(
        "/v1/vehicle-models",
        json={"make_id": marca.json()["id"], "name": "Modelo de A", "body_type": "SUV"},
        cookies=cookies_a,
    )
    color = await client.post("/v1/vehicle-colors", json={"name": "Color de A"}, cookies=cookies_a)
    assert {marca.status_code, modelo.status_code, color.status_code} == {201}
    return {"marca": marca.json(), "modelo": modelo.json(), "color": color.json()}


@pytest.mark.parametrize(
    ("recurso", "clave", "parche"),
    [
        ("vehicle-makes", "marca", {"name": "Pisada", "hidden": True}),
        ("vehicle-models", "modelo", {"name": "Pisado", "body_type": "MOTO"}),
        ("vehicle-colors", "color", {"name": "Pisado", "hidden": True}),
    ],
)
async def test_b_no_ve_ni_edita_lo_de_a_y_el_404_es_identico(
    client, cookies_a, cookies_b, de_a, recurso, clave, parche
):
    propio = de_a[clave]
    ajeno, inexistente = f"/v1/{recurso}/{propio['id']}", f"/v1/{recurso}/{uuid.uuid4()}"
    for metodo, kwargs in (("GET", {}), ("PATCH", {"json": parche})):
        r_ajeno = await client.request(metodo, ajeno, cookies=cookies_b, **kwargs)
        r_nada = await client.request(metodo, inexistente, cookies=cookies_b, **kwargs)
        assert r_ajeno.status_code == 404, (metodo, r_ajeno.text)
        assert _normal(r_ajeno, propio["id"]) == _normal(r_nada, inexistente.rsplit("/", 1)[1])
        assert propio["id"] not in r_ajeno.text
    intacto = (await client.get(ajeno, cookies=cookies_a)).json()
    assert intacto["name"] == propio["name"] and intacto["hidden_at"] is None


async def test_listar_modelos_de_una_marca_ajena_es_el_404_de_una_inexistente(
    client, cookies_b, de_a
):
    ajena = await client.get(
        "/v1/vehicle-models", params={"make_id": de_a["marca"]["id"]}, cookies=cookies_b
    )
    azar = uuid.uuid4()
    inexistente = await client.get(
        "/v1/vehicle-models", params={"make_id": str(azar)}, cookies=cookies_b
    )
    assert ajena.status_code == 404
    assert _normal(ajena, de_a["marca"]["id"]) == _normal(inexistente, azar)


async def test_lo_que_suma_a_no_aparece_en_b(client, cookies_a, cookies_b, de_a):
    """A10: una marca agregada por A no aparece en B (ni un modelo ni un color)."""
    for url, q in (
        ("/v1/vehicle-makes", "marca de a"),
        ("/v1/vehicle-models", "modelo de a"),
        ("/v1/vehicle-colors", "color de a"),
    ):
        en_a = (await client.get(url, params={"q": q}, cookies=cookies_a)).json()
        en_b = await client.get(url, params={"q": q, "include_hidden": True}, cookies=cookies_b)
        assert en_a["total"] == 1, url
        assert en_b.json()["total"] == 0, url
    total_a = (await client.get("/v1/vehicle-makes", cookies=cookies_a)).json()["total"]
    total_b = (await client.get("/v1/vehicle-makes", cookies=cookies_b)).json()["total"]
    assert total_a == total_b + 1


async def test_ocultar_en_a_no_oculta_en_b(client, cookies_a, cookies_b, catalogos):
    fiat_a = (
        await client.get("/v1/vehicle-makes", params={"q": "fiat"}, cookies=cookies_a)
    ).json()["items"][0]
    await client.patch(
        f"/v1/vehicle-makes/{fiat_a['id']}", json={"hidden": True}, cookies=cookies_a
    )
    en_b = (await client.get("/v1/vehicle-makes", params={"q": "fiat"}, cookies=cookies_b)).json()
    assert en_b["total"] == 1 and en_b["items"][0]["id"] != fiat_a["id"]


async def test_tenant_id_del_cuerpo_se_ignora(
    client, cookies_a, cookies_b, tenant_a, tenant_b, catalogos
):
    ajeno = {"tenant_id": str(tenant_b.id)}
    marca = await client.post(
        "/v1/vehicle-makes", json={"name": "Propia", **ajeno}, cookies=cookies_a
    )
    assert marca.status_code == 201 and marca.json()["tenant_id"] == str(tenant_a.id)
    modelo = await client.post(
        "/v1/vehicle-models",
        json={"make_id": marca.json()["id"], "name": "Propio", "body_type": "SUV", **ajeno},
        cookies=cookies_a,
    )
    assert modelo.status_code == 201 and modelo.json()["tenant_id"] == str(tenant_a.id)
    editado = await client.patch(
        f"/v1/vehicle-makes/{marca.json()['id']}", json={"hidden": True, **ajeno}, cookies=cookies_a
    )
    assert editado.status_code == 200 and editado.json()["tenant_id"] == str(tenant_a.id)
    color = await client.post(
        "/v1/vehicle-colors", json={"name": "Propio", **ajeno}, cookies=cookies_a
    )
    assert color.json()["tenant_id"] == str(tenant_a.id)
    r = await client.get("/v1/vehicle-makes", params={"q": "propia"}, cookies=cookies_b)
    assert r.json()["total"] == 0


async def test_b_no_cuelga_un_modelo_de_la_marca_de_a(client, cookies_b, de_a):
    r = await client.post(
        "/v1/vehicle-models",
        json={"make_id": de_a["marca"]["id"], "name": "Intruso", "body_type": "SUV"},
        cookies=cookies_b,
    )
    assert r.status_code == 404 and r.json()["detail"]["code"] == "NOT_FOUND"


async def test_b_no_mapea_una_carroceria_a_un_tamano_de_a(client, cookies_a, cookies_b, catalogos):
    """El 422 repite el id que mandó B (como en servicios) y es el mismo que con un id al azar:
    no confirma que exista en otro negocio."""
    tamanos_a = await crear_tamanos(client, cookies_a, "AUTO")
    azar = str(uuid.uuid4())
    respuestas = [
        await client.put(
            "/v1/body-type-sizes",
            json={"items": [{"body_type": "SEDAN", "vehicle_size_id": size_id}]},
            cookies=cookies_b,
        )
        for size_id in (tamanos_a["AUTO"], azar)
    ]
    assert respuestas[0].status_code == 422
    assert _normal(respuestas[0], tamanos_a["AUTO"]) == _normal(respuestas[1], azar)


async def test_el_mapa_de_a_no_sugiere_en_b(client, cookies_a, cookies_b, catalogos):
    tamanos_a = await crear_tamanos(client, cookies_a, "PICKUP")
    await client.put(
        "/v1/body-type-sizes",
        json={"items": [{"body_type": "PICKUP", "vehicle_size_id": tamanos_a["PICKUP"]}]},
        cookies=cookies_a,
    )
    hilux_b = (
        await client.get("/v1/vehicle-models", params={"q": "hilux"}, cookies=cookies_b)
    ).json()["items"][0]
    assert hilux_b["suggested_vehicle_size_id"] is None
    mapa_b = (await client.get("/v1/body-type-sizes", cookies=cookies_b)).json()["items"]
    assert all(i["vehicle_size_id"] is None for i in mapa_b)


async def test_la_misma_clave_y_el_mismo_nombre_conviven(client, cookies_a, cookies_b, catalogos):
    key = {"Idempotency-Key": f"k-{uuid.uuid4()}"}
    r1 = await client.post(
        "/v1/vehicle-makes", json={"name": "Compartida"}, cookies=cookies_a, headers=key
    )
    r2 = await client.post(
        "/v1/vehicle-makes", json={"name": "Compartida"}, cookies=cookies_b, headers=key
    )
    assert r1.status_code == r2.status_code == 201
