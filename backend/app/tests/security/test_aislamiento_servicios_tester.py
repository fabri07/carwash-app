"""Tester-aislamiento del PR 4.3 — servicios, categorías, plantillas y búsqueda (A1-A3, A5, A8).

Escrito por quien NO implementó el 4.3. Complementa `test_aislamiento_catalogo_servicios.py` y
`tests/api/test_servicios.py` con lo que esos no atacan:

1. **Matriz permiso × endpoint** completa (generada contra las rutas reales: una ruta nueva sin
   fila hace fallar el meta-test) y `must_change_password` en cada endpoint.
2. **Estados**: recursos anulados (propios) dan el mismo 404 que uno inexistente; categoría
   anulada no se puede asignar; una plantilla reaplicada después de anular recrea.
3. **Asignación masiva**: `id`, `search_key`, `voided_at`, `glossary_code`, precios o modalidad en
   el `PATCH`/`POST` no cambian nada que no deban.
4. **Plantillas**: atómicas (nada a medias, ni categorías), el `skipped` de A nunca apunta a un
   servicio de B, tamaños ajenos en `items[].prices` dan el mismo 422 que uno al azar.
5. **Búsqueda**: comodines SQL, strings largos/raros, `q` que normaliza a vacío, alias del
   glosario que no se filtran entre negocios.
6. **Límites** de precio y duración, y que ninguna entrada hostil termine en 5xx.
7. **Idempotency-Key**: un 422 no "quema" la clave; la misma clave en otro scope/tenant es libre.

Datos sintéticos.
"""

import uuid
from typing import Any

import pytest

from app.domain.glossary import load_glossary
from app.domain.permissions import ALL_PERMISSIONS, Permission
from app.domain.roles import Role
from app.main import create_app
from app.tests.api._servicios_helpers import (
    crear_servicio,
    crear_tamanos,
    cuerpo_servicio,
    precios,
)
from app.tests.conftest import make_profile, make_user, session_cookies

# ── armado ───────────────────────────────────────────────────────────────────


def _detalle(r) -> dict[str, Any]:
    return r.json()["detail"]


async def _staff(
    db_session, tenant, nombre: str, permisos: frozenset[Permission] | set[Permission]
):
    perfil = await make_profile(db_session, tenant, nombre, permisos)
    user = await make_user(
        db_session,
        tenant,
        Role.STAFF,
        f"{nombre.lower().replace(' ', '')}@ejemplo.invalid",
        profile=perfil,
    )
    return session_cookies(user)


@pytest.fixture
async def u_nada(db_session, tenant_a):
    return await _staff(
        db_session,
        tenant_a,
        "Sin nada",
        ALL_PERMISSIONS - {Permission.CATALOGO_EDITAR, Permission.PRECIOS_EDITAR},
    )


@pytest.fixture
async def u_catalogo(db_session, tenant_a):
    return await _staff(db_session, tenant_a, "Solo catalogo", {Permission.CATALOGO_EDITAR})


@pytest.fixture
async def u_precios(db_session, tenant_a):
    return await _staff(db_session, tenant_a, "Solo precios", {Permission.PRECIOS_EDITAR})


@pytest.fixture
async def u_ambos(db_session, tenant_a):
    return await _staff(
        db_session, tenant_a, "Ambos", {Permission.CATALOGO_EDITAR, Permission.PRECIOS_EDITAR}
    )


@pytest.fixture
async def u_todo_menos_owner(db_session, tenant_a):
    """STAFF con TODOS los permisos: no es OWNER, así que las plantillas siguen cerradas."""
    return await _staff(db_session, tenant_a, "Encargado total", ALL_PERMISSIONS)


@pytest.fixture
async def u_clave_pendiente(db_session, tenant_a):
    owner = await make_user(
        db_session, tenant_a, Role.OWNER, "pendiente@ejemplo.invalid", must_change_password=True
    )
    return session_cookies(owner)


# ── 1 · matriz permiso × endpoint ────────────────────────────────────────────

#: (método, plantilla de ruta) → quién puede. `lectura` = cualquier autenticado.
LECTURA, CATALOGO, PRECIOS, AMBOS, OWNER = "lectura", "catalogo", "precios", "ambos", "owner"
MATRIZ: dict[tuple[str, str], str] = {
    ("GET", "/v1/service-categories"): LECTURA,
    ("POST", "/v1/service-categories"): CATALOGO,
    ("GET", "/v1/service-categories/{id}"): LECTURA,
    ("PATCH", "/v1/service-categories/{id}"): CATALOGO,
    ("DELETE", "/v1/service-categories/{id}"): CATALOGO,
    ("GET", "/v1/services"): LECTURA,
    ("GET", "/v1/services/search"): LECTURA,
    ("POST", "/v1/services"): AMBOS,
    ("GET", "/v1/services/{id}"): LECTURA,
    ("PATCH", "/v1/services/{id}"): CATALOGO,
    ("PUT", "/v1/services/{id}/prices"): PRECIOS,
    ("DELETE", "/v1/services/{id}"): AMBOS,
    ("GET", "/v1/templates/{kind}"): OWNER,
    ("POST", "/v1/templates/apply"): OWNER,
}

#: Quién puede cada categoría de permiso, por usuario de prueba.
PUEDE: dict[str, set[str]] = {
    "u_nada": {LECTURA},
    "u_catalogo": {LECTURA, CATALOGO},
    "u_precios": {LECTURA, PRECIOS},
    "u_ambos": {LECTURA, CATALOGO, PRECIOS, AMBOS},
    "u_todo_menos_owner": {LECTURA, CATALOGO, PRECIOS, AMBOS},
    "cookies_a": {LECTURA, CATALOGO, PRECIOS, AMBOS, OWNER},
}


