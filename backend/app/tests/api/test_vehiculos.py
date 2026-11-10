"""Catálogo de vehículos (PR 4.4): `/vehicle-makes`, `/vehicle-models`, `/vehicle-colors` y
`/body-type-sizes`. Lo funcional, sobre un negocio registrado por `/auth/register` (que copia la
base, Y3).

Aislamiento entre negocios: `tests/security/test_aislamiento_vehiculos.py`. Datos sintéticos.
"""

import uuid
from typing import Any

import pytest

from app.domain.permissions import ALL_PERMISSIONS, Permission
from app.domain.roles import Role
from app.domain.vehicle_catalog import VehicleBodyType, load_vehicle_base
from app.tests.api._servicios_helpers import crear_tamanos
from app.tests.conftest import make_profile, make_user, session_cookies

PASSWORD = "correct-horse-battery"
BASE = load_vehicle_base()


@pytest.fixture
async def negocio(client) -> dict[str, Any]:
    """Un negocio dado de alta por la API: su dueño queda logueado en el `client`."""
    r = await client.post(
        "/v1/auth/register",
        json={"email": "duenio-vehiculos@example.com", "password": PASSWORD, "tenant": "Demo"},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _marca(client, nombre: str) -> dict[str, Any]:
    r = await client.get("/v1/vehicle-makes", params={"q": nombre})
    assert r.status_code == 200, r.text
    return next(m for m in r.json()["items"] if m["name"] == nombre)


async def _modelo(client, marca: str, nombre: str) -> dict[str, Any]:
    make = await _marca(client, marca)
    r = await client.get("/v1/vehicle-models", params={"make_id": make["id"], "q": nombre})
    return next(m for m in r.json()["items"] if m["name"] == nombre)


async def _staff(db_session, negocio, permisos) -> dict[str, str]:
    from app.persistence.models import Tenant  # noqa: PLC0415

    tenant = await db_session.get(Tenant, uuid.UUID(negocio["tenant"]["id"]))
    perfil = await make_profile(db_session, tenant, f"perfil {uuid.uuid4().hex[:6]}", permisos)
    user = await make_user(
        db_session, tenant, Role.STAFF, None, username=f"emp.{uuid.uuid4().hex[:8]}", profile=perfil
    )
    return session_cookies(user)


# ── La base copiada al registrarse ───────────────────────────────────────────


async def test_el_registro_copia_la_base_entera(client, negocio):
    marcas = (await client.get("/v1/vehicle-makes", params={"limit": 200})).json()
    assert marcas["total"] == len(BASE.makes)
    assert not any(m["is_custom"] or m["hidden_at"] for m in marcas["items"])
    modelos = (await client.get("/v1/vehicle-models", params={"limit": 1})).json()
    assert modelos["total"] == BASE.model_count
    colores = (await client.get("/v1/vehicle-colors", params={"limit": 200})).json()
    assert [c["name"] for c in colores["items"]] == sorted(BASE.colors)
    tabla = (await client.get("/v1/body-type-sizes")).json()
    assert [i["body_type"] for i in tabla["items"]] == [b.value for b in VehicleBodyType]
    assert all(i["vehicle_size_id"] is None for i in tabla["items"])  # nace vacía (§11.7)


async def test_sin_q_ordena_por_nombre_y_pagina(client, negocio):
    p1 = (await client.get("/v1/vehicle-makes", params={"limit": 5})).json()
    p2 = (await client.get("/v1/vehicle-makes", params={"limit": 5, "offset": 5})).json()
    nombres = [m["name"] for m in p1["items"] + p2["items"]]
    assert nombres == sorted(nombres) and len(set(nombres)) == 10
    assert p1["has_more"] is True


# ── Búsqueda (Y8) ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("q", "esperada"),
    [("toyota", "Toyota"), ("volks", "Volkswagen"), ("toyta", "Toyota"), ("citroen", "Citroën")],
)
async def test_buscar_marcas_tolera_tildes_prefijos_y_errores(client, negocio, q, esperada):
    r = (await client.get("/v1/vehicle-makes", params={"q": q})).json()
    assert r["items"][0]["name"] == esperada, r


async def test_q_vacio_o_sin_nada_buscable_lista_todo(client, negocio):
    for q in ("", "  ", "!!"):
        r = (await client.get("/v1/vehicle-makes", params={"q": q})).json()
        assert r["total"] == len(BASE.makes), q


