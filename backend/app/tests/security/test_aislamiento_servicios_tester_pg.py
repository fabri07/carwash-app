"""Tester-aislamiento del PR 4.3 contra **Postgres real** (rol `carwash_app`, RLS activo).

Escrito por quien NO implementó el 4.3. Lo que los tests de SQLite no pueden probar:

1. **RLS de `service_categories`** y de las columnas nuevas de `services`: sin contexto no hay
   filas, B no ve/edita/borra/inserta en nombre de A, ni mueve una categoría de tenant.
2. **FK compuesta** `(tenant_id, category_id)`: B no cuelga un servicio de una categoría de A ni
   por SQL crudo.
3. **Únicos parciales** (`glossary_code`, `name`): el mismo valor convive en A y B; dentro de un
   negocio solo choca entre vivos; los `NULL` no chocan.
4. **Servicios de aplicación** (`ServiceCatalogService`, `TemplateService`) con ids de A desde B:
   `NotFoundError` indistinguible de un id inexistente y ninguna fila de A cambia. Estas
   operaciones no están en `_intentos_servicios.INTENTOS` (solo trae las de `CatalogService`).
5. **HTTP con rollback real**: un 422/404 no "quema" la `Idempotency-Key` (en la suite rápida la
   sesión es compartida y no hay rollback, así que ahí no se puede probar).
6. **HTTP**: 404 de A visto por B idéntico al inexistente, búsqueda y plantillas aisladas.

Datos sintéticos.
"""

import re
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.services.errors import NotFoundError
from app.application.services.service_catalog_service import PriceInput, ServiceCatalogService
from app.application.services.template_service import TemplateItemInput, TemplateService
from app.domain.enums import PricingMode
from app.domain.glossary import TemplateKind
from app.persistence.db.tenant_context import set_tenant_context
from app.tests.conftest import cookie_header
from app.utils.cookies import ACCESS_COOKIE
from app.utils.security import create_access_token

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]


def _sesion(user: uuid.UUID, tenant: uuid.UUID) -> dict[str, str]:
    token = create_access_token({"sub": str(user), "tenant_id": str(tenant), "ver": 0})
    return cookie_header({ACCESS_COOKIE: token})


@pytest.fixture
async def dos(pg_admin_engine, pg_tenant_a, pg_tenant_b, pg_user_factory) -> dict[str, Any]:
    owner_a = await pg_user_factory(pg_tenant_a, "owner-a@ejemplo.invalid")
    owner_b = await pg_user_factory(pg_tenant_b, "owner-b@ejemplo.invalid")
    return {
        "a": pg_tenant_a,
        "b": pg_tenant_b,
        "ha": _sesion(owner_a, pg_tenant_a),
        "hb": _sesion(owner_b, pg_tenant_b),
        "owner_a": owner_a,
        "owner_b": owner_b,
    }


async def _tamanos(client, headers) -> dict[str, str]:
    ids = {}
    for codigo in ("AUTO", "SUV"):
        r = await client.post(
            "/v1/vehicle-sizes", json={"code": codigo, "label": codigo}, headers=headers
        )
        assert r.status_code == 201, r.text
        ids[codigo] = r.json()["id"]
    return ids


def _precios(tamanos: dict[str, str], base: int = 2_000_000) -> list[dict[str, Any]]:
    return [
        {"vehicle_size_id": s, "price_cents": base + i * 1000, "duration_min": 60 + i}
        for i, s in enumerate(tamanos.values())
    ]


def _cuerpo(tamanos, nombre: str, **extra: Any) -> dict[str, Any]:
    return {"name": nombre, "pricing_mode": "PRECIO_FIJO", "prices": _precios(tamanos), **extra}


# ── 1 y 2 · RLS y FK compuesta con SQL directo ───────────────────────────────


async def _insertar_categoria(admin: AsyncEngine, tenant: uuid.UUID, nombre: str) -> uuid.UUID:
    cid = uuid.uuid4()
    async with admin.begin() as conn:
        await conn.execute(
            text("INSERT INTO service_categories (id, tenant_id, name) VALUES (:i, :t, :n)"),
            {"i": cid, "t": tenant, "n": nombre},
        )
    return cid