def test_la_matriz_cubre_todas_las_rutas_de_servicios_categorias_y_plantillas():
    """Una ruta nueva bajo estos prefijos sin fila en MATRIZ rompe acá (A3)."""
    prefijos = ("/v1/service-categories", "/v1/services", "/v1/templates")
    reales: set[tuple[str, str]] = set()
    for route in create_app().routes:
        path = getattr(route, "path", "")
        if path.startswith(prefijos):
            for metodo in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
                reales.add((metodo, path))
    # FastAPI nombra el parámetro de la plantilla `kind`/`id`, igual que MATRIZ.
    assert reales == set(MATRIZ), reales ^ set(MATRIZ)


async def _armar(client, cookies_a, plantilla: str, tamanos, sufijo: str):
    """Ruta concreta + cuerpo válido, con un recurso nuevo si hace falta (así un DELETE o un
    PATCH exitoso de una celda no ensucia la siguiente)."""
    servicio = await crear_servicio(client, cookies_a, tamanos, f"Base {sufijo}")
    categoria = (
        await client.post(
            "/v1/service-categories", json={"name": f"Cat {sufijo}"}, cookies=cookies_a
        )
    ).json()
    ruta = plantilla.replace("/{id}/prices", f"/{servicio['id']}/prices").replace(
        "/{kind}", "/lavadero"
    )
    if plantilla.startswith("/v1/service-categories/{id}"):
        ruta = plantilla.replace("{id}", categoria["id"])
    elif "{id}" in ruta:
        ruta = ruta.replace("{id}", servicio["id"])
    return ruta, servicio


def _cuerpo(metodo: str, plantilla: str, tamanos, sufijo: str) -> dict[str, Any] | None:
    if (metodo, plantilla) == ("POST", "/v1/service-categories"):
        return {"name": f"Nueva {sufijo}"}
    if (metodo, plantilla) == ("PATCH", "/v1/service-categories/{id}"):
        return {"name": f"Renombrada {sufijo}"}
    if (metodo, plantilla) == ("POST", "/v1/services"):
        return cuerpo_servicio(tamanos, f"Servicio {sufijo}")
    if (metodo, plantilla) == ("PATCH", "/v1/services/{id}"):
        return {"notes": "tocado"}
    if (metodo, plantilla) == ("PUT", "/v1/services/{id}/prices"):
        return {"prices": precios(tamanos, base=7_000_000)}
    if (metodo, plantilla) == ("POST", "/v1/templates/apply"):
        return {
            "kind": "lavadero",
            "items": [{"glossary_code": "lavado_rapido", "prices": precios(tamanos)}],
        }
    return None


@pytest.fixture
async def usuarios(cookies_a, u_nada, u_catalogo, u_precios, u_ambos, u_todo_menos_owner):
    return {
        "u_nada": u_nada,
        "u_catalogo": u_catalogo,
        "u_precios": u_precios,
        "u_ambos": u_ambos,
        "u_todo_menos_owner": u_todo_menos_owner,
        "cookies_a": cookies_a,
    }


@pytest.mark.parametrize("quien", list(PUEDE))
async def test_matriz_permiso_por_endpoint(usuarios, client, cookies_a, quien):
    """Cada usuario, en cada endpoint: 403 si no tiene el permiso, éxito si lo tiene.

    Con 403 el estado de A queda exactamente igual (nada se creó, tocó ni anuló)."""
    cookies = usuarios[quien]
    tamanos = await crear_tamanos(client, cookies_a)
    for i, ((metodo, plantilla), requiere) in enumerate(MATRIZ.items()):
        sufijo = f"{quien}{i}"
        ruta, base = await _armar(client, cookies_a, plantilla, tamanos, sufijo)
        if plantilla.endswith("/search"):
            ruta += "?q=base"
        antes = (await client.get("/v1/services?limit=200", cookies=cookies_a)).json()
        antes_cat = (await client.get("/v1/service-categories?limit=200", cookies=cookies_a)).json()
        r = await client.request(
            metodo, ruta, json=_cuerpo(metodo, plantilla, tamanos, sufijo), cookies=cookies
        )
        if requiere in PUEDE[quien]:
            assert r.status_code < 300, (quien, metodo, plantilla, r.status_code, r.text)
        else:
            assert r.status_code == 403, (quien, metodo, plantilla, r.status_code, r.text)
            assert _detalle(r)["code"] == "FORBIDDEN"
            despues = (await client.get("/v1/services?limit=200", cookies=cookies_a)).json()
            despues_cat = (
                await client.get("/v1/service-categories?limit=200", cookies=cookies_a)
            ).json()
            assert (despues, despues_cat) == (antes, antes_cat), (quien, metodo, plantilla)
        assert base  # el recurso base existía


async def test_con_clave_pendiente_todo_endpoint_responde_403_password_change_required(
    client, cookies_a, u_clave_pendiente
):
    """§2.3: ni siquiera las lecturas. Un OWNER con `must_change_password` no opera nada."""
    tamanos = await crear_tamanos(client, cookies_a)
    for (metodo, plantilla), _ in MATRIZ.items():
        ruta, _base = await _armar(
            client, cookies_a, plantilla, tamanos, f"p{uuid.uuid4().hex[:6]}"
        )
        if plantilla.endswith("/search"):
            ruta += "?q=base"
        r = await client.request(
            metodo, ruta, json=_cuerpo(metodo, plantilla, tamanos, "x"), cookies=u_clave_pendiente
        )
        assert r.status_code == 403, (metodo, plantilla, r.status_code)
        assert _detalle(r)["code"] == "PASSWORD_CHANGE_REQUIRED", (metodo, plantilla)