async def test_buscar_modelos_por_marca_y_modelo_sin_elegir_marca(client, negocio):
    r = (await client.get("/v1/vehicle-models", params={"q": "toyota hilux"})).json()
    assert [(m["make_name"], m["name"]) for m in r["items"]] == [("Toyota", "Hilux")]
    r = (await client.get("/v1/vehicle-models", params={"q": "hilux"})).json()
    assert r["items"][0]["name"] == "Hilux" and r["items"][0]["body_type"] == "PICKUP"


async def test_modelos_de_una_marca(client, negocio):
    toyota = await _marca(client, "Toyota")
    r = (
        await client.get("/v1/vehicle-models", params={"make_id": toyota["id"], "limit": 200})
    ).json()
    esperados = next(m for m in BASE.makes if m.name == "Toyota").models
    assert r["total"] == len(esperados)
    assert {m["make_id"] for m in r["items"]} == {toyota["id"]}
    assert {m["name"] for m in r["items"]} == {m.name for m in esperados}


async def test_buscar_colores(client, negocio):
    r = (await client.get("/v1/vehicle-colors", params={"q": "gris"})).json()
    assert [c["name"] for c in r["items"]] == ["Gris", "Gris oscuro"]


# ── Alta, renombrar, ocultar ─────────────────────────────────────────────────


async def test_crear_marca_modelo_y_color_propios(client, negocio):
    marca = await client.post("/v1/vehicle-makes", json={"name": "Marca Sintética"})
    assert marca.status_code == 201, marca.text
    assert marca.json()["is_custom"] is True and marca.json()["hidden_at"] is None
    modelo = await client.post(
        "/v1/vehicle-models",
        json={"make_id": marca.json()["id"], "name": "Modelo X", "body_type": "SUV"},
    )
    assert modelo.status_code == 201, modelo.text
    m = modelo.json()
    assert (m["make_name"], m["body_type"], m["is_custom"]) == ("Marca Sintética", "SUV", True)
    color = await client.post("/v1/vehicle-colors", json={"name": "Verde agua"})
    assert color.status_code == 201 and color.json()["is_custom"] is True
    buscado = (await client.get("/v1/vehicle-models", params={"q": "sintetica modelo x"})).json()
    assert [i["id"] for i in buscado["items"]] == [m["id"]]


@pytest.mark.parametrize("nombre", ["Toyota", "toyota", "TOYOTA ", "Toyóta"])
async def test_marca_repetida_por_clave_es_409(client, negocio, nombre):
    r = await client.post("/v1/vehicle-makes", json={"name": nombre})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NAME_TAKEN"