async def test_rls_de_service_categories_sin_contexto_y_con_contexto(
    pg_session_factory, pg_admin_engine, dos
):
    ca = await _insertar_categoria(pg_admin_engine, dos["a"], "Lavado")
    cb = await _insertar_categoria(pg_admin_engine, dos["b"], "Lavado")  # mismo nombre: conviven
    async with pg_session_factory() as s, s.begin():
        assert (await s.scalar(text("SELECT count(*) FROM service_categories"))) == 0
    async with pg_session_factory() as s, s.begin():
        await set_tenant_context(s, dos["b"])
        visibles = {r[0] for r in await s.execute(text("SELECT id FROM service_categories"))}
        assert visibles == {cb}
        # B no ve, no actualiza y no borra la fila de A (RLS: 0 filas afectadas).
        upd = await s.execute(
            text("UPDATE service_categories SET name = 'pisada' WHERE id = :i"), {"i": ca}
        )
        assert upd.rowcount == 0
    # El rol de runtime ni siquiera tiene DELETE: todo es soft-delete (ADR de borrado).
    with pytest.raises(DBAPIError):
        async with pg_session_factory() as s, s.begin():
            await set_tenant_context(s, dos["b"])
            await s.execute(text("DELETE FROM service_categories WHERE id = :i"), {"i": ca})
    async with pg_admin_engine.connect() as conn:
        nombre = await conn.scalar(
            text("SELECT name FROM service_categories WHERE id = :i"), {"i": ca}
        )
    assert nombre == "Lavado"


async def test_b_no_inserta_ni_muda_una_categoria_a_nombre_de_a(
    pg_session_factory, pg_admin_engine, dos
):
    cb = await _insertar_categoria(pg_admin_engine, dos["b"], "De B")
    with pytest.raises(DBAPIError):  # WITH CHECK: tenant_id de la fila != contexto
        async with pg_session_factory() as s, s.begin():
            await set_tenant_context(s, dos["b"])
            await s.execute(
                text("INSERT INTO service_categories (id, tenant_id, name) VALUES (:i, :t, 'x')"),
                {"i": uuid.uuid4(), "t": dos["a"]},
            )
    with pytest.raises(DBAPIError):  # mudar la propia a A viola WITH CHECK
        async with pg_session_factory() as s, s.begin():
            await set_tenant_context(s, dos["b"])
            await s.execute(
                text("UPDATE service_categories SET tenant_id = :a WHERE id = :i"),
                {"a": dos["a"], "i": cb},
            )


async def test_b_no_cuelga_un_servicio_de_la_categoria_de_a_ni_por_sql_crudo(
    pg_session_factory, pg_admin_engine, dos
):
    ca = await _insertar_categoria(pg_admin_engine, dos["a"], "De A")
    with pytest.raises(IntegrityError):  # FK compuesta (tenant_id, category_id)
        async with pg_session_factory() as s, s.begin():
            await set_tenant_context(s, dos["b"])
            await s.execute(
                text(
                    "INSERT INTO services (id, tenant_id, name, search_key, pricing_mode,"
                    " category_id)"
                    " VALUES (:i, :t, 'Mio', 'mio', 'PRECIO_FIJO', :c)"
                ),
                {"i": uuid.uuid4(), "t": dos["b"], "c": ca},
            )


async def test_unicos_parciales_conviven_entre_negocios_y_solo_chocan_entre_vivos(
    pg_session_factory, pg_admin_engine, dos
):
    async def servicio(tenant, nombre, codigo):
        async with pg_admin_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO services (id, tenant_id, name, search_key, pricing_mode,"
                    " glossary_code) VALUES (:i, :t, :n, :n, 'PRECIO_FIJO', :g)"
                ),
                {"i": uuid.uuid4(), "t": tenant, "n": nombre, "g": codigo},
            )

    await servicio(dos["a"], "Uno", "pulido")
    await servicio(dos["b"], "Uno", "pulido")  # mismo nombre y código en otro negocio: ok
    await servicio(dos["a"], "Dos", None)  # NULL no choca con NULL
    await servicio(dos["a"], "Tres", None)
    with pytest.raises(IntegrityError):
        await servicio(dos["a"], "Cuatro", "pulido")  # mismo código entre vivos de A
    with pytest.raises(IntegrityError):
        await servicio(dos["a"], "Uno", "otro")  # mismo nombre entre vivos de A


async def test_la_columna_search_key_es_not_null_y_la_base_no_la_calcula(pg_admin_engine, dos):
    with pytest.raises(IntegrityError):
        async with pg_admin_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO services (id, tenant_id, name, pricing_mode)"
                    " VALUES (:i, :t, 'Sin clave', 'PRECIO_FIJO')"
                ),
                {"i": uuid.uuid4(), "t": dos["a"]},
            )


