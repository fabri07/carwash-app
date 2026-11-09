"""Servicios y categorías (PR 4.3): lo funcional, A8 y la matriz de permisos A5.

Aislamiento entre tenants: `tests/security/test_aislamiento_catalogo_servicios.py`.
Plantillas: `test_plantillas.py`.
"""

import uuid

import pytest

from app.domain.permissions import ALL_PERMISSIONS, Permission
from app.domain.roles import Role
from app.tests.api._servicios_helpers import (
    crear_servicio,
    crear_tamanos,
    cuerpo_servicio,
    precios,
)
from app.tests.conftest import make_profile, make_user, session_cookies


def _codigo(r) -> str:
    return r.json()["detail"]["code"]


# ── Categorías ───────────────────────────────────────────────────────────────


async def test_ciclo_de_una_categoria(client, cookies_a):
    creada = await client.post(
        "/v1/service-categories", json={"name": "Lavado", "sort_order": 2}, cookies=cookies_a
    )
    assert creada.status_code == 201, creada.text
    cid = creada.json()["id"]
    dup = await client.post("/v1/service-categories", json={"name": "Lavado"}, cookies=cookies_a)
    assert dup.status_code == 409 and _codigo(dup) == "NAME_TAKEN"
    r = await client.patch(
        f"/v1/service-categories/{cid}", json={"name": "Lavados"}, cookies=cookies_a
    )
    assert r.status_code == 200 and r.json()["name"] == "Lavados"
    otra = await client.post("/v1/service-categories", json={"name": "Interior"}, cookies=cookies_a)
    choque = await client.patch(
        f"/v1/service-categories/{otra.json()['id']}", json={"name": "Lavados"}, cookies=cookies_a
    )
    assert choque.status_code == 409 and _codigo(choque) == "NAME_TAKEN"
    lista = (await client.get("/v1/service-categories", cookies=cookies_a)).json()
    assert [c["name"] for c in lista["items"]] == ["Interior", "Lavados"]  # por sort_order
    assert (
        await client.delete(f"/v1/service-categories/{cid}", cookies=cookies_a)
    ).status_code == 204
    assert (await client.get(f"/v1/service-categories/{cid}", cookies=cookies_a)).status_code == 404
    # Anulada, libera el nombre.
    de_nuevo = await client.post(
        "/v1/service-categories", json={"name": "Lavados"}, cookies=cookies_a
    )
    assert de_nuevo.status_code == 201