async def test_modelo_repetido_en_su_marca_es_409_pero_en_otra_marca_no(client, negocio):
    toyota = await _marca(client, "Toyota")
    r = await client.post(
        "/v1/vehicle-models", json={"make_id": toyota["id"], "name": "HILUX", "body_type": "SUV"}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NAME_TAKEN"
    ford = await _marca(client, "Ford")
    r = await client.post(
        "/v1/vehicle-models", json={"make_id": ford["id"], "name": "Hilux", "body_type": "PICKUP"}
    )
    assert r.status_code == 201


async def test_color_repetido_es_409(client, negocio):
    r = await client.post("/v1/vehicle-colors", json={"name": "blanco"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NAME_TAKEN"


@pytest.mark.parametrize("nombre", ["", "   ", "!!!", "​", "x" * 41])
async def test_nombre_invalido_es_422(client, negocio, nombre):
    r = await client.post("/v1/vehicle-makes", json={"name": nombre})
    assert r.status_code == 422, r.text


async def test_renombrar_recalcula_la_clave_y_choca_con_otra(client, negocio):
    fiat = await _marca(client, "Fiat")
    r = await client.patch(f"/v1/vehicle-makes/{fiat['id']}", json={"name": "FIAT Argentina"})
    assert r.status_code == 200 and r.json()["name"] == "FIAT Argentina"
    assert (await client.get("/v1/vehicle-makes", params={"q": "fiat arg"})).json()["total"] == 1
    r = await client.patch(f"/v1/vehicle-makes/{fiat['id']}", json={"name": "Ford"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NAME_TAKEN"
    # El 409 no deja nada a medias.
    assert (await client.get(f"/v1/vehicle-makes/{fiat['id']}")).json()["name"] == "FIAT Argentina"


async def test_renombrar_modelo_y_color(client, negocio):
    gol = await _modelo(client, "Volkswagen", "Gol")
    r = await client.patch(f"/v1/vehicle-models/{gol['id']}", json={"name": "Gol Power"})
    assert r.status_code == 200 and r.json()["name"] == "Gol Power"
    trend = await _modelo(client, "Volkswagen", "Gol Trend")
    r = await client.patch(f"/v1/vehicle-models/{trend['id']}", json={"name": "gol power"})
    assert r.status_code == 409
    rojo = (await client.get("/v1/vehicle-colors", params={"q": "rojo"})).json()["items"][0]
    r = await client.patch(f"/v1/vehicle-colors/{rojo['id']}", json={"name": "Rojo Tango"})
    assert r.status_code == 200 and r.json()["name"] == "Rojo Tango"
    r = await client.patch(f"/v1/vehicle-colors/{rojo['id']}", json={"name": "Negro"})
    assert r.status_code == 409


async def test_ocultar_y_mostrar_una_marca_oculta_sus_modelos(client, negocio):
    alfa = await _marca(client, "Alfa Romeo")
    r = await client.patch(f"/v1/vehicle-makes/{alfa['id']}", json={"hidden": True})
    assert r.status_code == 200 and r.json()["hidden_at"] is not None
    oculta_desde = r.json()["hidden_at"]
    # Ocultar de nuevo no mueve la fecha (SQLite la relee sin zona: se compara sin la `Z`).
    r = await client.patch(f"/v1/vehicle-makes/{alfa['id']}", json={"hidden": True})
    assert r.json()["hidden_at"].rstrip("Z") == oculta_desde.rstrip("Z")
    visibles = (await client.get("/v1/vehicle-makes", params={"limit": 200})).json()
    assert alfa["id"] not in {m["id"] for m in visibles["items"]}
    assert visibles["total"] == len(BASE.makes) - 1
    todas = (
        await client.get("/v1/vehicle-makes", params={"limit": 200, "include_hidden": True})
    ).json()
    assert alfa["id"] in {m["id"] for m in todas["items"]}
    # Sus modelos tampoco salen en el selector, ni por búsqueda ni pidiendo la marca.
    assert (await client.get("/v1/vehicle-models", params={"q": "giulietta"})).json()["total"] == 0
    por_marca = await client.get("/v1/vehicle-models", params={"make_id": alfa["id"]})
    assert por_marca.status_code == 200 and por_marca.json()["total"] == 0
    admin = await client.get(
        "/v1/vehicle-models", params={"make_id": alfa["id"], "include_hidden": True}
    )
    assert admin.json()["total"] == 4
    # Una marca oculta sigue ocupando su nombre.
    assert (await client.post("/v1/vehicle-makes", json={"name": "Alfa Romeo"})).status_code == 409
    r = await client.patch(f"/v1/vehicle-makes/{alfa['id']}", json={"hidden": False})
    assert r.json()["hidden_at"] is None
    assert (await client.get("/v1/vehicle-models", params={"q": "giulietta"})).json()["total"] == 1


async def test_ocultar_un_modelo_y_un_color(client, negocio):
    gol = await _modelo(client, "Volkswagen", "Gol")
    await client.patch(f"/v1/vehicle-models/{gol['id']}", json={"hidden": True})
    nombres = {
        m["name"]
        for m in (await client.get("/v1/vehicle-models", params={"q": "gol"})).json()["items"]
    }
    assert "Gol" not in nombres and "Gol Trend" in nombres
    assert (await client.get(f"/v1/vehicle-models/{gol['id']}")).status_code == 200
    beige = (await client.get("/v1/vehicle-colors", params={"q": "beige"})).json()["items"][0]
    await client.patch(f"/v1/vehicle-colors/{beige['id']}", json={"hidden": True})
    assert (await client.get("/v1/vehicle-colors", params={"q": "beige"})).json()["total"] == 0
    con_ocultos = await client.get(
        "/v1/vehicle-colors", params={"q": "beige", "include_hidden": True}
    )
    assert con_ocultos.json()["total"] == 1


async def test_patch_vacio_no_cambia_nada(client, negocio):
    fiat = await _marca(client, "Fiat")
    r = await client.patch(
        f"/v1/vehicle-makes/{fiat['id']}", json={"name": None, "is_custom": True}
    )
    assert r.status_code == 200
    assert (r.json()["name"], r.json()["is_custom"], r.json()["hidden_at"]) == ("Fiat", False, None)


async def test_idempotency_key_repetida_es_409(client, negocio):
    key = {"Idempotency-Key": f"k-{uuid.uuid4()}"}
    r1 = await client.post("/v1/vehicle-colors", json={"name": "Lila"}, headers=key)
    r2 = await client.post("/v1/vehicle-colors", json={"name": "Lila"}, headers=key)
    assert r1.status_code == 201
    assert r2.status_code == 409 and r2.json()["detail"]["code"] == "DUPLICATE_IDEMPOTENT"


async def test_modelo_de_marca_inexistente_es_404(client, negocio):
    r = await client.post(
        "/v1/vehicle-models", json={"make_id": str(uuid.uuid4()), "name": "X", "body_type": "SUV"}
    )
    assert r.status_code == 404
    r = await client.get("/v1/vehicle-models", params={"make_id": str(uuid.uuid4())})
    assert r.status_code == 404


# ── Carrocería → tamaño (A10) ────────────────────────────────────────────────


async def test_elegir_un_modelo_sugiere_el_tamano_de_su_carroceria(client, negocio):
    tamanos = await crear_tamanos(client, {}, "AUTO", "PICKUP")
    r = await client.put(
        "/v1/body-type-sizes",
        json={
            "items": [
                {"body_type": "PICKUP", "vehicle_size_id": tamanos["PICKUP"]},
                {"body_type": "SEDAN", "vehicle_size_id": tamanos["AUTO"]},
                {"body_type": "HATCHBACK", "vehicle_size_id": tamanos["AUTO"]},
            ]
        },
    )
    assert r.status_code == 200, r.text
    mapa = {i["body_type"]: i["vehicle_size_id"] for i in r.json()["items"]}
    assert mapa["PICKUP"] == tamanos["PICKUP"] and mapa["SUV"] is None
    assert r.json()["total"] == len(VehicleBodyType)

    hilux = await _modelo(client, "Toyota", "Hilux")
    assert hilux["suggested_vehicle_size_id"] == tamanos["PICKUP"]
    uno = (await client.get(f"/v1/vehicle-models/{hilux['id']}")).json()
    assert uno["suggested_vehicle_size_id"] == tamanos["PICKUP"]
    cronos = await _modelo(client, "Fiat", "Cronos")
    assert cronos["suggested_vehicle_size_id"] == tamanos["AUTO"]
    sw4 = await _modelo(client, "Toyota", "SW4")
    assert sw4["suggested_vehicle_size_id"] is None  # SUV sin tamaño

    # Corregir la carrocería del modelo cambia la sugerencia.
    r = await client.patch(f"/v1/vehicle-models/{sw4['id']}", json={"body_type": "PICKUP"})
    assert r.json()["suggested_vehicle_size_id"] == tamanos["PICKUP"]


async def test_put_reemplaza_el_mapa_entero(client, negocio):
    tamanos = await crear_tamanos(client, {}, "AUTO", "SUV")
    await client.put(
        "/v1/body-type-sizes",
        json={"items": [{"body_type": "SUV", "vehicle_size_id": tamanos["SUV"]}]},
    )
    r = await client.put(
        "/v1/body-type-sizes",
        json={
            "items": [
                {"body_type": "SUV", "vehicle_size_id": tamanos["AUTO"]},
                {"body_type": "MOTO", "vehicle_size_id": None},
            ]
        },
    )
    mapa = {i["body_type"]: i["vehicle_size_id"] for i in r.json()["items"]}
    assert mapa["SUV"] == tamanos["AUTO"] and mapa["MOTO"] is None
    r = await client.put("/v1/body-type-sizes", json={"items": []})
    assert all(i["vehicle_size_id"] is None for i in r.json()["items"])
    # Volver a poner la misma carrocería después de borrarla no choca con el único.
    r = await client.put(
        "/v1/body-type-sizes",
        json={"items": [{"body_type": "SUV", "vehicle_size_id": tamanos["SUV"]}]},
    )
    assert r.status_code == 200
    leido = (await client.get("/v1/body-type-sizes")).json()["items"]
    assert {i["body_type"]: i["vehicle_size_id"] for i in leido}["SUV"] == tamanos["SUV"]


async def test_put_rechaza_carroceria_repetida_y_tamano_desconocido(client, negocio):
    tamanos = await crear_tamanos(client, {}, "AUTO")
    repetida = await client.put(
        "/v1/body-type-sizes",
        json={
            "items": [
                {"body_type": "SUV", "vehicle_size_id": tamanos["AUTO"]},
                {"body_type": "SUV", "vehicle_size_id": None},
            ]
        },
    )
    assert repetida.status_code == 422
    desconocido = await client.put(
        "/v1/body-type-sizes",
        json={"items": [{"body_type": "SUV", "vehicle_size_id": str(uuid.uuid4())}]},
    )
    assert desconocido.status_code == 422
    assert desconocido.json()["detail"]["code"] == "VALIDATION_ERROR"
    invalida = await client.put(
        "/v1/body-type-sizes",
        json={"items": [{"body_type": "CAMION", "vehicle_size_id": None}]},
    )
    assert invalida.status_code == 422


async def test_un_tamano_anulado_deja_de_sugerirse(client, negocio):
    tamanos = await crear_tamanos(client, {}, "AUTO", "PICKUP")
    await client.put(
        "/v1/body-type-sizes",
        json={"items": [{"body_type": "PICKUP", "vehicle_size_id": tamanos["PICKUP"]}]},
    )
    assert (await client.delete(f"/v1/vehicle-sizes/{tamanos['PICKUP']}")).status_code == 204
    hilux = await _modelo(client, "Toyota", "Hilux")
    assert hilux["suggested_vehicle_size_id"] is None
    mapa = {
        i["body_type"]: i["vehicle_size_id"]
        for i in (await client.get("/v1/body-type-sizes")).json()["items"]
    }
    assert mapa["PICKUP"] is None
    # Y un PUT con el tamaño anulado se rechaza como uno desconocido.
    r = await client.put(
        "/v1/body-type-sizes",
        json={"items": [{"body_type": "PICKUP", "vehicle_size_id": tamanos["PICKUP"]}]},
    )
    assert r.status_code == 422


# ── Permisos (A3) ────────────────────────────────────────────────────────────


async def test_sin_catalogo_editar_lee_pero_no_escribe(client, db_session, negocio):
    cookies = await _staff(db_session, negocio, ALL_PERMISSIONS - {Permission.CATALOGO_EDITAR})
    client.cookies.clear()
    fiat = (await client.get("/v1/vehicle-makes", params={"q": "fiat"}, cookies=cookies)).json()[
        "items"
    ][0]
    cronos = (
        await client.get("/v1/vehicle-models", params={"q": "cronos"}, cookies=cookies)
    ).json()["items"][0]
    color = (await client.get("/v1/vehicle-colors", cookies=cookies)).json()["items"][0]
    for metodo, url in (
        ("GET", f"/v1/vehicle-makes/{fiat['id']}"),
        ("GET", f"/v1/vehicle-models/{cronos['id']}"),
        ("GET", f"/v1/vehicle-colors/{color['id']}"),
        ("GET", "/v1/body-type-sizes"),
    ):
        assert (await client.request(metodo, url, cookies=cookies)).status_code == 200, url
    escrituras = (
        ("POST", "/v1/vehicle-makes", {"name": "Nueva"}),
        ("PATCH", f"/v1/vehicle-makes/{fiat['id']}", {"hidden": True}),
        ("POST", "/v1/vehicle-models", {"make_id": fiat["id"], "name": "N", "body_type": "SUV"}),
        ("PATCH", f"/v1/vehicle-models/{cronos['id']}", {"name": "Otro"}),
        ("POST", "/v1/vehicle-colors", {"name": "Nuevo"}),
        ("PATCH", f"/v1/vehicle-colors/{color['id']}", {"hidden": True}),
        ("PUT", "/v1/body-type-sizes", {"items": []}),
    )
    for metodo, url, body in escrituras:
        r = await client.request(metodo, url, json=body, cookies=cookies)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "FORBIDDEN", (metodo, url)


async def test_con_catalogo_editar_escribe(client, db_session, negocio):
    cookies = await _staff(db_session, negocio, {Permission.CATALOGO_EDITAR})
    client.cookies.clear()
    r = await client.post("/v1/vehicle-makes", json={"name": "De Staff"}, cookies=cookies)
    assert r.status_code == 201
    r = await client.put("/v1/body-type-sizes", json={"items": []}, cookies=cookies)
    assert r.status_code == 200


async def test_sin_sesion_es_401(client, negocio):
    client.cookies.clear()
    for url in ("/v1/vehicle-makes", "/v1/vehicle-models", "/v1/vehicle-colors"):
        assert (await client.get(url)).status_code == 401
    assert (await client.put("/v1/body-type-sizes", json={"items": []})).status_code == 401