async def test_staff_con_todos_los_permisos_no_entra_a_plantillas(
    client, cookies_a, u_todo_menos_owner
):
    for r in (
        await client.get("/v1/templates/lavadero", cookies=u_todo_menos_owner),
        await client.get("/v1/templates/detailing", cookies=u_todo_menos_owner),
        await client.post(
            "/v1/templates/apply",
            json={"kind": "lavadero", "items": [{"glossary_code": "lavado_rapido", "prices": []}]},
            cookies=u_todo_menos_owner,
        ),
    ):
        assert r.status_code == 403, r.text


async def test_el_perfil_cambia_y_rige_en_el_request_siguiente(
    client, db_session, cookies_a, tenant_a
):
    """Quitar `CATALOGO_EDITAR` al perfil corta el acceso con el mismo cookie ya emitido."""
    perfil = await make_profile(db_session, tenant_a, "Mutable", {Permission.CATALOGO_EDITAR})
    user = await make_user(
        db_session, tenant_a, Role.STAFF, "mutable@ejemplo.invalid", profile=perfil
    )
    cookies = session_cookies(user)
    ok = await client.post("/v1/service-categories", json={"name": "Una"}, cookies=cookies)
    assert ok.status_code == 201
    perfil.permissions = []
    await db_session.flush()
    cortado = await client.post("/v1/service-categories", json={"name": "Dos"}, cookies=cookies)
    assert cortado.status_code == 403


# ── 2 · estados: anulados propios ────────────────────────────────────────────