# ── 4 · servicios de aplicación con ids ajenos ───────────────────────────────


async def _foto(admin: AsyncEngine, tenant: uuid.UUID) -> dict[str, list[str]]:
    tablas = ("service_categories", "services", "service_prices", "vehicle_sizes")
    out: dict[str, list[str]] = {}
    async with admin.connect() as conn:
        for t in tablas:
            rows = await conn.execute(
                text(f"SELECT row_to_json(x)::text FROM {t} x WHERE tenant_id = :t ORDER BY id"),
                {"t": tenant},
            )
            out[t] = [r[0] for r in rows]
    return out


def _normal(exc: NotFoundError, ajeno: uuid.UUID) -> tuple[type, str]:
    return type(exc), re.sub(re.escape(str(ajeno)), "<id>", str(exc))


async def test_los_servicios_de_b_con_ids_de_a_dan_not_found_indistinguible_y_no_tocan_a(
    pg_session_factory, pg_admin_engine, pg_client, dos
):
    ta = await _tamanos(pg_client, dos["ha"])
    cat_a = (
        await pg_client.post("/v1/service-categories", json={"name": "De A"}, headers=dos["ha"])
    ).json()["id"]
    srv_a = (
        await pg_client.post(
            "/v1/services", json=_cuerpo(ta, "Servicio de A", category_id=cat_a), headers=dos["ha"]
        )
    ).json()["id"]
    tb = await _tamanos(pg_client, dos["hb"])
    prices_b = [PriceInput(uuid.UUID(s), 1_000_000, 60) for s in tb.values()]
    antes = await _foto(pg_admin_engine, dos["a"])

    async def intento(nombre: str, llamada) -> None:
        for id_ajeno, id_nada in (
            (uuid.UUID(srv_a), uuid.uuid4()),
            (uuid.UUID(cat_a), uuid.uuid4()),
        ):
            errores = []
            for objetivo in (id_ajeno, id_nada):
                async with pg_session_factory() as s, s.begin():
                    await set_tenant_context(s, dos["b"])
                    svc = ServiceCatalogService(s, dos["b"], dos["owner_b"])
                    try:
                        await llamada(svc, objetivo)
                    except NotFoundError as exc:  # se traga y se COMITEA: el peor llamador
                        errores.append(exc)
                    else:  # pragma: no cover  # solo corre si hay una fuga: la rama sana no llega
                        pytest.fail(f"{nombre}: un id ajeno no dio NotFoundError")
            # Solo cuenta si el id es del tipo que la operación espera: el otro es también ajeno
            # (no existe para B), y debe dar lo mismo.
            assert _normal(errores[0], id_ajeno) == _normal(errores[1], id_nada), nombre

    ops = {
        "get_service": lambda svc, i: svc.get_service(i),
        "update_service": lambda svc, i: svc.update_service(i, name="Pisado"),
        "replace_prices": lambda svc, i: svc.replace_prices(i, prices_b),
        "void_service": lambda svc, i: svc.void_service(i),
        "get_category": lambda svc, i: svc.get_category(i),
        "update_category": lambda svc, i: svc.update_category(i, name="Pisada"),
        "void_category": lambda svc, i: svc.void_category(i),
    }
    for nombre, llamada in ops.items():
        await intento(nombre, llamada)
    assert await _foto(pg_admin_engine, dos["a"]) == antes


async def test_b_no_crea_con_la_categoria_de_a_desde_el_servicio_de_aplicacion(
    pg_session_factory, pg_admin_engine, pg_client, dos
):
    ta = await _tamanos(pg_client, dos["ha"])
    cat_a = (
        await pg_client.post("/v1/service-categories", json={"name": "De A"}, headers=dos["ha"])
    ).json()["id"]
    assert ta
    tb = await _tamanos(pg_client, dos["hb"])
    antes_b = await _foto(pg_admin_engine, dos["b"])
    with pytest.raises(NotFoundError):
        async with pg_session_factory() as s, s.begin():
            await set_tenant_context(s, dos["b"])
            await ServiceCatalogService(s, dos["b"], dos["owner_b"]).create_priced_service(
                "Mio",
                PricingMode.PRECIO_FIJO,
                [PriceInput(uuid.UUID(x), 1_000_000, 60) for x in tb.values()],
                category_id=uuid.UUID(cat_a),
            )
    assert await _foto(pg_admin_engine, dos["b"]) == antes_b


