"""CRUD del recurso dummy: paginación, idempotencia, roles y anulación.

El test cruzado entre tenants NO está acá: es de T3 (Tester-aislamiento).
"""

import uuid

from sqlalchemy import select

from app.domain.void import VoidReason
from app.persistence.models import DummyResource


async def _crear(client, cookies, n: int) -> list[str]:
    ids = []
    for i in range(n):
        r = await client.post("/v1/dummy-resources", json={"name": f"d{i}"}, cookies=cookies)
        assert r.status_code == 201
        ids.append(r.json()["id"])
    return ids


async def test_crear_y_leer(client, cookies_a, tenant_a):
    r = await client.post("/v1/dummy-resources", json={"name": "uno"}, cookies=cookies_a)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "uno" and body["tenant_id"] == str(tenant_a.id)
    got = await client.get(f"/v1/dummy-resources/{body['id']}", cookies=cookies_a)
    assert got.status_code == 200 and got.json()["id"] == body["id"]


async def test_tenant_id_del_body_se_ignora(client, cookies_a, tenant_a, tenant_b):
    r = await client.post(
        "/v1/dummy-resources",
        json={"name": "x", "tenant_id": str(tenant_b.id)},
        cookies=cookies_a,
    )
    assert r.status_code == 201
    assert r.json()["tenant_id"] == str(tenant_a.id)


async def test_la_segunda_pagina_es_alcanzable(client, cookies_a):
    await _crear(client, cookies_a, 60)
    p1 = (await client.get("/v1/dummy-resources?limit=50&offset=0", cookies=cookies_a)).json()
    assert p1["total"] == 60 and p1["has_more"] is True and len(p1["items"]) == 50
    p2 = (await client.get("/v1/dummy-resources?limit=50&offset=50", cookies=cookies_a)).json()
    assert p2["has_more"] is False and len(p2["items"]) == 10
    assert not ({i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]})


async def test_limit_por_encima_del_maximo_es_422(client, cookies_a):
    r = await client.get("/v1/dummy-resources?limit=100000", cookies=cookies_a)
    assert r.status_code == 422
    assert (await client.get("/v1/dummy-resources?offset=-1", cookies=cookies_a)).status_code == 422


async def test_idempotency_key_repetida_es_409_duplicate_idempotent(client, cookies_a):
    headers = {"Idempotency-Key": "k-123"}
    first = await client.post(
        "/v1/dummy-resources", json={"name": "a"}, headers=headers, cookies=cookies_a
    )
    assert first.status_code == 201
    replay = await client.post(
        "/v1/dummy-resources", json={"name": "a"}, headers=headers, cookies=cookies_a
    )
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "DUPLICATE_IDEMPOTENT"
    listado = (await client.get("/v1/dummy-resources", cookies=cookies_a)).json()
    assert listado["total"] == 1


async def test_la_misma_key_en_otro_tenant_no_choca(client, cookies_a, cookies_b):
    headers = {"Idempotency-Key": "compartida"}
    a = await client.post(
        "/v1/dummy-resources", json={"name": "a"}, headers=headers, cookies=cookies_a
    )
    b = await client.post(
        "/v1/dummy-resources", json={"name": "b"}, headers=headers, cookies=cookies_b
    )
    assert a.status_code == b.status_code == 201


async def test_patch_actualiza(client, cookies_a, dummy_a):
    r = await client.patch(
        f"/v1/dummy-resources/{dummy_a.id}", json={"name": "nuevo"}, cookies=cookies_a
    )
    assert r.status_code == 200 and r.json()["name"] == "nuevo"


async def test_inexistente_es_404_en_get_patch_delete(client, cookies_a):
    missing = uuid.uuid4()
    get = await client.get(f"/v1/dummy-resources/{missing}", cookies=cookies_a)
    patch = await client.patch(
        f"/v1/dummy-resources/{missing}", json={"name": "x"}, cookies=cookies_a
    )
    delete = await client.delete(f"/v1/dummy-resources/{missing}", cookies=cookies_a)
    assert get.status_code == patch.status_code == delete.status_code == 404
    assert get.json()["detail"]["code"] == "NOT_FOUND"


async def test_delete_anula_y_desaparece_del_listado(client, cookies_a, dummy_a, db_session):
    r = await client.delete(f"/v1/dummy-resources/{dummy_a.id}", cookies=cookies_a)
    assert r.status_code == 204
    assert (
        await client.get(f"/v1/dummy-resources/{dummy_a.id}", cookies=cookies_a)
    ).status_code == 404
    fila = await db_session.scalar(select(DummyResource).where(DummyResource.id == dummy_a.id))
    assert fila is not None, "anular no es borrar"
    assert fila.void_reason == VoidReason.PEDIDO_DEL_USUARIO and fila.voided_at is not None


async def test_staff_no_entra_donde_pide_owner(client, staff_cookies, dummy_a):
    r = await client.delete(f"/v1/dummy-resources/{dummy_a.id}", cookies=staff_cookies)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "FORBIDDEN"


async def test_staff_puede_leer(client, staff_cookies, dummy_a):
    r = await client.get(f"/v1/dummy-resources/{dummy_a.id}", cookies=staff_cookies)
    assert r.status_code == 200


async def test_fixtures_de_vektor_con_header_cookie(client, auth_headers, second_auth_headers):
    assert (await client.get("/v1/auth/me", headers=auth_headers)).status_code == 200
    assert (await client.get("/v1/auth/me", headers=second_auth_headers)).status_code == 200
