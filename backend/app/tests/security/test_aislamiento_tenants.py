"""A8 · Aislamiento cruzado entre dos tenants — 404, nunca 403 (ADR-0001, ADR-0002).

Escrito por el Tester-aislamiento de T3, que no implementó el backend. El objetivo
es que un tenant vea o toque datos de otro; cada test es un intento fallido de eso.

Dos capas, mismo archivo:

- **API sobre SQLite** (sin marca): corre en la suite rápida. Sin RLS, prueba el
  filtro del repositorio y el contrato HTTP.
- **API sobre Postgres real** (marca `postgres`): rol `carwash_app`, RLS activo, el
  mismo `get_db_session` de producción. Es lo que pide A8:
  `uv run pytest app/tests/security/test_aislamiento_tenants.py -v -m postgres -n 0`.

La red de abajo (SQL crudo contra las políticas, pool, ventana de login) vive en
`test_aislamiento_tenants_pg.py`.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt
from sqlalchemy import select, text

from app.config.settings import get_settings
from app.domain.roles import Role
from app.persistence.models import DummyResource
from app.tests.conftest import make_user, session_cookies
from app.utils.cookies import ACCESS_COOKIE
from app.utils.security import create_access_token, create_refresh_token

URL = "/v1/dummy-resources"


def _mismo_cuerpo(ajeno, inexistente) -> None:
    """Lo ajeno y lo inexistente tienen que ser indistinguibles byte a byte."""
    assert ajeno.status_code == inexistente.status_code
    assert ajeno.content == inexistente.content
    assert ajeno.headers.get("content-type") == inexistente.headers.get("content-type")


# ══ API sobre SQLite ══════════════════════════════════════════════════════════


async def test_get_ajeno_es_404_identico_al_inexistente(client, cookies_b, id_de_a):
    ajeno = await client.get(f"{URL}/{id_de_a}", cookies=cookies_b)
    inexistente = await client.get(f"{URL}/{uuid.uuid4()}", cookies=cookies_b)
    assert ajeno.status_code == 404
    assert ajeno.json()["detail"]["code"] == "NOT_FOUND"
    _mismo_cuerpo(ajeno, inexistente)


async def test_patch_ajeno_es_404_identico_y_no_cambia_nada(client, cookies_a, cookies_b, id_de_a):
    ajeno = await client.patch(f"{URL}/{id_de_a}", json={"name": "pisado"}, cookies=cookies_b)
    inexistente = await client.patch(
        f"{URL}/{uuid.uuid4()}", json={"name": "pisado"}, cookies=cookies_b
    )
    assert ajeno.status_code == 404
    _mismo_cuerpo(ajeno, inexistente)

    como_a = await client.get(f"{URL}/{id_de_a}", cookies=cookies_a)
    assert como_a.status_code == 200
    assert como_a.json()["name"] == "dummy de A"


async def test_delete_ajeno_es_404_identico_y_no_anula(
    client, cookies_a, cookies_b, id_de_a, db_session
):
    ajeno = await client.delete(f"{URL}/{id_de_a}", cookies=cookies_b)
    inexistente = await client.delete(f"{URL}/{uuid.uuid4()}", cookies=cookies_b)
    assert ajeno.status_code == 404
    _mismo_cuerpo(ajeno, inexistente)

    assert (await client.get(f"{URL}/{id_de_a}", cookies=cookies_a)).status_code == 200
    fila = await db_session.scalar(select(DummyResource).where(DummyResource.id == id_de_a))
    assert fila is not None and fila.voided_at is None and fila.void_reason is None


async def test_staff_de_b_no_distingue_ajeno_de_inexistente_en_delete(
    client, db_session, tenant_b, id_de_a
):
    # DELETE exige OWNER: el 403 de rol sale antes de mirar el id. Lo que importa es
    # que salga igual para el id ajeno que para uno que no existe.
    staff_b = await make_user(db_session, tenant_b, Role.STAFF, "staff-b@example.com")
    cookies = session_cookies(staff_b)
    ajeno = await client.delete(f"{URL}/{id_de_a}", cookies=cookies)
    inexistente = await client.delete(f"{URL}/{uuid.uuid4()}", cookies=cookies)
    assert ajeno.status_code == 403
    _mismo_cuerpo(ajeno, inexistente)


async def test_listado_de_b_no_contiene_ni_cuenta_lo_de_a(client, cookies_a, cookies_b, id_de_a):
    for i in range(3):
        r = await client.post(URL, json={"name": f"a{i}"}, cookies=cookies_a)
        assert r.status_code == 201
    propio = await client.post(URL, json={"name": "de B"}, cookies=cookies_b)
    assert propio.status_code == 201

    listado_b = await client.get(URL, cookies=cookies_b)
    assert listado_b.status_code == 200
    body = listado_b.json()
    assert [i["id"] for i in body["items"]] == [propio.json()["id"]]
    assert body["total"] == 1
    assert body["has_more"] is False
    assert str(id_de_a) not in listado_b.text

    # paginar más allá no descubre filas ajenas
    lejos = (await client.get(f"{URL}?limit=50&offset=1", cookies=cookies_b)).json()
    assert lejos["items"] == [] and lejos["total"] == 1

    listado_a = (await client.get(URL, cookies=cookies_a)).json()
    assert listado_a["total"] == 4
    assert propio.json()["id"] not in {i["id"] for i in listado_a["items"]}


async def test_listado_vacio_de_b_es_identico_con_o_sin_datos_en_a(client, cookies_a, cookies_b):
    antes = await client.get(URL, cookies=cookies_b)
    r = await client.post(URL, json={"name": "de A"}, cookies=cookies_a)
    assert r.status_code == 201
    despues = await client.get(URL, cookies=cookies_b)
    assert antes.content == despues.content


@pytest.mark.parametrize(
    "extra",
    [
        {"tenant_id": "A"},
        {"tenantId": "A"},
        {"tenant_id": "A", "id": "A_DUMMY"},
    ],
    ids=["tenant_id", "tenantId-camel", "tenant_id-e-id-ajeno"],
)
async def test_crear_en_b_con_tenant_de_a_en_el_body_queda_en_b(
    client, cookies_a, cookies_b, tenant_a, tenant_b, id_de_a, extra
):
    body = {"name": "inyectado"}
    for k, v in extra.items():
        body[k] = str(tenant_a.id) if v == "A" else str(id_de_a)
    r = await client.post(URL, json=body, cookies=cookies_b)
    assert r.status_code in (201, 422)
    if r.status_code == 201:
        creado = r.json()
        assert creado["tenant_id"] == str(tenant_b.id)
        assert creado["id"] != str(id_de_a)
        assert (await client.get(f"{URL}/{creado['id']}", cookies=cookies_a)).status_code == 404
    listado_a = (await client.get(URL, cookies=cookies_a)).json()
    assert listado_a["total"] == 1  # solo el dummy original de A
    assert (await client.get(f"{URL}/{id_de_a}", cookies=cookies_a)).json()["name"] == "dummy de A"


async def test_patch_propio_no_puede_mudar_el_recurso_al_otro_tenant(
    client, cookies_a, cookies_b, tenant_a, tenant_b
):
    creado = (await client.post(URL, json={"name": "de B"}, cookies=cookies_b)).json()
    r = await client.patch(
        f"{URL}/{creado['id']}",
        json={"name": "mudado", "tenant_id": str(tenant_a.id)},
        cookies=cookies_b,
    )
    assert r.status_code in (200, 422)
    assert (await client.get(f"{URL}/{creado['id']}", cookies=cookies_a)).status_code == 404
    assert (await client.get(URL, cookies=cookies_a)).json()["total"] == 0
    assert (await client.get(f"{URL}/{creado['id']}", cookies=cookies_b)).json()[
        "tenant_id"
    ] == str(tenant_b.id)


async def test_idempotency_key_repetida_en_mismo_tenant_409_y_en_otro_201(
    client, cookies_a, cookies_b
):
    headers = {"Idempotency-Key": "clave-cruzada"}
    a1 = await client.post(URL, json={"name": "a"}, headers=headers, cookies=cookies_a)
    a2 = await client.post(URL, json={"name": "a"}, headers=headers, cookies=cookies_a)
    b1 = await client.post(URL, json={"name": "b"}, headers=headers, cookies=cookies_b)
    b2 = await client.post(URL, json={"name": "b"}, headers=headers, cookies=cookies_b)
    assert a1.status_code == 201
    assert a2.status_code == 409 and a2.json()["detail"]["code"] == "DUPLICATE_IDEMPOTENT"
    assert b1.status_code == 201
    assert b2.status_code == 409 and b2.json()["detail"]["code"] == "DUPLICATE_IDEMPOTENT"
    # el 409 de B no filtra nada de A: mismo cuerpo que el replay de A
    assert a2.content == b2.content
    assert (await client.get(URL, cookies=cookies_a)).json()["total"] == 1
    assert (await client.get(URL, cookies=cookies_b)).json()["total"] == 1


async def test_key_usada_por_a_no_revela_nada_a_b(client, cookies_a, cookies_b):
    # el primer uso en B de una key que A ya usó se comporta igual que una key virgen
    headers = {"Idempotency-Key": "solo-de-a"}
    assert (
        await client.post(URL, json={"name": "a"}, headers=headers, cookies=cookies_a)
    ).status_code == 201
    usada = await client.post(URL, json={"name": "b"}, headers=headers, cookies=cookies_b)
    virgen = await client.post(
        URL, json={"name": "b"}, headers={"Idempotency-Key": "nunca-usada"}, cookies=cookies_b
    )
    assert usada.status_code == virgen.status_code == 201


# ── Tokens ────────────────────────────────────────────────────────────────────


def _claims(user) -> dict:
    return {"sub": str(user.id), "tenant_id": str(user.tenant_id), "ver": user.token_version}


def _firmar(payload: dict, key: str, alg: str = "HS256") -> str:
    exp = datetime.now(UTC) + timedelta(minutes=5)
    return str(jwt.encode({**payload, "exp": exp}, key, algorithm=alg))


async def test_token_con_tenant_manipulado_y_otra_clave_es_401(
    client, owner, tenant_b, id_de_a, owner_b
):
    payload = {**_claims(owner_b), "tenant_id": str(owner.tenant_id), "type": "access"}
    token = _firmar(payload, "otra-clave-que-no-es-la-del-servidor-0123456789")
    for ruta in (f"{URL}/{id_de_a}", URL, "/v1/auth/me"):
        r = await client.get(ruta, cookies={ACCESS_COOKIE: token})
        assert r.status_code == 401, ruta


async def test_token_de_b_con_payload_editado_sin_refirmar_es_401(client, owner_b, owner, id_de_a):
    legit = session_cookies(owner_b)[ACCESS_COOKIE]
    header, _, firma = legit.split(".")
    otro = create_access_token({**_claims(owner_b), "tenant_id": str(owner.tenant_id)})
    _, payload_a, _ = otro.split(".")
    frankenstein = f"{header}.{payload_a}.{firma}"
    r = await client.get(f"{URL}/{id_de_a}", cookies={ACCESS_COOKIE: frankenstein})
    assert r.status_code == 401


async def test_token_alg_none_es_401(client, owner, id_de_a):
    import base64
    import json

    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    token = (
        f"{b64({'alg': 'none', 'typ': 'JWT'})}."
        f"{b64({**_claims(owner), 'type': 'access', 'exp': exp})}."
    )
    assert (await client.get(f"{URL}/{id_de_a}", cookies={ACCESS_COOKIE: token})).status_code == 401


async def test_token_bien_firmado_con_usuario_de_b_y_tenant_de_a_es_401(
    client, owner_b, tenant_a, id_de_a
):
    # Si algún día se emitiera un token con la pareja (sub, tenant) inconsistente, el
    # usuario no existe en ese tenant y la sesión no vale: no alcanza con firmar bien.
    token = create_access_token({**_claims(owner_b), "tenant_id": str(tenant_a.id)})
    for ruta in (f"{URL}/{id_de_a}", URL):
        assert (await client.get(ruta, cookies={ACCESS_COOKIE: token})).status_code == 401


async def test_refresh_token_usado_como_access_es_401(client, owner, id_de_a):
    refresh = create_refresh_token(_claims(owner))
    for ruta in (f"{URL}/{id_de_a}", URL, "/v1/auth/me"):
        assert (await client.get(ruta, cookies={ACCESS_COOKIE: refresh})).status_code == 401


async def test_token_de_otro_tenant_con_secreto_real_pero_sin_type_es_401(client, owner, id_de_a):
    token = _firmar(_claims(owner), get_settings().JWT_SECRET_KEY)
    assert (await client.get(f"{URL}/{id_de_a}", cookies={ACCESS_COOKIE: token})).status_code == 401


# ══ API sobre Postgres real (A8) ═══════════════════════════════════════════════


def _pg_cookies(user_id: uuid.UUID, tenant_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token({"sub": str(user_id), "tenant_id": str(tenant_id), "ver": 0})
    return {ACCESS_COOKIE: token}


@pytest.fixture
async def pg_escenario(pg_tenant_a, pg_tenant_b, pg_user_factory, pg_dummy_de_a):
    owner_a = await pg_user_factory(pg_tenant_a, "owner-a@pg.example.com")
    owner_b = await pg_user_factory(pg_tenant_b, "owner-b@pg.example.com")
    return {
        "tenant_a": pg_tenant_a,
        "tenant_b": pg_tenant_b,
        "cookies_a": _pg_cookies(owner_a, pg_tenant_a),
        "cookies_b": _pg_cookies(owner_b, pg_tenant_b),
        "id_de_a": pg_dummy_de_a,
    }


@pytest.mark.postgres
@pytest.mark.asyncio(loop_scope="session")
async def test_pg_get_patch_delete_ajeno_404_byte_identico(pg_client, pg_escenario):
    e = pg_escenario
    ajeno_id, falso_id = e["id_de_a"], uuid.uuid4()
    for metodo, kwargs in (
        ("GET", {}),
        ("PATCH", {"json": {"name": "pisado"}}),
        ("DELETE", {}),
    ):
        ajeno = await pg_client.request(
            metodo, f"{URL}/{ajeno_id}", cookies=e["cookies_b"], **kwargs
        )
        inexistente = await pg_client.request(
            metodo, f"{URL}/{falso_id}", cookies=e["cookies_b"], **kwargs
        )
        assert ajeno.status_code == 404, (metodo, ajeno.text)
        _mismo_cuerpo(ajeno, inexistente)

    como_a = await pg_client.get(f"{URL}/{ajeno_id}", cookies=e["cookies_a"])
    assert como_a.status_code == 200
    assert como_a.json()["name"] == "de A", "PATCH/DELETE de B tocaron el recurso de A"


@pytest.mark.postgres
@pytest.mark.asyncio(loop_scope="session")
async def test_pg_listado_de_b_no_ve_ni_cuenta_lo_de_a(pg_client, pg_escenario):
    e = pg_escenario
    vacio = await pg_client.get(URL, cookies=e["cookies_b"])
    assert vacio.status_code == 200
    assert vacio.json()["items"] == [] and vacio.json()["total"] == 0

    propio = (await pg_client.post(URL, json={"name": "de B"}, cookies=e["cookies_b"])).json()
    body = (await pg_client.get(URL, cookies=e["cookies_b"])).json()
    assert [i["id"] for i in body["items"]] == [propio["id"]]
    assert body["total"] == 1
    assert str(e["id_de_a"]) not in {i["id"] for i in body["items"]}

    body_a = (await pg_client.get(URL, cookies=e["cookies_a"])).json()
    assert body_a["total"] == 1 and body_a["items"][0]["id"] == str(e["id_de_a"])


@pytest.mark.postgres
@pytest.mark.asyncio(loop_scope="session")
async def test_pg_crear_con_tenant_de_a_en_el_body_queda_en_b(
    pg_client, pg_escenario, pg_admin_engine
):
    e = pg_escenario
    r = await pg_client.post(
        URL,
        json={"name": "inyectado", "tenant_id": str(e["tenant_a"]), "tenantId": str(e["tenant_a"])},
        cookies=e["cookies_b"],
    )
    assert r.status_code == 201, r.text
    assert r.json()["tenant_id"] == str(e["tenant_b"])
    async with pg_admin_engine.connect() as conn:
        fila_tenant = await conn.scalar(
            text("SELECT tenant_id FROM dummy_resources WHERE id = :i"), {"i": r.json()["id"]}
        )
    assert fila_tenant == e["tenant_b"]
    assert (await pg_client.get(URL, cookies=e["cookies_a"])).json()["total"] == 1


@pytest.mark.postgres
@pytest.mark.asyncio(loop_scope="session")
async def test_pg_idempotency_key_por_tenant(pg_client, pg_escenario):
    e = pg_escenario
    h = {"Idempotency-Key": "pg-cruzada"}
    a1 = await pg_client.post(URL, json={"name": "a"}, headers=h, cookies=e["cookies_a"])
    a2 = await pg_client.post(URL, json={"name": "a"}, headers=h, cookies=e["cookies_a"])
    b1 = await pg_client.post(URL, json={"name": "b"}, headers=h, cookies=e["cookies_b"])
    assert a1.status_code == 201, a1.text
    assert a2.status_code == 409 and a2.json()["detail"]["code"] == "DUPLICATE_IDEMPOTENT"
    assert b1.status_code == 201, b1.text


@pytest.mark.postgres
@pytest.mark.asyncio(loop_scope="session")
async def test_pg_token_con_tenant_inconsistente_es_401(pg_client, pg_escenario, pg_user_factory):
    e = pg_escenario
    # token bien firmado: usuario de B, tenant de A → el contexto RLS queda en A, el
    # usuario no existe ahí, 401 (y nunca los datos de A).
    user_b = await pg_user_factory(e["tenant_b"], "cruzado@pg.example.com")
    cookies = _pg_cookies(user_b, e["tenant_a"])
    for ruta in (f"{URL}/{e['id_de_a']}", URL):
        r = await pg_client.get(ruta, cookies=cookies)
        assert r.status_code == 401, (ruta, r.text)