async def test_la_plantilla_de_a_y_de_b_no_se_ven_en_postgres(
    pg_session_factory, pg_admin_engine, pg_client, dos
):
    ta = await _tamanos(pg_client, dos["ha"])
    tb = await _tamanos(pg_client, dos["hb"])
    item_a = TemplateItemInput(
        "lavado_rapido",
        PricingMode.PRECIO_FIJO,
        [PriceInput(uuid.UUID(s), 1_000_000, 60) for s in ta.values()],
    )
    item_b = TemplateItemInput(
        "lavado_rapido",
        PricingMode.PRECIO_FIJO,
        [PriceInput(uuid.UUID(s), 2_000_000, 90) for s in tb.values()],
    )
    async with pg_session_factory() as s, s.begin():
        await set_tenant_context(s, dos["a"])
        ra = await TemplateService(s, dos["a"], dos["owner_a"]).apply(
            TemplateKind.LAVADERO, [item_a]
        )
        id_a = ra.created[0].service.id
    async with pg_session_factory() as s, s.begin():
        await set_tenant_context(s, dos["b"])
        svc = TemplateService(s, dos["b"], dos["owner_b"])
        assert not any(e.already_added for e in await svc.get_template(TemplateKind.LAVADERO))
        rb = await svc.apply(TemplateKind.LAVADERO, [item_b])
        assert len(rb.created) == 1 and rb.skipped == []
        id_b = rb.created[0].service.id
    async with pg_session_factory() as s, s.begin():
        await set_tenant_context(s, dos["a"])
        again = await TemplateService(s, dos["a"], dos["owner_a"]).apply(
            TemplateKind.LAVADERO,
            [item_b],  # con precios de B: ignorados por ya existir
        )
        assert [x.service.id for x in again.skipped] == [id_a] and id_a != id_b


# ── 5 · idempotency con rollback real ────────────────────────────────────────


async def test_un_422_o_un_404_no_queman_la_idempotency_key(pg_client, pg_admin_engine, dos):
    ta = await _tamanos(pg_client, dos["ha"])
    tb = await _tamanos(pg_client, dos["hb"])
    cat_a = (
        await pg_client.post("/v1/service-categories", json={"name": "De A"}, headers=dos["ha"])
    ).json()["id"]
    # 422: falta un tamaño
    k1 = {"Idempotency-Key": f"k-{uuid.uuid4()}", **dos["hb"]}
    malo = _cuerpo(tb, "Con clave")
    malo["prices"] = malo["prices"][:1]
    assert (await pg_client.post("/v1/services", json=malo, headers=k1)).status_code == 422
    ok = await pg_client.post("/v1/services", json=_cuerpo(tb, "Con clave"), headers=k1)
    assert ok.status_code == 201, ok.text
    # 404: categoría de A usada por B
    k2 = {"Idempotency-Key": f"k-{uuid.uuid4()}", **dos["hb"]}
    r = await pg_client.post(
        "/v1/services", json=_cuerpo(tb, "Otra", category_id=cat_a), headers=k2
    )
    assert r.status_code == 404
    ok2 = await pg_client.post("/v1/services", json=_cuerpo(tb, "Otra"), headers=k2)
    assert ok2.status_code == 201, ok2.text
    # la clave de A no depende de la de B ni al revés
    k3 = {"Idempotency-Key": "compartida", **dos["ha"]}
    k4 = {"Idempotency-Key": "compartida", **dos["hb"]}
    assert (
        await pg_client.post("/v1/service-categories", json={"name": "K"}, headers=k3)
    ).status_code == 201
    assert (
        await pg_client.post("/v1/service-categories", json={"name": "K"}, headers=k4)
    ).status_code == 201
    assert ta


# ── 6 · HTTP ─────────────────────────────────────────────────────────────────