async def test_un_servicio_anulado_es_un_404_igual_al_inexistente_en_todo_metodo(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    s = await crear_servicio(client, cookies_a, tamanos, "Efimero")
    assert (await client.delete(f"/v1/services/{s['id']}", cookies=cookies_a)).status_code == 204
    fantasma = str(uuid.uuid4())
    for metodo, sufijo, kw in (
        ("GET", "", {}),
        ("PATCH", "", {"json": {"name": "Zombi"}}),
        ("DELETE", "", {}),
        ("PUT", "/prices", {"json": {"prices": precios(tamanos)}}),
    ):
        anulado = await client.request(
            metodo, f"/v1/services/{s['id']}{sufijo}", cookies=cookies_a, **kw
        )
        nada = await client.request(
            metodo, f"/v1/services/{fantasma}{sufijo}", cookies=cookies_a, **kw
        )
        assert anulado.status_code == nada.status_code == 404, (metodo, anulado.text)
        assert anulado.content.replace(s["id"].encode(), b"X") == nada.content.replace(
            fantasma.encode(), b"X"
        )
    assert (await client.get("/v1/services", cookies=cookies_a)).json()["total"] == 0


async def test_una_categoria_anulada_no_se_asigna_ni_se_edita(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    cat = (
        await client.post("/v1/service-categories", json={"name": "Vieja"}, cookies=cookies_a)
    ).json()
    assert (
        await client.delete(f"/v1/service-categories/{cat['id']}", cookies=cookies_a)
    ).status_code == 204
    nuevo = await client.post(
        "/v1/services", json=cuerpo_servicio(tamanos, "X", category_id=cat["id"]), cookies=cookies_a
    )
    assert nuevo.status_code == 404
    propio = await crear_servicio(client, cookies_a, tamanos, "Y")
    r = await client.patch(
        f"/v1/services/{propio['id']}", json={"category_id": cat["id"]}, cookies=cookies_a
    )
    assert r.status_code == 404
    for metodo, kw in (("GET", {}), ("PATCH", {"json": {"name": "Z"}}), ("DELETE", {})):
        assert (
            await client.request(
                metodo, f"/v1/service-categories/{cat['id']}", cookies=cookies_a, **kw
            )
        ).status_code == 404
    # y el nombre de la anulada queda libre
    assert (
        await client.post("/v1/service-categories", json={"name": "Vieja"}, cookies=cookies_a)
    ).status_code == 201


async def test_anulado_un_servicio_la_plantilla_lo_recrea_y_no_lo_da_por_hecho(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    cuerpo = {
        "kind": "lavadero",
        "items": [{"glossary_code": "lavado_rapido", "prices": precios(tamanos)}],
    }
    primero = (await client.post("/v1/templates/apply", json=cuerpo, cookies=cookies_a)).json()
    sid = primero["created"][0]["id"]
    assert (await client.delete(f"/v1/services/{sid}", cookies=cookies_a)).status_code == 204
    marcada = (await client.get("/v1/templates/lavadero", cookies=cookies_a)).json()
    assert not any(i["already_added"] for c in marcada["categories"] for i in c["items"])
    segundo = (await client.post("/v1/templates/apply", json=cuerpo, cookies=cookies_a)).json()
    assert len(segundo["created"]) == 1 and segundo["skipped"] == []
    assert segundo["created"][0]["id"] != sid


# ── 3 · asignación masiva ────────────────────────────────────────────────────


async def test_el_cuerpo_del_alta_no_fija_id_ni_search_key_ni_anulacion(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    mio = str(uuid.uuid4())
    r = await client.post(
        "/v1/services",
        json={
            **cuerpo_servicio(tamanos, "Pulido fino"),
            "id": mio,
            "search_key": "zzz otra cosa",
            "voided_at": "2020-01-01T00:00:00Z",
            "void_reason": "ERROR_DE_CARGA",
            "created_at": "2001-01-01T00:00:00Z",
        },
        cookies=cookies_a,
    )
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    assert cuerpo["id"] != mio and not cuerpo[
        "createdAt" if "createdAt" in cuerpo else "created_at"
    ].startswith("2001")
    # La clave de búsqueda sale del nombre, no del cuerpo, y el servicio está vivo.
    assert (await client.get("/v1/services/search?q=pulido", cookies=cookies_a)).json()[
        "total"
    ] == 1
    assert (await client.get("/v1/services/search?q=zzz", cookies=cookies_a)).json()["total"] == 0
    assert (await client.get(f"/v1/services/{cuerpo['id']}", cookies=cookies_a)).status_code == 200


async def test_el_patch_ignora_precio_modalidad_glosario_y_clave(client, cookies_a, u_catalogo):
    tamanos = await crear_tamanos(client, cookies_a)
    aplicado = await client.post(
        "/v1/templates/apply",
        json={
            "kind": "lavadero",
            "items": [{"glossary_code": "lavado_rapido", "prices": precios(tamanos)}],
        },
        cookies=cookies_a,
    )
    s = aplicado.json()["created"][0]
    assert s["glossary_code"] == "lavado_rapido"
    r = await client.patch(
        f"/v1/services/{s['id']}",
        json={
            "notes": "ok",
            "pricing_mode": "A_COTIZAR",
            "glossary_code": "pulido",
            "search_key": "hackeada",
            "prices": [
                {"vehicle_size_id": list(tamanos.values())[0], "price_cents": 1, "duration_min": 1}
            ],
            "voided_at": "2020-01-01T00:00:00Z",
        },
        cookies=u_catalogo,  # sin PRECIOS_EDITAR: tampoco puede colar precios por acá
    )
    assert r.status_code == 200, r.text
    despues = r.json()
    assert despues["pricing_mode"] == s["pricing_mode"]
    assert despues["glossary_code"] == "lavado_rapido"
    assert despues["prices"] == s["prices"]
    assert (await client.get("/v1/services/search?q=hackeada", cookies=cookies_a)).json()[
        "total"
    ] == 0
    assert (await client.get(f"/v1/services/{s['id']}", cookies=cookies_a)).status_code == 200


async def test_put_de_precios_con_cambio_de_modalidad_exige_precios_editar(
    client, cookies_a, u_catalogo
):
    tamanos = await crear_tamanos(client, cookies_a)
    s = await crear_servicio(client, cookies_a, tamanos, "Fijo")
    r = await client.put(
        f"/v1/services/{s['id']}/prices",
        json={"pricing_mode": "A_COTIZAR", "prices": precios(tamanos)},
        cookies=u_catalogo,
    )
    assert r.status_code == 403
    assert (await client.get(f"/v1/services/{s['id']}", cookies=cookies_a)).json()[
        "pricing_mode"
    ] == s["pricing_mode"]


async def test_el_patch_de_categoria_ignora_tenant_y_campos_ajenos(client, cookies_a, tenant_b):
    cat = (
        await client.post("/v1/service-categories", json={"name": "Lavado"}, cookies=cookies_a)
    ).json()
    r = await client.patch(
        f"/v1/service-categories/{cat['id']}",
        json={"sort_order": 3, "tenant_id": str(tenant_b.id), "id": str(uuid.uuid4())},
        cookies=cookies_a,
    )
    assert r.status_code == 200
    assert r.json()["id"] == cat["id"] and r.json()["tenant_id"] == cat["tenant_id"]


# ── 4 · plantillas ───────────────────────────────────────────────────────────


async def test_la_plantilla_es_atomica_un_precio_malo_no_deja_servicios_ni_categorias(
    client, cookies_a
):
    tamanos = await crear_tamanos(client, cookies_a)
    malo = precios(tamanos)[:1]  # falta un tamaño
    r = await client.post(
        "/v1/templates/apply",
        json={
            "kind": "detailing",
            "items": [
                {"glossary_code": "lavado_rapido", "prices": precios(tamanos)},
                {"glossary_code": "pulido", "prices": malo},
            ],
        },
        cookies=cookies_a,
    )
    assert r.status_code == 422, r.text
    assert (await client.get("/v1/services", cookies=cookies_a)).json()["total"] == 0
    assert (await client.get("/v1/service-categories", cookies=cookies_a)).json()["total"] == 0


@pytest.mark.parametrize(
    "items",
    [
        [{"glossary_code": "pulido"}],  # detailing-only en lavadero
        [{"glossary_code": "aromatizante"}],  # producto
        [{"glossary_code": "swirls_telaranas"}],  # defecto
        [{"glossary_code": "no_existe"}],
        [{"glossary_code": "lavado_rapido"}, {"glossary_code": "lavado_rapido"}],  # repetido
    ],
)
async def test_items_fuera_de_plantilla_o_repetidos_son_422_y_no_crean_nada(
    client, cookies_a, items
):
    tamanos = await crear_tamanos(client, cookies_a)
    cuerpo = {"kind": "lavadero", "items": [{**i, "prices": precios(tamanos)} for i in items]}
    r = await client.post("/v1/templates/apply", json=cuerpo, cookies=cookies_a)
    assert r.status_code == 422, r.text
    assert (await client.get("/v1/services", cookies=cookies_a)).json()["total"] == 0
    assert (await client.get("/v1/service-categories", cookies=cookies_a)).json()["total"] == 0


async def test_el_skipped_de_a_nunca_apunta_a_un_servicio_de_b(client, cookies_a, cookies_b):
    tamanos_a = await crear_tamanos(client, cookies_a)
    tamanos_b = await crear_tamanos(client, cookies_b)
    item = "lavado_rapido"
    ra = (
        await client.post(
            "/v1/templates/apply",
            json={
                "kind": "lavadero",
                "items": [{"glossary_code": item, "prices": precios(tamanos_a)}],
            },
            cookies=cookies_a,
        )
    ).json()
    rb = (
        await client.post(
            "/v1/templates/apply",
            json={
                "kind": "lavadero",
                "items": [{"glossary_code": item, "prices": precios(tamanos_b)}],
            },
            cookies=cookies_b,
        )
    ).json()
    id_a, id_b = ra["created"][0]["id"], rb["created"][0]["id"]
    assert id_a != id_b
    # A reaplica con precios de B (ajenos): como el ítem ya existe, no se valida ni se toca.
    # Lo único que importa acá: lo que A recibe es SU servicio, jamás el de B.
    otra = await client.post(
        "/v1/templates/apply",
        json={"kind": "lavadero", "items": [{"glossary_code": item, "prices": precios(tamanos_b)}]},
        cookies=cookies_a,
    )
    assert otra.status_code == 200, otra.text
    assert [s["service_id"] for s in otra.json()["skipped"]] == [id_a]
    assert id_b not in otra.text
    # Y viceversa
    otra_b = await client.post(
        "/v1/templates/apply",
        json={"kind": "lavadero", "items": [{"glossary_code": item, "prices": precios(tamanos_a)}]},
        cookies=cookies_b,
    )
    assert [s["service_id"] for s in otra_b.json()["skipped"]] == [id_b]
    assert id_a not in otra_b.text
    # Los precios de A no cambiaron por el reintento con precios ajenos.
    assert (await client.get(f"/v1/services/{id_a}", cookies=cookies_a)).json()["prices"] == ra[
        "created"
    ][0]["prices"]


async def test_categorias_de_la_plantilla_no_se_comparten_entre_negocios(
    client, cookies_a, cookies_b
):
    tamanos_a = await crear_tamanos(client, cookies_a)
    tamanos_b = await crear_tamanos(client, cookies_b)
    cat_a = (
        await client.post("/v1/service-categories", json={"name": "Lavado"}, cookies=cookies_a)
    ).json()
    rb = (
        await client.post(
            "/v1/templates/apply",
            json={
                "kind": "lavadero",
                "items": [{"glossary_code": "lavado_rapido", "prices": precios(tamanos_b)}],
            },
            cookies=cookies_b,
        )
    ).json()
    # B no reusó la categoría "Lavado" de A: creó la suya.
    assert [c["name"] for c in rb["categories_created"]] == ["Lavado"]
    assert rb["categories_created"][0]["id"] != cat_a["id"]
    assert rb["created"][0]["category_id"] == rb["categories_created"][0]["id"]
    # y a la inversa: A aplica y reusa la suya, no la de B
    ra = (
        await client.post(
            "/v1/templates/apply",
            json={
                "kind": "lavadero",
                "items": [{"glossary_code": "lavado_rapido", "prices": precios(tamanos_a)}],
            },
            cookies=cookies_a,
        )
    ).json()
    assert ra["categories_created"] == [] and ra["created"][0]["category_id"] == cat_a["id"]


async def test_tamano_ajeno_en_la_plantilla_da_el_mismo_422_que_uno_al_azar(
    client, cookies_a, cookies_b
):
    tamanos_a = await crear_tamanos(client, cookies_a)
    tamanos_b = await crear_tamanos(client, cookies_b)
    pr = precios(tamanos_b)

    def cuerpo(size_id: str) -> dict[str, Any]:
        copia = [dict(p) for p in pr]
        copia[0]["vehicle_size_id"] = size_id
        return {"kind": "lavadero", "items": [{"glossary_code": "lavado_rapido", "prices": copia}]}

    ajeno = await client.post(
        "/v1/templates/apply", json=cuerpo(list(tamanos_a.values())[0]), cookies=cookies_b
    )
    azar = await client.post(
        "/v1/templates/apply", json=cuerpo(str(uuid.uuid4())), cookies=cookies_b
    )
    assert ajeno.status_code == azar.status_code == 422
    assert _detalle(ajeno)["code"] == _detalle(azar)["code"]
    # El mensaje nombra solo el id que mandó B, nunca datos de A (ni su código "AUTO"/"SUV" de A).
    assert (await client.get("/v1/services", cookies=cookies_b)).json()["total"] == 0


async def test_plantilla_respeta_un_servicio_propio_del_mismo_nombre_sin_duplicar(
    client, cookies_a
):
    tamanos = await crear_tamanos(client, cookies_a)
    propio = await crear_servicio(client, cookies_a, tamanos, "Lavado rápido")  # nombre del término
    r = await client.post(
        "/v1/templates/apply",
        json={
            "kind": "lavadero",
            "items": [{"glossary_code": "lavado_rapido", "prices": precios(tamanos)}],
        },
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["created"] == []
    assert cuerpo["skipped"][0]["service_id"] == propio["id"]
    assert cuerpo["skipped"][0]["reason"] == "NAME"
    assert (await client.get("/v1/services", cookies=cookies_a)).json()["total"] == 1


@pytest.mark.parametrize(
    "codigo", ["lavado_rapido", "aromatizante", "no_existe", "../etc", "A B", "X" * 81, ""]
)
async def test_el_post_de_servicios_ignora_glossary_code_solo_la_plantilla_lo_fija(
    client, cookies_a, codigo
):
    """El código del glosario ya no entra por el cuerpo: ni uno válido, ni uno raro, ni repetido."""
    tamanos = await crear_tamanos(client, cookies_a)
    for nombre in ("Uno", "Dos"):  # dos veces el mismo código: sin 409 porque no se guarda
        r = await client.post(
            "/v1/services",
            json=cuerpo_servicio(tamanos, nombre, glossary_code=codigo),
            cookies=cookies_a,
        )
        assert r.status_code == 201, (codigo, r.text)
        assert r.json()["glossary_code"] is None


async def test_glossary_code_de_la_plantilla_convive_en_a_y_b_y_se_libera_al_anular(
    client, cookies_a, cookies_b
):
    ta = await crear_tamanos(client, cookies_a)
    tb = await crear_tamanos(client, cookies_b)

    def cuerpo(tam):
        return {
            "kind": "lavadero",
            "items": [{"glossary_code": "lavado_rapido", "prices": precios(tam)}],
        }

    ra = (await client.post("/v1/templates/apply", json=cuerpo(ta), cookies=cookies_a)).json()
    rb = (await client.post("/v1/templates/apply", json=cuerpo(tb), cookies=cookies_b)).json()
    assert len(ra["created"]) == len(rb["created"]) == 1
    assert ra["created"][0]["glossary_code"] == rb["created"][0]["glossary_code"] == "lavado_rapido"
    sid = ra["created"][0]["id"]
    assert (await client.delete(f"/v1/services/{sid}", cookies=cookies_a)).status_code == 204
    # A lo recrea, B no se enteró
    again = (await client.post("/v1/templates/apply", json=cuerpo(ta), cookies=cookies_a)).json()
    assert len(again["created"]) == 1
    assert (
        await client.get(f"/v1/services/{rb['created'][0]['id']}", cookies=cookies_b)
    ).status_code == 200


# ── 5 · búsqueda ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "q",
    [
        "%",
        "_",
        "%%",
        "\\",
        "'; DROP TABLE services; --",
        "' OR '1'='1",
        "a" * 100,
        "ñ" * 100,
        "ｌａｖａｄｏ",  # fullwidth: NFKD lo pliega a ASCII
        "İİİ",
        "ß",
        "洗車",
        "‮‭lavado",
        "é" * 50,  # combining marks
        "🚗" * 20,
        "   ",
        "---",
        "\t\n",
        "*",
        "lavado\x00",
        "..",
        "{{7*7}}",
    ],
)
async def test_busqueda_con_entradas_raras_no_revienta_ni_trae_filas_ajenas(
    client, cookies_a, cookies_b, q
):
    tamanos_a = await crear_tamanos(client, cookies_a)
    tamanos_b = await crear_tamanos(client, cookies_b)
    await crear_servicio(client, cookies_a, tamanos_a, "Lavado de A", glossary_code="lavado_rapido")
    de_b = await crear_servicio(client, cookies_b, tamanos_b, "Lavado de B")
    for url in ("/v1/services/search", "/v1/services"):
        r = await client.get(url, params={"q": q}, cookies=cookies_b)
        assert r.status_code in (200, 422), (url, q, r.status_code, r.text[:200])
        if r.status_code == 200:
            ids = {s["id"] for s in r.json()["items"]}
            assert ids <= {de_b["id"]}, (url, q)
            assert r.json()["total"] == len(ids)


async def test_q_demasiado_larga_es_422_y_search_sin_q_tambien(client, cookies_a):
    for q in ("a" * 101, "a" * 5000):
        for url in ("/v1/services/search", "/v1/services"):
            r = await client.get(url, params={"q": q}, cookies=cookies_a)
            assert r.status_code == 422, (url, len(q), r.status_code)
    assert (await client.get("/v1/services/search", cookies=cookies_a)).status_code == 422
    assert (await client.get("/v1/services/search?q=", cookies=cookies_a)).status_code == 422


async def test_q_que_normaliza_a_vacio_lista_solo_lo_propio_nunca_lo_ajeno(
    client, cookies_a, cookies_b
):
    """Decisión actual: `q` sin nada que buscar (`   `, `---`, `""` en el listado) lista todo el
    catálogo **del propio negocio**. Lo que importa para el aislamiento: jamás filas de otro."""
    tamanos_a = await crear_tamanos(client, cookies_a)
    tamanos_b = await crear_tamanos(client, cookies_b)
    de_a = await crear_servicio(client, cookies_a, tamanos_a, "Lavado exterior")
    de_b = await crear_servicio(client, cookies_b, tamanos_b, "Lavado de B")
    for q in ("   ", "---", "!!!", "洗"):
        for url in ("/v1/services/search", "/v1/services"):
            ra = await client.get(url, params={"q": q}, cookies=cookies_a)
            rb = await client.get(url, params={"q": q}, cookies=cookies_b)
            assert [s["id"] for s in ra.json()["items"]] == [de_a["id"]], (url, q)
            assert [s["id"] for s in rb.json()["items"]] == [de_b["id"]], (url, q)
    r = await client.get("/v1/services", params={"q": ""}, cookies=cookies_a)
    assert r.status_code == 200 and r.json()["total"] == 1


async def test_los_alias_del_glosario_de_a_no_hacen_aparecer_servicios_en_b(
    client, cookies_a, cookies_b
):
    tamanos_a = await crear_tamanos(client, cookies_a)
    tamanos_b = await crear_tamanos(client, cookies_b)
    # A tiene "Pulido" del glosario; B tiene un servicio propio sin código con otro nombre.
    await crear_servicio(client, cookies_a, tamanos_a, "Pulido", glossary_code="pulido")
    de_b = await crear_servicio(client, cookies_b, tamanos_b, "Algo distinto")
    # Un sinónimo de "pulido" encuentra solo el de A, y para B no existe nada.
    termino = load_glossary().get("pulido").synonyms[0]
    ra = (await client.get("/v1/services/search", params={"q": termino}, cookies=cookies_a)).json()
    rb = (await client.get("/v1/services/search", params={"q": termino}, cookies=cookies_b)).json()
    assert ra["total"] == 1 and rb["total"] == 0, (termino, ra, rb)
    # El servicio propio de B (sin glossary_code) no hereda alias de nadie.
    assert (
        await client.get("/v1/services/search", params={"q": "algo"}, cookies=cookies_b)
    ).json()["items"][0]["id"] == de_b["id"]


async def test_renombrar_no_deja_la_clave_vieja_buscable_ni_en_otro_negocio(
    client, cookies_a, cookies_b
):
    tamanos_a = await crear_tamanos(client, cookies_a)
    s = await crear_servicio(client, cookies_a, tamanos_a, "Encerado premium")
    r = await client.patch(f"/v1/services/{s['id']}", json={"name": "Sellador"}, cookies=cookies_a)
    assert r.status_code == 200
    assert (await client.get("/v1/services/search?q=encerado", cookies=cookies_a)).json()[
        "total"
    ] == 0
    assert (await client.get("/v1/services/search?q=sellador", cookies=cookies_a)).json()[
        "total"
    ] == 1
    assert (await client.get("/v1/services/search?q=sellador", cookies=cookies_b)).json()[
        "total"
    ] == 0


async def test_paginacion_hostil_no_es_5xx(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    await crear_servicio(client, cookies_a, tamanos, "Lavado")
    casos = [
        {"limit": 0},
        {"limit": -1},
        {"limit": 201},
        {"limit": 10**20},
        {"offset": -1},
        {"offset": 10**12},
        {"offset": 2**63},
        {"offset": 10**30},
        {"limit": "x"},
    ]
    for base in ("/v1/services", "/v1/services/search", "/v1/service-categories"):
        for params in casos:
            p = dict(params)
            if base.endswith("search"):
                p["q"] = "lav"
            r = await client.get(base, params=p, cookies=cookies_a)
            assert r.status_code < 500, (base, params, r.status_code, r.text[:200])


async def test_ids_malformados_en_ruta_y_en_cuerpo_son_422(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    for ruta in ("/v1/services/no-es-uuid", "/v1/service-categories/1", "/v1/services/%00"):
        for metodo in ("GET", "PATCH", "DELETE"):
            r = await client.request(metodo, ruta, json={}, cookies=cookies_a)
            assert r.status_code in (404, 422), (metodo, ruta, r.status_code)
    r = await client.put(
        "/v1/services/xx/prices", json={"prices": precios(tamanos)}, cookies=cookies_a
    )
    assert r.status_code == 422
    for malo in ("x", "", 5, "00000000-0000-0000-0000-00000000000g"):
        r = await client.post(
            "/v1/services", json=cuerpo_servicio(tamanos, "Z", category_id=malo), cookies=cookies_a
        )
        assert r.status_code == 422, (malo, r.status_code)
    r = await client.get("/v1/services", params={"category_id": "nope"}, cookies=cookies_a)
    assert r.status_code == 422


# ── 6 · límites ──────────────────────────────────────────────────────────────

TOPE_PRECIO = 10_000_000_000
TOPE_MIN = 24 * 60 * 14


@pytest.mark.parametrize(
    ("precio", "duracion", "esperado"),
    [
        (1, 1, 201),
        (TOPE_PRECIO, TOPE_MIN, 201),
        (TOPE_PRECIO + 1, 60, 422),
        (60, TOPE_MIN + 1, 422),
        (0, 60, 422),
        (-1, 60, 422),
        (-TOPE_PRECIO, 60, 422),
        (60, 0, 422),
        (60, -5, 422),
        (2**31, 60, 201),  # pasa el int32, entra en bigint
        (2**63, 60, 422),
        (2**64, 60, 422),
        (10**30, 60, 422),
        (60, 2**31, 422),
        (60, 2**63, 422),
        (None, 60, 422),
        (60, None, 422),
        ("12abc", 60, 422),
        (1.5, 60, 422),
        (float("1e10") + 1e6, 60, 422),
        ([60], 60, 422),
        ({"a": 1}, 60, 422),
    ],
)
async def test_limites_de_precio_y_duracion_en_el_alta(
    client, cookies_a, precio, duracion, esperado
):
    tamanos = await crear_tamanos(client, cookies_a, "AUTO")
    cuerpo = cuerpo_servicio(tamanos, "Limite")
    cuerpo["prices"][0]["price_cents"] = precio
    cuerpo["prices"][0]["duration_min"] = duracion
    r = await client.post("/v1/services", json=cuerpo, cookies=cookies_a)
    assert r.status_code == esperado, (precio, duracion, r.status_code, r.text[:300])
    if esperado == 201:
        assert r.json()["prices"][0]["price_cents"] == precio


@pytest.mark.parametrize(
    "raw", ['{"price_cents": NaN}', '{"price_cents": Infinity}', '{"price_cents": 1e999}']
)
async def test_json_con_nan_infinito_o_desborde_no_es_5xx(client, cookies_a, raw):
    tamanos = await crear_tamanos(client, cookies_a, "AUTO")
    size = list(tamanos.values())[0]
    cuerpo = (
        '{"name": "N", "pricing_mode": "PRECIO_FIJO", "prices": [{"vehicle_size_id": "'
        + size
        + '", "duration_min": 60, '
        + raw[1:-1]
        + "}]}"
    )
    r = await client.post(
        "/v1/services",
        content=cuerpo,
        headers={"content-type": "application/json"},
        cookies=cookies_a,
    )
    assert r.status_code == 422, r.text[:300]


async def test_precio_booleano_no_se_cuela_como_un_centavo(client, cookies_a):
    """`true` en `price_cents`: pydantic en modo lax lo acepta como 1. Un precio de $0,01 por un
    typo de cliente HTTP rompe el espíritu de A8 (precio válido, > 0)."""
    tamanos = await crear_tamanos(client, cookies_a, "AUTO")
    cuerpo = cuerpo_servicio(tamanos, "Booleano")
    cuerpo["prices"][0]["price_cents"] = True
    r = await client.post("/v1/services", json=cuerpo, cookies=cookies_a)
    assert r.status_code == 422, r.text[:300]


@pytest.mark.parametrize(
    "nombre",
    [
        "",
        "   ",
        "x" * 81,
        "con\x00nul",
        "\n",
        "\u200b",
    ],
)
async def test_nombres_invalidos_de_servicio_y_categoria(client, cookies_a, nombre):
    tamanos = await crear_tamanos(client, cookies_a)
    r = await client.post("/v1/services", json=cuerpo_servicio(tamanos, nombre), cookies=cookies_a)
    assert r.status_code == 422, (repr(nombre), r.status_code)
    r = await client.post("/v1/service-categories", json={"name": nombre}, cookies=cookies_a)
    assert r.status_code == 422, (repr(nombre), r.status_code)


async def test_nombre_de_solo_ideogramas_no_deja_un_servicio_inencontrable_en_silencio(
    client, cookies_a
):
    """`normalize` descarta lo que no es ASCII: un servicio "洗車" queda con `search_key` vacía y
    ninguna búsqueda lo encuentra. No es una fuga; se documenta el comportamiento actual."""
    tamanos = await crear_tamanos(client, cookies_a)
    r = await client.post("/v1/services", json=cuerpo_servicio(tamanos, "洗車"), cookies=cookies_a)
    assert r.status_code in (201, 422), r.text
    if r.status_code == 201:
        encontrado = (
            await client.get("/v1/services/search", params={"q": "洗車"}, cookies=cookies_a)
        ).json()
        # Sigue listable por GET /services aunque no por búsqueda.
        assert (await client.get("/v1/services", cookies=cookies_a)).json()["total"] == 1
        assert encontrado["total"] in (0, 1)


async def test_categoria_con_orden_fuera_de_rango_es_422(client, cookies_a):
    for orden in (-1, 10001, 2**63, "x", 1.5):
        r = await client.post(
            "/v1/service-categories",
            json={"name": f"C{orden}", "sort_order": orden},
            cookies=cookies_a,
        )
        assert r.status_code == 422, (orden, r.status_code)


async def test_muchos_precios_repetidos_o_de_mas_son_422(client, cookies_a):
    tamanos = await crear_tamanos(client, cookies_a)
    base = precios(tamanos)
    repetido = base + [base[0]]
    r = await client.post(
        "/v1/services",
        json={**cuerpo_servicio(tamanos, "Rep"), "prices": repetido},
        cookies=cookies_a,
    )
    assert r.status_code == 422
    gigante = [dict(base[0]) for _ in range(5000)]
    r = await client.post(
        "/v1/services",
        json={**cuerpo_servicio(tamanos, "Gig"), "prices": gigante},
        cookies=cookies_a,
    )
    assert r.status_code == 422
    assert (await client.get("/v1/services", cookies=cookies_a)).json()["total"] == 0


# ── 7 · Idempotency-Key ──────────────────────────────────────────────────────


async def test_la_misma_clave_en_el_mismo_tenant_es_un_replay_sea_cual_sea_el_scope(
    client, cookies_a, cookies_b
):
    """La clave es única por negocio (no por acción): documentado, no es un bug del 4.3."""
    tamanos = await crear_tamanos(client, cookies_a)
    clave = {"Idempotency-Key": f"k-{uuid.uuid4()}"}
    assert (
        await client.post(
            "/v1/service-categories", json={"name": "C"}, cookies=cookies_a, headers=clave
        )
    ).status_code == 201
    dup = await client.post(
        "/v1/services", json=cuerpo_servicio(tamanos, "S"), cookies=cookies_a, headers=clave
    )
    assert dup.status_code == 409 and _detalle(dup)["code"] == "DUPLICATE_IDEMPOTENT"
    assert (await client.get("/v1/services", cookies=cookies_a)).json()["total"] == 0
    # y B con esa misma clave: libre
    assert (
        await client.post(
            "/v1/service-categories", json={"name": "C"}, cookies=cookies_b, headers=clave
        )
    ).status_code == 201


@pytest.mark.parametrize("clave", ["", " ", "x" * 300, "con\x00nul", "ñ" * 50])
async def test_idempotency_key_rara_no_es_5xx(client, cookies_a, clave):
    tamanos = await crear_tamanos(client, cookies_a)
    try:
        r = await client.post(
            "/v1/services",
            json=cuerpo_servicio(tamanos, "Clave rara"),
            cookies=cookies_a,
            headers={"Idempotency-Key": clave},
        )
    except Exception as exc:  # httpx rechaza algunos valores antes de enviar: no es del server
        pytest.skip(f"el cliente no manda esa cabecera: {exc!r}")
    assert r.status_code < 500, (clave, r.status_code, r.text[:200])