async def test_no_se_anula_una_categoria_con_servicios_vivos(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    cat = (
        await client.post("/v1/service-categories", json={"name": "Lavado"}, cookies=cookies_a)
    ).json()
    servicio = await crear_servicio(
        client, cookies_a, tamanos, "Lavado exterior", category_id=cat["id"]
    )
    r = await client.delete(f"/v1/service-categories/{cat['id']}", cookies=cookies_a)
    assert r.status_code == 409 and _codigo(r) == "IN_USE"
    # Sacando el servicio, se puede.
    assert (
        await client.delete(f"/v1/services/{servicio['id']}", cookies=cookies_a)
    ).status_code == 204
    assert (
        await client.delete(f"/v1/service-categories/{cat['id']}", cookies=cookies_a)
    ).status_code == 204


# ── Alta de un servicio con precios (D4-6, A8) ───────────────────────────────


async def test_un_servicio_nace_con_precio_para_cada_tamano(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    r = await client.post("/v1/services", json=cuerpo_servicio(tamanos), cookies=cookies_a)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["name"] == "Lavado exterior" and s["pricing_mode"] == "PRECIO_FIJO"
    assert {p["vehicle_size_id"] for p in s["prices"]} == set(tamanos.values())
    assert all(p["price_cents"] > 0 and p["duration_min"] > 0 for p in s["prices"])
    leido = (await client.get(f"/v1/services/{s['id']}", cookies=cookies_a)).json()
    # SQLite devuelve las fechas sin zona; el resto (incluido el orden de los precios) es igual.
    assert {k: v for k, v in leido.items() if not k.endswith("_at")} == {
        k: v for k, v in s.items() if not k.endswith("_at")
    }


async def test_a8_un_servicio_sin_precio_para_algun_tamano_se_rechaza(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    incompleto = cuerpo_servicio(tamanos)
    incompleto["prices"] = incompleto["prices"][:1]  # falta SUV
    r = await client.post("/v1/services", json=incompleto, cookies=cookies_a)
    assert r.status_code == 422 and _codigo(r) == "VALIDATION_ERROR"
    assert "SUV" in r.json()["detail"]["message"]
    # Nada quedó a medias.
    assert (await client.get("/v1/services", cookies=cookies_a)).json()["total"] == 0


@pytest.mark.parametrize(
    "precio",
    [0, -5, None, "caro", 10_000_000_001],
    ids=["cero", "negativo", "nulo", "texto", "desborde"],
)
async def test_a8_ningun_precio_nulo_ni_invalido(client, cookies_a, precio):
    tamanos = await crear_tamanos(client, cookies_a)
    cuerpo = cuerpo_servicio(tamanos)
    cuerpo["prices"][0]["price_cents"] = precio
    r = await client.post("/v1/services", json=cuerpo, cookies=cookies_a)
    assert r.status_code == 422, r.text
    assert (await client.get("/v1/services", cookies=cookies_a)).json()["total"] == 0


async def test_a8_el_precio_se_exige_tambien_a_cotizar(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    cuerpo = cuerpo_servicio(tamanos, "Tapizado", pricing_mode="A_COTIZAR")
    ok = await client.post("/v1/services", json=cuerpo, cookies=cookies_a)
    assert ok.status_code == 201 and ok.json()["pricing_mode"] == "A_COTIZAR"
    sin = {**cuerpo, "name": "Otro", "prices": []}
    assert (await client.post("/v1/services", json=sin, cookies=cookies_a)).status_code == 422


async def test_tamano_ajeno_repetido_o_inexistente_es_422(client, cookies_a, cookies_b):
    propios = await crear_tamanos(client, cookies_a)
    ajenos = await crear_tamanos(client, cookies_b, "AUTO", "SUV")
    mezcla = cuerpo_servicio(propios)
    mezcla["prices"][1]["vehicle_size_id"] = ajenos["SUV"]
    r = await client.post("/v1/services", json=mezcla, cookies=cookies_a)
    assert r.status_code == 422 and "unknown vehicle size" in r.json()["detail"]["message"]
    repetido = cuerpo_servicio(propios)
    repetido["prices"][1]["vehicle_size_id"] = repetido["prices"][0]["vehicle_size_id"]
    assert (await client.post("/v1/services", json=repetido, cookies=cookies_a)).status_code == 422
    inexistente = cuerpo_servicio(propios)
    inexistente["prices"][1]["vehicle_size_id"] = str(uuid.uuid4())
    assert (
        await client.post("/v1/services", json=inexistente, cookies=cookies_a)
    ).status_code == 422


async def test_nombre_repetido_es_409_y_se_libera_al_anular(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    primero = await crear_servicio(client, cookies_a, tamanos, "Pulido")
    dup = await client.post(
        "/v1/services", json=cuerpo_servicio(tamanos, "Pulido"), cookies=cookies_a
    )
    assert dup.status_code == 409 and _codigo(dup) == "NAME_TAKEN"
    await client.delete(f"/v1/services/{primero['id']}", cookies=cookies_a)
    assert (
        await client.post(
            "/v1/services", json=cuerpo_servicio(tamanos, "Pulido"), cookies=cookies_a
        )
    ).status_code == 201


async def test_categoria_del_alta_y_glossary_code_fuera_del_body(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    inexistente = cuerpo_servicio(tamanos, "X", category_id=str(uuid.uuid4()))
    assert (
        await client.post("/v1/services", json=inexistente, cookies=cookies_a)
    ).status_code == 404
    # `glossary_code` ya no es del body (solo lo fija la plantilla): se ignora, no se guarda.
    con_codigo = cuerpo_servicio(tamanos, "Mi pulido", glossary_code="pulido")
    s = (await client.post("/v1/services", json=con_codigo, cookies=cookies_a)).json()
    assert s["glossary_code"] is None
    otro = cuerpo_servicio(tamanos, "Mi otro pulido", glossary_code="pulido")
    assert (await client.post("/v1/services", json=otro, cookies=cookies_a)).status_code == 201


# ── Listado, filtros y búsqueda ──────────────────────────────────────────────


async def test_listado_filtra_por_categoria_y_pagina(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    cat = (
        await client.post("/v1/service-categories", json={"name": "Lavado"}, cookies=cookies_a)
    ).json()
    await crear_servicio(client, cookies_a, tamanos, "Lavado exterior", category_id=cat["id"])
    await crear_servicio(client, cookies_a, tamanos, "Lavado rápido", category_id=cat["id"])
    await crear_servicio(client, cookies_a, tamanos, "Pulido")
    todos = (await client.get("/v1/services", cookies=cookies_a)).json()
    assert [s["name"] for s in todos["items"]] == ["Lavado exterior", "Lavado rápido", "Pulido"]
    assert all(len(s["prices"]) == 2 for s in todos["items"])
    de_cat = (await client.get(f"/v1/services?category_id={cat['id']}", cookies=cookies_a)).json()
    assert de_cat["total"] == 2
    pagina = (await client.get("/v1/services?limit=2&offset=2", cookies=cookies_a)).json()
    assert [s["name"] for s in pagina["items"]] == ["Pulido"]
    assert pagina["total"] == 3 and pagina["has_more"] is False


async def test_q_en_el_listado_usa_la_misma_busqueda(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    await crear_servicio(client, cookies_a, tamanos, "Lavado exterior")
    await crear_servicio(client, cookies_a, tamanos, "Pulido")
    r = (await client.get("/v1/services?q=lavad", cookies=cookies_a)).json()
    assert [s["name"] for s in r["items"]] == ["Lavado exterior"]
    # `q` vacío o que no deja nada que buscar ("?!") es "sin filtro": lista todo.
    for q in ("", "   ", "?!"):
        todos = (await client.get("/v1/services", params={"q": q}, cookies=cookies_a)).json()
        assert todos["total"] == 2, q


async def test_la_busqueda_ignora_tildes_mayusculas_y_tolera_errores(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    await crear_servicio(client, cookies_a, tamanos, "Limpieza de tapizados")
    for q in ("TAPIZADOS", "tapisados", "limpiesa", "tapizad"):
        r = (await client.get("/v1/services/search", params={"q": q}, cookies=cookies_a)).json()
        assert r["total"] == 1, q
    vacio = (await client.get("/v1/services/search", params={"q": "zzz"}, cookies=cookies_a)).json()
    assert vacio["items"] == [] and vacio["total"] == 0
    assert (await client.get("/v1/services/search", cookies=cookies_a)).status_code == 422


async def test_a9_por_la_api_con_servicios_de_la_plantilla_renombrados(client, cookies_a):
    """El `glossary_code` (lo fija la plantilla) da los sinónimos aunque se renombre el servicio."""
    tamanos = await crear_tamanos(client, cookies_a)
    codigos = (
        "tratamiento_ceramico",
        "film_de_proteccion_de_pintura",
        "correccion_de_pintura",
        "ceramico_de_llantas",
    )
    aplicado = await client.post(
        "/v1/templates/apply",
        json={
            "kind": "detailing",
            "items": [{"glossary_code": c, "prices": precios(tamanos)} for c in codigos],
        },
        cookies=cookies_a,
    )
    assert aplicado.status_code == 200, aplicado.text
    por_codigo = {s["glossary_code"]: s["id"] for s in aplicado.json()["created"]}
    for codigo, nombre in zip(
        codigos,
        ("Cerámico", "Protección PPF premium", "Pulida y corte", "Cerámico de llantas VIP"),
        strict=True,
    ):
        r = await client.patch(
            f"/v1/services/{por_codigo[codigo]}", json={"name": nombre}, cookies=cookies_a
        )
        assert r.status_code == 200, r.text

    async def buscar(q: str) -> list[str]:
        r = await client.get("/v1/services/search", params={"q": q}, cookies=cookies_a)
        return [s["name"] for s in r.json()["items"]]

    assert (await buscar("ceramico"))[0] == "Cerámico"
    assert (await buscar("ppf"))[0] == "Protección PPF premium"
    assert (await buscar("sacar rayas"))[0] == "Pulida y corte"
    assert (await buscar("paint correction"))[0] == "Pulida y corte"  # el nombre en inglés
    assert "Lavado completo" not in await buscar("sacar rayas")


async def test_renombrar_recalcula_la_clave_de_busqueda(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    s = await crear_servicio(client, cookies_a, tamanos, "Lavado exterior")
    r = await client.patch(
        f"/v1/services/{s['id']}", json={"name": "Espuma activa"}, cookies=cookies_a
    )
    assert r.status_code == 200 and r.json()["name"] == "Espuma activa"

    async def hits(q: str) -> int:
        return (await client.get("/v1/services/search", params={"q": q}, cookies=cookies_a)).json()[
            "total"
        ]

    assert await hits("espuma") == 1
    assert await hits("exterior") == 0


# ── PATCH sin precios; PUT de precios ────────────────────────────────────────


async def test_patch_edita_nombre_notas_y_categoria_pero_no_toca_precios(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    cat = (
        await client.post("/v1/service-categories", json={"name": "Lavado"}, cookies=cookies_a)
    ).json()
    s = await crear_servicio(client, cookies_a, tamanos, "Lavado exterior", notes="a mano")
    r = await client.patch(
        f"/v1/services/{s['id']}",
        json={
            "category_id": cat["id"],
            "notes": "con espuma",
            # Todo esto se ignora: el PATCH no tiene campos de precio (§2.3).
            "prices": [{"vehicle_size_id": str(uuid.uuid4()), "price_cents": 1, "duration_min": 1}],
            "price_cents": 1,
            "pricing_mode": "A_COTIZAR",
            "glossary_code": "pulido",
            "tenant_id": str(uuid.uuid4()),
        },
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    nuevo = r.json()
    assert nuevo["category_id"] == cat["id"] and nuevo["notes"] == "con espuma"
    assert nuevo["prices"] == s["prices"]
    assert nuevo["pricing_mode"] == "PRECIO_FIJO" and nuevo["glossary_code"] is None
    assert nuevo["tenant_id"] == s["tenant_id"]
    # `null` explícito borra notas y categoría; ausente no las toca.
    r = await client.patch(
        f"/v1/services/{s['id']}", json={"notes": None, "category_id": None}, cookies=cookies_a
    )
    assert r.json()["notes"] is None and r.json()["category_id"] is None
    r = await client.patch(f"/v1/services/{s['id']}", json={"name": None}, cookies=cookies_a)
    assert r.status_code == 422
    dup = await crear_servicio(client, cookies_a, tamanos, "Pulido")
    r = await client.patch(
        f"/v1/services/{dup['id']}", json={"name": "Lavado exterior"}, cookies=cookies_a
    )
    assert r.status_code == 409 and _codigo(r) == "NAME_TAKEN"


async def test_put_de_precios_actualiza_todos_los_tamanos_de_una_vez(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    s = await crear_servicio(client, cookies_a, tamanos, "Lavado exterior")
    nuevos = precios(tamanos, base=3_000_000)
    r = await client.put(
        f"/v1/services/{s['id']}/prices", json={"prices": nuevos}, cookies=cookies_a
    )
    assert r.status_code == 200, r.text
    por_tamano = {p["vehicle_size_id"]: p for p in r.json()["prices"]}
    assert len(por_tamano) == 2
    for n in nuevos:
        assert por_tamano[n["vehicle_size_id"]]["price_cents"] == n["price_cents"]
        assert por_tamano[n["vehicle_size_id"]]["duration_min"] == n["duration_min"]
    # Los precios son los mismos registros (se actualizan, no se duplican).
    assert {p["id"] for p in r.json()["prices"]} == {p["id"] for p in s["prices"]}


async def test_put_exige_un_precio_para_cada_tamano_vivo(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    s = await crear_servicio(client, cookies_a, tamanos, "Lavado exterior")
    r = await client.put(
        f"/v1/services/{s['id']}/prices", json={"prices": precios(tamanos)[:1]}, cookies=cookies_a
    )
    assert r.status_code == 422 and "SUV" in r.json()["detail"]["message"]
    assert (await client.get(f"/v1/services/{s['id']}", cookies=cookies_a)).json()["prices"] == s[
        "prices"
    ]
    # Un tamaño nuevo deja el servicio sin precio para él: el PUT siguiente tiene que cubrirlo.
    await client.post(
        "/v1/vehicle-sizes", json={"code": "PICKUP", "label": "Pick-up"}, cookies=cookies_a
    )
    r = await client.put(
        f"/v1/services/{s['id']}/prices", json={"prices": precios(tamanos)}, cookies=cookies_a
    )
    assert r.status_code == 422 and "PICKUP" in r.json()["detail"]["message"]


async def test_put_puede_cambiar_la_modalidad(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    s = await crear_servicio(client, cookies_a, tamanos, "Tapizado")
    r = await client.put(
        f"/v1/services/{s['id']}/prices",
        json={"pricing_mode": "A_COTIZAR", "prices": precios(tamanos)},
        cookies=cookies_a,
    )
    assert r.status_code == 200 and r.json()["pricing_mode"] == "A_COTIZAR"


async def test_anular_un_servicio_anula_sus_precios_y_libera_el_tamano(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    s = await crear_servicio(client, cookies_a, tamanos, "Lavado exterior")
    suv = tamanos["SUV"]
    con_precios = await client.delete(f"/v1/vehicle-sizes/{suv}", cookies=cookies_a)
    assert con_precios.status_code == 409 and _codigo(con_precios) == "IN_USE"
    assert (await client.delete(f"/v1/services/{s['id']}", cookies=cookies_a)).status_code == 204
    assert (await client.get(f"/v1/services/{s['id']}", cookies=cookies_a)).status_code == 404
    assert (await client.delete(f"/v1/vehicle-sizes/{suv}", cookies=cookies_a)).status_code == 204


async def test_idempotency_key_en_el_alta(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    key = {"Idempotency-Key": f"k-{uuid.uuid4()}"}
    r1 = await client.post(
        "/v1/services", json=cuerpo_servicio(tamanos), cookies=cookies_a, headers=key
    )
    r2 = await client.post(
        "/v1/services", json=cuerpo_servicio(tamanos), cookies=cookies_a, headers=key
    )
    assert r1.status_code == 201 and r2.status_code == 409
    assert _codigo(r2) == "DUPLICATE_IDEMPOTENT"
    assert (await client.get("/v1/services", cookies=cookies_a)).json()["total"] == 1


# ── A5 · CATALOGO_EDITAR y PRECIOS_EDITAR ────────────────────────────────────


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


@pytest.fixture
async def solo_catalogo(db_session, tenant_a):
    return await _empleado(db_session, tenant_a, "Solo catalogo", {Permission.CATALOGO_EDITAR})


@pytest.fixture
async def solo_precios(db_session, tenant_a):
    return await _empleado(db_session, tenant_a, "Solo precios", {Permission.PRECIOS_EDITAR})


@pytest.fixture
async def ambos(db_session, tenant_a):
    return await _empleado(
        db_session, tenant_a, "Ambos", {Permission.CATALOGO_EDITAR, Permission.PRECIOS_EDITAR}
    )


@pytest.fixture
async def sin_nada(db_session, tenant_a):
    permisos = ALL_PERMISSIONS - {Permission.CATALOGO_EDITAR, Permission.PRECIOS_EDITAR}
    return await _empleado(db_session, tenant_a, "Sin nada", set(permisos))


async def test_a5_catalogo_sin_precios_no_crea_servicios_ni_toca_precios(
    client, cookies_a, solo_catalogo
):
    tamanos = await crear_tamanos(client, cookies_a)
    s = await crear_servicio(client, cookies_a, tamanos, "Lavado exterior")
    crear = await client.post(
        "/v1/services", json=cuerpo_servicio(tamanos, "Otro"), cookies=solo_catalogo
    )
    assert crear.status_code == 403 and _codigo(crear) == "FORBIDDEN"
    precio = await client.put(
        f"/v1/services/{s['id']}/prices",
        json={"prices": precios(tamanos, base=9_000_000)},
        cookies=solo_catalogo,
    )
    assert precio.status_code == 403
    # Sí edita lo que no es precio.
    assert (
        await client.patch(f"/v1/services/{s['id']}", json={"notes": "x"}, cookies=solo_catalogo)
    ).status_code == 200
    assert (
        await client.post("/v1/service-categories", json={"name": "Lavado"}, cookies=solo_catalogo)
    ).status_code == 201
    # Anular también anula los precios: lo pide `PRECIOS_EDITAR` además de `CATALOGO_EDITAR` (§2.3).
    borrar = await client.delete(f"/v1/services/{s['id']}", cookies=solo_catalogo)
    assert borrar.status_code == 403 and _codigo(borrar) == "FORBIDDEN"
    # Nada cambió.
    intacto = (await client.get(f"/v1/services/{s['id']}", cookies=cookies_a)).json()
    assert intacto["prices"] == s["prices"]
    assert (await client.get("/v1/services", cookies=cookies_a)).json()["total"] == 1


async def test_a5_precios_sin_catalogo_cambia_precios_pero_no_crea_ni_edita(
    client, cookies_a, solo_precios
):
    tamanos = await crear_tamanos(client, cookies_a)
    s = await crear_servicio(client, cookies_a, tamanos, "Lavado exterior")
    crear = await client.post(
        "/v1/services", json=cuerpo_servicio(tamanos, "Otro"), cookies=solo_precios
    )
    assert crear.status_code == 403
    put = await client.put(
        f"/v1/services/{s['id']}/prices",
        json={"prices": precios(tamanos, base=5_000_000)},
        cookies=solo_precios,
    )
    assert put.status_code == 200, put.text
    assert {p["price_cents"] for p in put.json()["prices"]} == {5_000_000, 6_250_000}
    for r in (
        await client.patch(f"/v1/services/{s['id']}", json={"notes": "x"}, cookies=solo_precios),
        await client.delete(f"/v1/services/{s['id']}", cookies=solo_precios),
    ):
        assert r.status_code == 403


async def test_a5_con_los_dos_permisos_crea_el_servicio(client, cookies_a, ambos):
    tamanos = await crear_tamanos(client, cookies_a)
    r = await client.post("/v1/services", json=cuerpo_servicio(tamanos), cookies=ambos)
    assert r.status_code == 201, r.text
    assert (await client.delete(f"/v1/services/{r.json()['id']}", cookies=ambos)).status_code == 204


async def test_la_lectura_es_para_cualquier_usuario_autenticado(client, cookies_a, sin_nada):
    tamanos = await crear_tamanos(client, cookies_a)
    s = await crear_servicio(client, cookies_a, tamanos, "Lavado exterior")
    for url in (
        "/v1/services",
        f"/v1/services/{s['id']}",
        "/v1/services/search?q=lavado",
        "/v1/service-categories",
    ):
        assert (await client.get(url, cookies=sin_nada)).status_code == 200, url
    # El empleado ve precios aunque no los edite (D4-12).
    assert (await client.get("/v1/services", cookies=sin_nada)).json()["items"][0]["prices"]
    for r in (
        await client.post("/v1/services", json=cuerpo_servicio(tamanos, "Otro"), cookies=sin_nada),
        await client.post("/v1/service-categories", json={"name": "X"}, cookies=sin_nada),
        await client.put(
            f"/v1/services/{s['id']}/prices", json={"prices": precios(tamanos)}, cookies=sin_nada
        ),
        await client.delete(f"/v1/services/{s['id']}", cookies=sin_nada),
    ):
        assert r.status_code == 403


async def test_sin_sesion_todo_es_401(client):
    for metodo, url in (
        ("GET", "/v1/services"),
        ("GET", "/v1/services/search?q=a"),
        ("POST", "/v1/services"),
        ("PUT", f"/v1/services/{uuid.uuid4()}/prices"),
        ("GET", "/v1/service-categories"),
        ("GET", "/v1/templates/lavadero"),
        ("POST", "/v1/templates/apply"),
    ):
        assert (await client.request(metodo, url)).status_code == 401, (metodo, url)


# ── Tamaños sin precio (§6) ──────────────────────────────────────────────────


async def test_un_tamano_nuevo_no_se_bloquea_y_deja_el_servicio_marcado(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    s = await crear_servicio(client, cookies_a, tamanos, "Lavado exterior")
    assert s["missing_price_size_ids"] == []
    nuevo = await client.post(
        "/v1/vehicle-sizes", json={"code": "PICKUP", "label": "Pick-up"}, cookies=cookies_a
    )
    assert nuevo.status_code == 201, nuevo.text  # crear el tamaño no se bloquea
    pickup = nuevo.json()["id"]
    uno = (await client.get(f"/v1/services/{s['id']}", cookies=cookies_a)).json()
    assert uno["missing_price_size_ids"] == [pickup]
    lista = (await client.get("/v1/services", cookies=cookies_a)).json()["items"]
    assert lista[0]["missing_price_size_ids"] == [pickup]
    busqueda = (
        await client.get("/v1/services/search", params={"q": "lavado"}, cookies=cookies_a)
    ).json()["items"]
    assert busqueda[0]["missing_price_size_ids"] == [pickup]
    # Completando todos los precios, el marcador se va.
    completo = await client.put(
        f"/v1/services/{s['id']}/prices",
        json={"prices": precios({**tamanos, "PICKUP": pickup})},
        cookies=cookies_a,
    )
    assert completo.status_code == 200 and completo.json()["missing_price_size_ids"] == []
    # Un tamaño anulado ya no cuenta como faltante.
    await client.delete(f"/v1/vehicle-sizes/{pickup}", cookies=cookies_a)  # con precios: 409
    assert (await client.get(f"/v1/services/{s['id']}", cookies=cookies_a)).json()[
        "missing_price_size_ids"
    ] == []


# ── Anular un servicio en uso ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "abierto", ["turno", "cotizacion", "job"], ids=["turno abierto", "cotizacion", "job abierto"]
)
async def test_no_se_anula_un_servicio_que_usa_algo_abierto(
    client, db_session, tenant_a, owner, cookies_a, abierto
):
    from sqlalchemy import select  # noqa: PLC0415

    from app.domain.enums import BookingStatus, JobStatus, QuoteStatus  # noqa: PLC0415
    from app.persistence.models.agenda import Booking  # noqa: PLC0415
    from app.persistence.models.catalog import Service  # noqa: PLC0415
    from app.persistence.models.job import Job  # noqa: PLC0415
    from app.persistence.models.quote import Quote  # noqa: PLC0415
    from app.tests.persistence.test_esquema_dominio import alta_por_orm  # noqa: PLC0415

    ids = await alta_por_orm(db_session, tenant_a.id, owner.id)
    servicio = await db_session.scalar(select(Service).where(Service.tenant_id == tenant_a.id))
    url = f"/v1/services/{servicio.id}"

    # Con los tres abiertos (el armado trae turno CONFIRMADO, cotización PENDIENTE y job PRESENTE).
    r = await client.delete(url, cookies=cookies_a)
    assert r.status_code == 409 and _codigo(r) == "IN_USE"
    assert (await client.get(url, cookies=cookies_a)).status_code == 200

    # Se cierran los otros dos: queda uno solo, y alcanza para frenar.
    if abierto != "turno":
        (await db_session.get(Booking, ids["booking"])).status = BookingStatus.ATENDIDO
    if abierto != "cotizacion":
        (await db_session.get(Quote, ids["quote"])).status = QuoteStatus.RECHAZADO
    if abierto != "job":
        (await db_session.get(Job, ids["job"])).status = JobStatus.RETIRADO
    await db_session.flush()
    r = await client.delete(url, cookies=cookies_a)
    assert r.status_code == 409 and _codigo(r) == "IN_USE", abierto

    # Resuelto lo último, se anula (y los precios con él).
    if abierto == "turno":
        (await db_session.get(Booking, ids["booking"])).status = BookingStatus.CANCELADO_CLIENTE
    elif abierto == "cotizacion":
        (await db_session.get(Quote, ids["quote"])).status = QuoteStatus.VENCIDO
    else:
        (await db_session.get(Job, ids["job"])).status = JobStatus.CANCELADO_DEMORA
    await db_session.flush()
    assert (await client.delete(url, cookies=cookies_a)).status_code == 204


async def test_un_nombre_sin_nada_buscable_se_rechaza_con_un_mensaje_claro(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    for nombre in ("洗車", "​", "!!!", "  "):
        r = await client.post(
            "/v1/services", json=cuerpo_servicio(tamanos, nombre), cookies=cookies_a
        )
        assert r.status_code == 422, repr(nombre)
        c = await client.post("/v1/service-categories", json={"name": nombre}, cookies=cookies_a)
        assert c.status_code == 422, repr(nombre)
    r = await client.post("/v1/services", json=cuerpo_servicio(tamanos, "洗車"), cookies=cookies_a)
    assert "cannot be searched" in r.text
    # Con letras latinas y acentos sí (ñ y tildes se normalizan).
    ok = await client.post(
        "/v1/services", json=cuerpo_servicio(tamanos, "Ñandú 洗"), cookies=cookies_a
    )
    assert ok.status_code == 201
    # Renombrar a algo inencontrable tampoco.
    r = await client.patch(
        f"/v1/services/{ok.json()['id']}", json={"name": "洗車"}, cookies=cookies_a
    )
    assert r.status_code == 422


async def test_precio_y_duracion_son_enteros_estrictos(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a, "AUTO")
    for campo, valor in (
        ("price_cents", True),
        ("duration_min", True),
        ("price_cents", "100"),
        ("price_cents", 10.5),
    ):
        cuerpo = cuerpo_servicio(tamanos, "Estricto")
        cuerpo["prices"][0][campo] = valor
        r = await client.post("/v1/services", json=cuerpo, cookies=cookies_a)
        assert r.status_code == 422, (campo, valor)