async def test_http_404_ajeno_identico_al_inexistente_y_listados_aislados(pg_client, dos):
    ta = await _tamanos(pg_client, dos["ha"])
    tb = await _tamanos(pg_client, dos["hb"])
    cat = (
        await pg_client.post("/v1/service-categories", json={"name": "Lavado"}, headers=dos["ha"])
    ).json()
    srv = (
        await pg_client.post(
            "/v1/services",
            json=_cuerpo(ta, "Lavado de A", category_id=cat["id"]),
            headers=dos["ha"],
        )
    ).json()
    fantasma = uuid.uuid4()
    pedidos = [
        ("GET", "/v1/services/{}", None),
        ("PATCH", "/v1/services/{}", {"name": "Pisado"}),
        ("DELETE", "/v1/services/{}", None),
        ("PUT", "/v1/services/{}/prices", {"prices": _precios(tb)}),
        ("GET", "/v1/service-categories/{}", None),
        ("PATCH", "/v1/service-categories/{}", {"name": "Pisada"}),
        ("DELETE", "/v1/service-categories/{}", None),
    ]
    for metodo, ruta, cuerpo in pedidos:
        ajeno_id = cat["id"] if "categories" in ruta else srv["id"]
        r1 = await pg_client.request(metodo, ruta.format(ajeno_id), json=cuerpo, headers=dos["hb"])
        r2 = await pg_client.request(metodo, ruta.format(fantasma), json=cuerpo, headers=dos["hb"])
        assert r1.status_code == r2.status_code == 404, (metodo, ruta, r1.text)
        assert r1.content.replace(ajeno_id.encode(), b"X") == r2.content.replace(
            str(fantasma).encode(), b"X"
        )
    # B nada ve de A por lista, categoría ni búsqueda (incluye un `q` con alias del glosario)
    for url in (
        "/v1/services",
        "/v1/services/search?q=lavado",
        "/v1/services?q=lavado",
        f"/v1/services?category_id={cat['id']}",
        "/v1/service-categories",
    ):
        assert (await pg_client.get(url, headers=dos["hb"])).json()["total"] == 0, url
    # A sigue intacta
    assert (await pg_client.get(f"/v1/services/{srv['id']}", headers=dos["ha"])).json()[
        "name"
    ] == "Lavado de A"
    # B no cuelga de la categoría de A ni da precio a un tamaño de A
    r = await pg_client.post(
        "/v1/services", json=_cuerpo(tb, "Mio", category_id=cat["id"]), headers=dos["hb"]
    )
    assert r.status_code == 404
    cuerpo = _cuerpo(tb, "Mio")
    cuerpo["prices"][0]["vehicle_size_id"] = list(ta.values())[0]
    assert (await pg_client.post("/v1/services", json=cuerpo, headers=dos["hb"])).status_code == 422


async def test_http_la_plantilla_de_a_no_afecta_a_b_ni_a_su_idempotencia(pg_client, dos):
    ta = await _tamanos(pg_client, dos["ha"])
    tb = await _tamanos(pg_client, dos["hb"])

    def aplicar(tam, base):
        return {
            "kind": "lavadero",
            "items": [
                {"glossary_code": c, "prices": _precios(tam, base)}
                for c in ("lavado_rapido", "lavado_exterior")
            ],
        }

    ra = await pg_client.post("/v1/templates/apply", json=aplicar(ta, 1_000_000), headers=dos["ha"])
    assert ra.status_code == 200 and len(ra.json()["created"]) == 2, ra.text
    sug_b = (await pg_client.get("/v1/templates/lavadero", headers=dos["hb"])).json()
    assert not any(i["already_added"] for c in sug_b["categories"] for i in c["items"])
    rb = await pg_client.post("/v1/templates/apply", json=aplicar(tb, 2_000_000), headers=dos["hb"])
    assert len(rb.json()["created"]) == 2 and rb.json()["skipped"] == []
    ids_a = {s["id"] for s in ra.json()["created"]}
    ids_b = {s["id"] for s in rb.json()["created"]}
    assert not ids_a & ids_b
    # reaplicar en A: todo skipped y solo ids de A
    r2 = await pg_client.post("/v1/templates/apply", json=aplicar(ta, 1_000_000), headers=dos["ha"])
    assert r2.json()["created"] == [] and {s["service_id"] for s in r2.json()["skipped"]} == ids_a
    # precios de cada uno intactos
    for s in ra.json()["created"]:
        assert all(p["price_cents"] < 2_000_000 for p in s["prices"])
    # y la búsqueda ve solo lo propio
    ba = (await pg_client.get("/v1/services/search?q=lavado", headers=dos["ha"])).json()
    bb = (await pg_client.get("/v1/services/search?q=lavado", headers=dos["hb"])).json()
    assert {s["id"] for s in ba["items"]} == ids_a and {s["id"] for s in bb["items"]} == ids_b
