"""Plantillas de servicios (PR 4.3): sugeridas por el glosario, con precio e idempotentes."""

import uuid

import pytest

from app.domain.glossary import TemplateKind, load_glossary
from app.tests.api._servicios_helpers import crear_servicio, crear_tamanos, precios


def _item(codigo: str, tamanos: dict[str, str], **extra):
    return {"glossary_code": codigo, "prices": precios(tamanos), **extra}


def _codigo(r) -> str:
    return r.json()["detail"]["code"]


@pytest.mark.parametrize("kind", ["lavadero", "detailing"])
async def test_la_plantilla_sugiere_solo_servicios_y_adicionales_sin_precios(
    client, cookies_a, kind
):
    r = await client.get(f"/v1/templates/{kind}", cookies=cookies_a)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["kind"] == kind
    esperado = {t.code for t in load_glossary().template(TemplateKind(kind))}
    items = [i for c in cuerpo["categories"] for i in c["items"]]
    assert {i["glossary_code"] for i in items} == esperado
    assert {i["type"] for i in items} <= {"servicio", "adicional"}
    assert all("price" not in " ".join(i) for i in items)  # el glosario no lleva precios
    assert not any(i["already_added"] for i in items)
    nombres = [c["name"] for c in cuerpo["categories"]]
    assert nombres[:2] == ["Lavado", "Interior"]
    assert len(nombres) == (2 if kind == "lavadero" else 4)


async def test_un_tipo_de_plantilla_desconocido_es_422(client, cookies_a):
    assert (await client.get("/v1/templates/taller", cookies=cookies_a)).status_code == 422


async def test_aplicar_crea_los_servicios_con_precios_y_categorias(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    r = await client.post(
        "/v1/templates/apply",
        json={
            "kind": "lavadero",
            "items": [_item("lavado_exterior", tamanos), _item("aspirado", tamanos)],
        },
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert [s["name"] for s in cuerpo["created"]] == ["Lavado exterior", "Aspirado"]
    assert cuerpo["skipped"] == []
    assert [c["name"] for c in cuerpo["categories_created"]] == ["Lavado", "Interior"]
    exterior = cuerpo["created"][0]
    assert exterior["glossary_code"] == "lavado_exterior" and exterior["category_id"]
    assert exterior["pricing_mode"] == "PRECIO_FIJO"
    assert {p["vehicle_size_id"] for p in exterior["prices"]} == set(tamanos.values())
    listado = (await client.get("/v1/services", cookies=cookies_a)).json()
    assert listado["total"] == 2
    marcados = (await client.get("/v1/templates/lavadero", cookies=cookies_a)).json()
    agregados = {
        i["glossary_code"] for c in marcados["categories"] for i in c["items"] if i["already_added"]
    }
    assert agregados == {"lavado_exterior", "aspirado"}


async def test_a8_una_plantilla_sin_precio_para_algun_tamano_no_se_aplica(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    completo = _item("lavado_exterior", tamanos)
    incompleto = {"glossary_code": "aspirado", "prices": precios(tamanos)[:1]}
    r = await client.post(
        "/v1/templates/apply",
        json={"kind": "lavadero", "items": [completo, incompleto]},
        cookies=cookies_a,
    )
    assert r.status_code == 422 and _codigo(r) == "VALIDATION_ERROR"
    assert "SUV" in r.json()["detail"]["message"]
    # Todo o nada: ni el servicio completo ni las categorías quedaron.
    assert (await client.get("/v1/services", cookies=cookies_a)).json()["total"] == 0
    assert (await client.get("/v1/service-categories", cookies=cookies_a)).json()["total"] == 0
    sin_precios = {"glossary_code": "aspirado", "prices": []}
    assert (
        await client.post(
            "/v1/templates/apply",
            json={"kind": "lavadero", "items": [sin_precios]},
            cookies=cookies_a,
        )
    ).status_code == 422


async def test_aplicar_dos_veces_no_duplica(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    cuerpo = {
        "kind": "detailing",
        "items": [_item("lavado_exterior", tamanos), _item("pulido", tamanos)],
    }
    primera = (await client.post("/v1/templates/apply", json=cuerpo, cookies=cookies_a)).json()
    segunda = await client.post("/v1/templates/apply", json=cuerpo, cookies=cookies_a)
    assert segunda.status_code == 200, segunda.text
    repetida = segunda.json()
    assert repetida["created"] == [] and repetida["categories_created"] == []
    assert {s["reason"] for s in repetida["skipped"]} == {"GLOSSARY_CODE"}
    assert {s["service_id"] for s in repetida["skipped"]} == {s["id"] for s in primera["created"]}
    assert (await client.get("/v1/services", cookies=cookies_a)).json()["total"] == 2
    assert (await client.get("/v1/service-categories", cookies=cookies_a)).json()["total"] == 2
    # Y no pisa precios: con otros precios en el cuerpo, el servicio existente queda como estaba.
    otros = {**cuerpo, "items": [_item("pulido", tamanos, prices=precios(tamanos, 9_000_000))]}
    await client.post("/v1/templates/apply", json=otros, cookies=cookies_a)
    pulido = (
        await client.get("/v1/services/search", params={"q": "pulido"}, cookies=cookies_a)
    ).json()
    assert pulido["items"][0]["prices"][0]["price_cents"] < 9_000_000


async def test_aplicar_un_lote_nuevo_mezcla_creados_y_salteados(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    uno = {"kind": "lavadero", "items": [_item("lavado_exterior", tamanos)]}
    await client.post("/v1/templates/apply", json=uno, cookies=cookies_a)
    dos = {
        "kind": "lavadero",
        "items": [_item("lavado_exterior", tamanos), _item("lavado_rapido", tamanos)],
    }
    r = (await client.post("/v1/templates/apply", json=dos, cookies=cookies_a)).json()
    assert [s["name"] for s in r["created"]] == ["Lavado rápido"]
    assert [s["glossary_code"] for s in r["skipped"]] == ["lavado_exterior"]
    assert r["categories_created"] == []  # "Lavado" ya existía


async def test_una_categoria_borrada_se_vuelve_a_crear_y_una_propia_se_reusa(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    propia = (
        await client.post("/v1/service-categories", json={"name": "Lavado"}, cookies=cookies_a)
    ).json()
    r = (
        await client.post(
            "/v1/templates/apply",
            json={"kind": "lavadero", "items": [_item("lavado_exterior", tamanos)]},
            cookies=cookies_a,
        )
    ).json()
    assert r["categories_created"] == []
    assert r["created"][0]["category_id"] == propia["id"]


async def test_un_servicio_propio_con_el_mismo_nombre_se_respeta(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    propio = await crear_servicio(client, cookies_a, tamanos, "Lavado exterior")
    r = await client.post(
        "/v1/templates/apply",
        json={
            "kind": "lavadero",
            "items": [_item("lavado_exterior", tamanos), _item("aspirado", tamanos)],
        },
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert [s["name"] for s in cuerpo["created"]] == ["Aspirado"]
    assert cuerpo["skipped"] == [
        {"glossary_code": "lavado_exterior", "service_id": propio["id"], "reason": "NAME"}
    ]
    # Solo se crea la categoría de la sección donde algo se creó (Interior), no la de Lavado.
    assert [c["name"] for c in cuerpo["categories_created"]] == ["Interior"]
    # El servicio propio no se vincula al término ni se toca.
    intacto = (await client.get(f"/v1/services/{propio['id']}", cookies=cookies_a)).json()
    assert intacto["glossary_code"] is None and intacto["category_id"] is None


async def test_un_servicio_propio_con_el_mismo_nombre_cuenta_como_ya_agregado(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    await crear_servicio(client, cookies_a, tamanos, "LAVADO   exterior")  # misma search_key
    plantilla = (await client.get("/v1/templates/lavadero", cookies=cookies_a)).json()
    estado = {
        i["glossary_code"]: i["already_added"] for c in plantilla["categories"] for i in c["items"]
    }
    assert estado["lavado_exterior"] is True
    assert estado["lavado_rapido"] is False


async def test_si_nada_de_una_seccion_se_crea_no_se_crea_su_categoria(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    await crear_servicio(client, cookies_a, tamanos, "Lavado exterior")
    r = await client.post(
        "/v1/templates/apply",
        json={"kind": "lavadero", "items": [_item("lavado_exterior", tamanos)]},
        cookies=cookies_a,
    )
    assert r.json()["created"] == [] and r.json()["categories_created"] == []
    assert (await client.get("/v1/service-categories", cookies=cookies_a)).json()["total"] == 0


async def test_items_fuera_de_la_plantilla_repetidos_o_inexistentes_son_422(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)

    async def aplicar(kind, *items):
        return await client.post(
            "/v1/templates/apply", json={"kind": kind, "items": list(items)}, cookies=cookies_a
        )

    # `pulido` es de detailing: no está en la plantilla de lavadero.
    assert (await aplicar("lavadero", _item("pulido", tamanos))).status_code == 422
    # Una técnica y un defecto nunca son plantilla.
    assert (await aplicar("detailing", _item("lavado_a_mano", tamanos))).status_code == 422
    assert (await aplicar("detailing", _item("swirls_telaranas", tamanos))).status_code == 422
    assert (await aplicar("lavadero", _item("inventado", tamanos))).status_code == 422
    assert (
        await aplicar("lavadero", _item("aspirado", tamanos), _item("aspirado", tamanos))
    ).status_code == 422
    assert (await aplicar("lavadero")).status_code == 422
    assert (await aplicar("taller", _item("aspirado", tamanos))).status_code == 422
    assert (await client.get("/v1/services", cookies=cookies_a)).json()["total"] == 0


async def test_aplicar_a_cotizar(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    r = await client.post(
        "/v1/templates/apply",
        json={
            "kind": "detailing",
            "items": [_item("limpieza_de_tapizados", tamanos, pricing_mode="A_COTIZAR")],
        },
        cookies=cookies_a,
    )
    assert r.json()["created"][0]["pricing_mode"] == "A_COTIZAR"


async def test_las_plantillas_son_solo_del_dueno(client, cookies_a, staff_cookies):
    tamanos = await crear_tamanos(client, cookies_a)
    assert (await client.get("/v1/templates/lavadero", cookies=staff_cookies)).status_code == 403
    r = await client.post(
        "/v1/templates/apply",
        json={"kind": "lavadero", "items": [_item("aspirado", tamanos)]},
        cookies=staff_cookies,
    )
    assert r.status_code == 403 and _codigo(r) == "FORBIDDEN"
    assert (await client.get("/v1/services", cookies=cookies_a)).json()["total"] == 0


async def test_el_tenant_id_del_cuerpo_se_ignora_y_dos_negocios_no_se_pisan(
    client, cookies_a, cookies_b, tenant_a, tenant_b
):
    tamanos_a = await crear_tamanos(client, cookies_a)
    tamanos_b = await crear_tamanos(client, cookies_b)
    cuerpo = lambda t: {  # noqa: E731
        "kind": "lavadero",
        "tenant_id": str(tenant_b.id),
        "items": [_item("aspirado", t)],
    }
    a = (await client.post("/v1/templates/apply", json=cuerpo(tamanos_a), cookies=cookies_a)).json()
    b = await client.post("/v1/templates/apply", json=cuerpo(tamanos_b), cookies=cookies_b)
    assert a["created"][0]["tenant_id"] == str(tenant_a.id)
    assert b.status_code == 200 and b.json()["created"][0]["tenant_id"] == str(tenant_b.id)
    # El mismo glossary_code convive en los dos negocios.
    assert b.json()["skipped"] == []
    # Los tamaños de A no sirven en B.
    cruzado = await client.post(
        "/v1/templates/apply",
        json={"kind": "lavadero", "items": [_item("lavado_exterior", tamanos_a)]},
        cookies=cookies_b,
    )
    assert cruzado.status_code == 422 and uuid.UUID(next(iter(tamanos_a.values())))


async def test_aplicar_toda_la_plantilla_no_hace_una_consulta_por_precio(
    client, cookies_a, db_session
):
    """56 servicios × 4 tamaños = 224 precios: las lecturas son por lote, no por ítem ni precio."""
    from sqlalchemy import event  # noqa: PLC0415

    tamanos = await crear_tamanos(client, cookies_a, "AUTO", "SUV", "PICKUP", "UTILITARIO")
    codigos = [t.code for t in load_glossary().template(TemplateKind.DETAILING)]
    assert len(codigos) == 56
    cuerpo = {"kind": "detailing", "items": [_item(c, tamanos) for c in codigos]}

    sentencias: list[str] = []
    motor = db_session.bind.sync_engine

    def contar(conn, cursor, statement, parameters, context, executemany):
        sentencias.append(statement.split(None, 1)[0].upper())

    event.listen(motor, "before_cursor_execute", contar)
    try:
        r = await client.post("/v1/templates/apply", json=cuerpo, cookies=cookies_a)
    finally:
        event.remove(motor, "before_cursor_execute", contar)
    assert r.status_code == 200, r.text
    assert len(r.json()["created"]) == 56
    assert all(len(s["prices"]) == 4 for s in r.json()["created"])
    selects = sentencias.count("SELECT")
    # Antes: ~1 consulta por precio (>= 224 SELECT solo para precios). Ahora, lecturas fijas.
    assert selects <= 15, f"{selects} SELECT"
    assert len(sentencias) <= 300, f"{len(sentencias)} sentencias en total"
    # Y reaplicar es barato: una lectura de servicios, ninguna escritura.
    sentencias.clear()
    event.listen(motor, "before_cursor_execute", contar)
    try:
        r2 = await client.post("/v1/templates/apply", json=cuerpo, cookies=cookies_a)
    finally:
        event.remove(motor, "before_cursor_execute", contar)
    assert len(r2.json()["skipped"]) == 56 and r2.json()["created"] == []
    assert "INSERT" not in sentencias and len(sentencias) <= 30
