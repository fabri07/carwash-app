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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest
from jose import jwt
from sqlalchemy import select, text

from app.config.settings import get_settings
from app.domain.roles import Role
from app.persistence.models import (
    BusinessHours,
    PaymentMethod,
    PermissionProfile,
    Resource,
    User,
    VehicleSize,
)
from app.persistence.models.tenant import TenantSettings
from app.tests.conftest import make_user, session_cookies
from app.utils.cookies import ACCESS_COOKIE
from app.utils.security import create_access_token, create_refresh_token

#: Sujeto de los tests cruzados desde F4 (Y12): los perfiles de permisos, solo OWNER.
URL = "/v1/permission-profiles"
STAFF_URL = "/v1/staff"
PERMISOS = ["AGENDA_VER"]


def _perfil(name: str, **extra: object) -> dict[str, object]:
    return {"name": name, "permissions": PERMISOS, **extra}


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
    ajeno = await client.patch(
        f"{URL}/{id_de_a}",
        json={"name": "pisado", "permissions": ["COBROS_ANULAR"]},
        cookies=cookies_b,
    )
    inexistente = await client.patch(
        f"{URL}/{uuid.uuid4()}",
        json={"name": "pisado", "permissions": ["COBROS_ANULAR"]},
        cookies=cookies_b,
    )
    assert ajeno.status_code == 404
    _mismo_cuerpo(ajeno, inexistente)

    como_a = await client.get(f"{URL}/{id_de_a}", cookies=cookies_a)
    assert como_a.status_code == 200
    assert como_a.json()["name"] == "perfil de A"
    assert como_a.json()["permissions"] == PERMISOS


async def test_delete_ajeno_es_404_identico_y_no_anula(
    client, cookies_a, cookies_b, id_de_a, db_session
):
    ajeno = await client.delete(f"{URL}/{id_de_a}", cookies=cookies_b)
    inexistente = await client.delete(f"{URL}/{uuid.uuid4()}", cookies=cookies_b)
    assert ajeno.status_code == 404
    _mismo_cuerpo(ajeno, inexistente)

    assert (await client.get(f"{URL}/{id_de_a}", cookies=cookies_a)).status_code == 200
    fila = await db_session.scalar(select(PermissionProfile).where(PermissionProfile.id == id_de_a))
    assert fila is not None and fila.voided_at is None and fila.void_reason is None


@pytest.mark.parametrize(
    ("metodo", "sufijo", "body"),
    [
        ("GET", "", None),
        ("PATCH", "", {"name": "pisado"}),
        ("DELETE", "", None),
    ],
    ids=["GET", "PATCH", "DELETE"],
)
async def test_staff_de_b_no_distingue_ajeno_de_inexistente(
    client, db_session, tenant_b, id_de_a, metodo, sufijo, body
):
    # Desde F4 todo `/permission-profiles` es OWNER-only: el 403 de rol sale antes de
    # mirar el id. Lo que importa es que salga igual para el id ajeno que para uno que no
    # existe, en cualquier método. El STAFF tiene un perfil con TODOS los permisos: no
    # alcanza con permisos, hace falta ser el dueño (Y5).
    staff_b = await make_user(db_session, tenant_b, Role.STAFF, "staff-b@example.com")
    cookies = session_cookies(staff_b)
    kwargs = {"json": body} if body is not None else {}
    ajeno = await client.request(metodo, f"{URL}/{id_de_a}{sufijo}", cookies=cookies, **kwargs)
    inexistente = await client.request(
        metodo, f"{URL}/{uuid.uuid4()}{sufijo}", cookies=cookies, **kwargs
    )
    assert ajeno.status_code == 403
    assert ajeno.json()["detail"]["code"] == "FORBIDDEN"
    _mismo_cuerpo(ajeno, inexistente)


# ── /staff: el empleado de A no existe para B ──────────────────────────────────


async def _empleado_de_a(db_session, tenant_a, perfil_a) -> User:
    return await make_user(
        db_session, tenant_a, Role.STAFF, None, username="lavador.a", profile=perfil_a
    )


STAFF_INTENTOS = [
    ("GET", "", None),
    ("PATCH", "", {"username": "secuestrado"}),
    ("DELETE", "", None),
    ("POST", "/reset-password", {"password": "una-clave-nueva-123"}),
]
STAFF_IDS = ["GET", "PATCH", "DELETE", "reset-password"]


@pytest.mark.parametrize(("metodo", "sufijo", "body"), STAFF_INTENTOS, ids=STAFF_IDS)
async def test_staff_ajeno_es_404_identico_y_no_cambia_nada(
    client, db_session, tenant_a, perfil_a, cookies_a, cookies_b, metodo, sufijo, body
):
    empleado = await _empleado_de_a(db_session, tenant_a, perfil_a)
    antes = (empleado.username, empleado.password_hash, empleado.token_version)
    kwargs = {"json": body} if body is not None else {}
    ajeno = await client.request(
        metodo, f"{STAFF_URL}/{empleado.id}{sufijo}", cookies=cookies_b, **kwargs
    )
    inexistente = await client.request(
        metodo, f"{STAFF_URL}/{uuid.uuid4()}{sufijo}", cookies=cookies_b, **kwargs
    )
    assert ajeno.status_code == 404, ajeno.text
    _mismo_cuerpo(ajeno, inexistente)

    await db_session.refresh(empleado)
    assert (empleado.username, empleado.password_hash, empleado.token_version) == antes
    assert empleado.voided_at is None and empleado.must_change_password is False
    assert (await client.get(f"{STAFF_URL}/{empleado.id}", cookies=cookies_a)).status_code == 200


@pytest.mark.parametrize(("metodo", "sufijo", "body"), STAFF_INTENTOS, ids=STAFF_IDS)
async def test_el_owner_no_es_staff_ni_para_su_propio_duenio(
    client, owner, owner_b, cookies_a, cookies_b, metodo, sufijo, body
):
    """El OWNER no se ve ni se edita por `/staff` (§4): su id da el MISMO 404 que uno
    inexistente — para su propio dueño y para el dueño de otro tenant."""
    kwargs = {"json": body} if body is not None else {}
    for cookies in (cookies_a, cookies_b):
        propio = await client.request(
            metodo, f"{STAFF_URL}/{owner.id}{sufijo}", cookies=cookies, **kwargs
        )
        inexistente = await client.request(
            metodo, f"{STAFF_URL}/{uuid.uuid4()}{sufijo}", cookies=cookies, **kwargs
        )
        assert propio.status_code == 404, propio.text
        _mismo_cuerpo(propio, inexistente)
    assert (await client.get("/v1/auth/me", cookies=cookies_a)).status_code == 200


async def test_listado_de_staff_de_b_no_ve_ni_cuenta_a_los_de_a(
    client, db_session, tenant_a, perfil_a, cookies_a, cookies_b
):
    empleado = await _empleado_de_a(db_session, tenant_a, perfil_a)
    de_b = await client.get(STAFF_URL, cookies=cookies_b)
    assert de_b.status_code == 200
    assert de_b.json()["items"] == [] and de_b.json()["total"] == 0
    assert str(empleado.id) not in de_b.text and "lavador.a" not in de_b.text
    de_a = (await client.get(STAFF_URL, cookies=cookies_a)).json()
    assert [i["id"] for i in de_a["items"]] == [str(empleado.id)]


async def test_crear_staff_en_b_con_perfil_de_a_es_404_identico_al_inexistente(
    client, db_session, perfil_a, cookies_b
):
    def cuerpo(perfil: uuid.UUID, username: str) -> dict[str, object]:
        return {
            "username": username,
            "password": "clave-inicial-123",
            "permission_profile_id": str(perfil),
        }

    ajeno = await client.post(STAFF_URL, json=cuerpo(perfil_a.id, "colado.b"), cookies=cookies_b)
    inexistente = await client.post(
        STAFF_URL, json=cuerpo(uuid.uuid4(), "colado.b"), cookies=cookies_b
    )
    assert ajeno.status_code == 404, ajeno.text
    _mismo_cuerpo(ajeno, inexistente)
    assert await db_session.scalar(select(User).where(User.username == "colado.b")) is None


async def test_mover_staff_de_b_al_perfil_de_a_es_404_y_no_lo_mueve(
    client, db_session, tenant_b, perfil_a, cookies_b
):
    staff_b = await make_user(db_session, tenant_b, Role.STAFF, None, username="lavador.b")
    perfil_original = staff_b.permission_profile_id
    ajeno = await client.patch(
        f"{STAFF_URL}/{staff_b.id}",
        json={"permission_profile_id": str(perfil_a.id)},
        cookies=cookies_b,
    )
    inexistente = await client.patch(
        f"{STAFF_URL}/{staff_b.id}",
        json={"permission_profile_id": str(uuid.uuid4())},
        cookies=cookies_b,
    )
    assert ajeno.status_code == 404, ajeno.text
    _mismo_cuerpo(ajeno, inexistente)
    await db_session.refresh(staff_b)
    assert staff_b.permission_profile_id == perfil_original


async def test_listado_de_b_no_contiene_ni_cuenta_lo_de_a(client, cookies_a, cookies_b, id_de_a):
    for i in range(3):
        r = await client.post(URL, json=_perfil(f"a{i}"), cookies=cookies_a)
        assert r.status_code == 201
    propio = await client.post(URL, json=_perfil("de B"), cookies=cookies_b)
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
    r = await client.post(URL, json=_perfil("de A"), cookies=cookies_a)
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
    body: dict[str, object] = _perfil("inyectado")
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
    assert listado_a["total"] == 1  # solo el perfil original de A
    assert (await client.get(f"{URL}/{id_de_a}", cookies=cookies_a)).json()["name"] == "perfil de A"


async def test_patch_propio_no_puede_mudar_el_recurso_al_otro_tenant(
    client, cookies_a, cookies_b, tenant_a, tenant_b
):
    creado = (await client.post(URL, json=_perfil("de B"), cookies=cookies_b)).json()
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
    a1 = await client.post(URL, json=_perfil("a"), headers=headers, cookies=cookies_a)
    a2 = await client.post(URL, json=_perfil("a"), headers=headers, cookies=cookies_a)
    b1 = await client.post(URL, json=_perfil("b"), headers=headers, cookies=cookies_b)
    b2 = await client.post(URL, json=_perfil("b"), headers=headers, cookies=cookies_b)
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
        await client.post(URL, json=_perfil("a"), headers=headers, cookies=cookies_a)
    ).status_code == 201
    usada = await client.post(URL, json=_perfil("b"), headers=headers, cookies=cookies_b)
    virgen = await client.post(
        URL, json=_perfil("b2"), headers={"Idempotency-Key": "nunca-usada"}, cookies=cookies_b
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
async def pg_escenario(pg_tenant_a, pg_tenant_b, pg_user_factory, pg_perfil_de_a):
    owner_a = await pg_user_factory(pg_tenant_a, "owner-a@pg.example.com")
    owner_b = await pg_user_factory(pg_tenant_b, "owner-b@pg.example.com")
    return {
        "tenant_a": pg_tenant_a,
        "tenant_b": pg_tenant_b,
        "cookies_a": _pg_cookies(owner_a, pg_tenant_a),
        "cookies_b": _pg_cookies(owner_b, pg_tenant_b),
        "id_de_a": pg_perfil_de_a,
    }


@pytest.mark.postgres
@pytest.mark.asyncio(loop_scope="session")
async def test_pg_get_patch_delete_ajeno_404_byte_identico(pg_client, pg_escenario):
    e = pg_escenario
    ajeno_id, falso_id = e["id_de_a"], uuid.uuid4()
    for metodo, kwargs in (
        ("GET", {}),
        ("PATCH", {"json": {"name": "pisado", "permissions": ["COBROS_ANULAR"]}}),
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

    propio = (await pg_client.post(URL, json=_perfil("de B"), cookies=e["cookies_b"])).json()
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
        json=_perfil("inyectado", tenant_id=str(e["tenant_a"]), tenantId=str(e["tenant_a"])),
        cookies=e["cookies_b"],
    )
    assert r.status_code == 201, r.text
    assert r.json()["tenant_id"] == str(e["tenant_b"])
    async with pg_admin_engine.connect() as conn:
        fila_tenant = await conn.scalar(
            text("SELECT tenant_id FROM permission_profiles WHERE id = :i"), {"i": r.json()["id"]}
        )
    assert fila_tenant == e["tenant_b"]
    assert (await pg_client.get(URL, cookies=e["cookies_a"])).json()["total"] == 1


@pytest.mark.postgres
@pytest.mark.asyncio(loop_scope="session")
async def test_pg_idempotency_key_por_tenant(pg_client, pg_escenario):
    e = pg_escenario
    h = {"Idempotency-Key": "pg-cruzada"}
    a1 = await pg_client.post(URL, json=_perfil("a"), headers=h, cookies=e["cookies_a"])
    a2 = await pg_client.post(URL, json=_perfil("a"), headers=h, cookies=e["cookies_a"])
    b1 = await pg_client.post(URL, json=_perfil("b"), headers=h, cookies=e["cookies_b"])
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


# ══ PR 4.2: /settings y los cuatro catálogos (A2) ═══════════════════════════════
#
# Escrito por el tester de aceptación del PR 4.2, en paralelo con el implementador y a
# partir del contrato (FASE-4-CONTRATO §3.2, §4, A2, Y11). Las filas de A se crean por el
# ORM (SQLite) o por SQL de admin (Postgres): el ataque solo depende de GET/PATCH/DELETE.

SETTINGS_URL = "/v1/settings"


@dataclass(frozen=True)
class Catalogo:
    """Un catálogo de 4.2: ruta, modelo, cómo nace una fila y qué se intenta pisar."""

    nombre: str
    url: str
    modelo: type[Any]
    #: kwargs del ORM para la fila `i` (sin `tenant_id`).
    fila: Callable[[int], dict[str, Any]]
    #: body del POST para la fila `i`.
    alta: Callable[[int], dict[str, Any]]
    #: body del PATCH (campo, valor pisado).
    cambio: dict[str, Any]
    #: SQL de admin para Postgres: `INSERT … (id, tenant_id, …) VALUES (:id, :t, …)`.
    sql_alta: str


CATALOGOS = [
    Catalogo(
        nombre="vehicle-sizes",
        url="/v1/vehicle-sizes",
        modelo=VehicleSize,
        fila=lambda i: {"code": f"TAM_{i}", "label": f"Tamaño {i}", "sort_order": i},
        alta=lambda i: {"code": f"NUEVO_{i}", "label": f"Nuevo {i}", "sort_order": i},
        cambio={"label": "pisado"},
        sql_alta=(
            "INSERT INTO vehicle_sizes (id, tenant_id, code, label) "
            "VALUES (:id, :t, 'TAM_A', 'Tamaño de A')"
        ),
    ),
    Catalogo(
        nombre="resources",
        url="/v1/resources",
        modelo=Resource,
        fila=lambda i: {"name": f"Puesto {i}", "sort_order": i},
        alta=lambda i: {"name": f"Puesto nuevo {i}", "sort_order": i},
        cambio={"name": "pisado"},
        sql_alta="INSERT INTO resources (id, tenant_id, name) VALUES (:id, :t, 'Puesto de A')",
    ),
    Catalogo(
        nombre="business-hours",
        url="/v1/business-hours",
        modelo=BusinessHours,
        fila=lambda i: {"weekday": 1 + i % 7, "opens_at": time(9), "closes_at": time(18)},
        alta=lambda i: {"weekday": 1 + i % 7, "opens_at": "08:00:00", "closes_at": "12:00:00"},
        cambio={"closes_at": "23:00:00"},
        sql_alta=(
            "INSERT INTO business_hours (id, tenant_id, weekday, opens_at, closes_at) "
            "VALUES (:id, :t, 1, '09:00', '18:00')"
        ),
    ),
    Catalogo(
        nombre="payment-methods",
        url="/v1/payment-methods",
        modelo=PaymentMethod,
        fila=lambda i: {"code": f"MEDIO_{i}", "label": f"Medio {i}"},
        alta=lambda i: {"code": f"NUEVO_{i}", "label": f"Medio nuevo {i}"},
        cambio={"label": "pisado"},
        sql_alta=(
            "INSERT INTO payment_methods (id, tenant_id, code, label) "
            "VALUES (:id, :t, 'MEDIO_A', 'Medio de A')"
        ),
    ),
]
CATALOGO_IDS = [c.nombre for c in CATALOGOS]


async def _fila_de(db_session, cat: Catalogo, tenant, i: int = 0) -> Any:
    fila = cat.modelo(id=uuid.uuid4(), tenant_id=tenant.id, **cat.fila(i))
    db_session.add(fila)
    await db_session.flush()
    return fila


def _snapshot(fila: Any, cat: Catalogo) -> dict[str, Any]:
    campos = [*cat.fila(0), "voided_at", "tenant_id"]
    return {c: getattr(fila, c) for c in campos}


async def _asegurar_settings(db_session, tenant) -> TenantSettings:
    """La fila 1:1 de settings del tenant: la crea si la fábrica de tenants no lo hizo."""
    fila = await db_session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
    )
    if fila is None:
        fila = TenantSettings(id=uuid.uuid4(), tenant_id=tenant.id)
        db_session.add(fila)
        await db_session.flush()
    return fila


# ── /settings ─────────────────────────────────────────────────────────────────


async def test_settings_de_b_nunca_muestra_los_de_a(
    client, db_session, tenant_a, tenant_b, cookies_a, cookies_b
):
    de_a = await _asegurar_settings(db_session, tenant_a)
    de_a.address = "Calle Sintética de A 123"
    de_a.welcome_text = "Bienvenida exclusiva de A"
    de_a.deposit_enabled = True
    de_a.deposit_bps = 3300
    await _asegurar_settings(db_session, tenant_b)
    await db_session.flush()

    r = await client.get(SETTINGS_URL, cookies=cookies_b)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "Sintética de A" not in r.text and "exclusiva de A" not in r.text
    assert str(tenant_a.id) not in r.text
    assert body["deposit_bps"] != 3300 and body["deposit_enabled"] is False
    if "tenant_id" in body:
        assert body["tenant_id"] == str(tenant_b.id)
    # y A ve lo suyo
    propio = (await client.get(SETTINGS_URL, cookies=cookies_a)).json()
    assert propio["address"] == "Calle Sintética de A 123" and propio["deposit_bps"] == 3300


@pytest.mark.parametrize(
    "extra",
    [{"tenant_id": "A"}, {"tenantId": "A"}, {"id": "SETTINGS_A"}],
    ids=["tenant_id", "tenantId-camel", "id-de-los-settings-de-A"],
)
async def test_patch_settings_de_b_con_tenant_de_a_en_el_body_no_toca_a_a(
    client, db_session, tenant_a, tenant_b, cookies_a, cookies_b, extra
):
    de_a = await _asegurar_settings(db_session, tenant_a)
    de_a.address = "Dirección original de A"
    await _asegurar_settings(db_session, tenant_b)
    await db_session.flush()

    body: dict[str, object] = {"address": "Inyectada por B"}
    for k, v in extra.items():
        body[k] = str(tenant_a.id) if v == "A" else str(de_a.id)
    r = await client.patch(SETTINGS_URL, json=body, cookies=cookies_b)
    assert r.status_code in (200, 422), r.text

    como_a = (await client.get(SETTINGS_URL, cookies=cookies_a)).json()
    assert como_a["address"] == "Dirección original de A"
    await db_session.refresh(de_a)
    assert de_a.address == "Dirección original de A" and de_a.tenant_id == tenant_a.id
    if r.status_code == 200:
        assert (await client.get(SETTINGS_URL, cookies=cookies_b)).json()[
            "address"
        ] == "Inyectada por B"


# ── Catálogos: 404 ajeno = inexistente, listados y tenant_id del body ──────────


@pytest.mark.parametrize("cat", CATALOGOS, ids=CATALOGO_IDS)
@pytest.mark.parametrize("metodo", ["GET", "PATCH", "DELETE"])
async def test_catalogo_ajeno_es_404_identico_al_inexistente_y_no_cambia(
    client, db_session, tenant_a, cookies_a, cookies_b, cat, metodo
):
    fila = await _fila_de(db_session, cat, tenant_a)
    antes = _snapshot(fila, cat)
    kwargs = {"json": cat.cambio} if metodo == "PATCH" else {}
    ajeno = await client.request(metodo, f"{cat.url}/{fila.id}", cookies=cookies_b, **kwargs)
    inexistente = await client.request(
        metodo, f"{cat.url}/{uuid.uuid4()}", cookies=cookies_b, **kwargs
    )
    assert ajeno.status_code == 404, ajeno.text
    assert ajeno.json()["detail"]["code"] == "NOT_FOUND"
    _mismo_cuerpo(ajeno, inexistente)

    await db_session.refresh(fila)
    assert _snapshot(fila, cat) == antes
    assert (await client.get(f"{cat.url}/{fila.id}", cookies=cookies_a)).status_code == 200


@pytest.mark.parametrize("cat", CATALOGOS, ids=CATALOGO_IDS)
async def test_listado_de_catalogo_de_b_no_ve_ni_cuenta_lo_de_a(
    client, db_session, tenant_a, tenant_b, cookies_a, cookies_b, cat
):
    vacio_antes = await client.get(cat.url, cookies=cookies_b)
    assert vacio_antes.status_code == 200, vacio_antes.text
    de_a = [await _fila_de(db_session, cat, tenant_a, i) for i in range(3)]
    vacio_despues = await client.get(cat.url, cookies=cookies_b)
    assert vacio_antes.content == vacio_despues.content
    assert vacio_despues.json()["items"] == [] and vacio_despues.json()["total"] == 0

    propia = await _fila_de(db_session, cat, tenant_b, 5)
    body = (await client.get(cat.url, cookies=cookies_b)).json()
    assert [i["id"] for i in body["items"]] == [str(propia.id)]
    assert body["total"] == 1 and body["has_more"] is False
    lejos = (await client.get(f"{cat.url}?limit=50&offset=1", cookies=cookies_b)).json()
    assert lejos["items"] == [] and lejos["total"] == 1

    body_a = (await client.get(cat.url, cookies=cookies_a)).json()
    assert {i["id"] for i in body_a["items"]} == {str(f.id) for f in de_a}
    assert body_a["total"] == 3


@pytest.mark.parametrize("cat", CATALOGOS, ids=CATALOGO_IDS)
@pytest.mark.parametrize(
    "extra",
    [{"tenant_id": "A"}, {"tenantId": "A"}, {"tenant_id": "A", "id": "FILA_A"}],
    ids=["tenant_id", "tenantId-camel", "tenant_id-e-id-ajeno"],
)
async def test_crear_catalogo_en_b_con_tenant_de_a_en_el_body_queda_en_b(
    client, db_session, tenant_a, tenant_b, cookies_a, cookies_b, cat, extra
):
    fila_a = await _fila_de(db_session, cat, tenant_a)
    body = cat.alta(1)
    for k, v in extra.items():
        body[k] = str(tenant_a.id) if v == "A" else str(fila_a.id)
    r = await client.post(cat.url, json=body, cookies=cookies_b)
    assert r.status_code in (201, 422), r.text
    if r.status_code == 201:
        creado = r.json()
        assert creado["id"] != str(fila_a.id)
        if "tenant_id" in creado:
            assert creado["tenant_id"] == str(tenant_b.id)
        assert (await client.get(f"{cat.url}/{creado['id']}", cookies=cookies_a)).status_code == 404
        en_base = await db_session.scalar(
            select(cat.modelo).where(cat.modelo.id == uuid.UUID(creado["id"]))
        )
        assert en_base is not None and en_base.tenant_id == tenant_b.id
    listado_a = (await client.get(cat.url, cookies=cookies_a)).json()
    assert listado_a["total"] == 1 and listado_a["items"][0]["id"] == str(fila_a.id)


@pytest.mark.parametrize("cat", CATALOGOS, ids=CATALOGO_IDS)
async def test_patch_de_catalogo_propio_no_lo_muda_al_otro_tenant(
    client, db_session, tenant_a, tenant_b, cookies_a, cookies_b, cat
):
    propia = await _fila_de(db_session, cat, tenant_b)
    r = await client.patch(
        f"{cat.url}/{propia.id}",
        json={**cat.cambio, "tenant_id": str(tenant_a.id)},
        cookies=cookies_b,
    )
    assert r.status_code in (200, 422), r.text
    await db_session.refresh(propia)
    assert propia.tenant_id == tenant_b.id
    assert (await client.get(f"{cat.url}/{propia.id}", cookies=cookies_a)).status_code == 404
    assert (await client.get(cat.url, cookies=cookies_a)).json()["total"] == 0


# ── PR 4.2 sobre Postgres real ─────────────────────────────────────────────────


async def _pg_settings(pg_admin_engine, tenant_id: uuid.UUID, address: str) -> None:
    """La fila de settings con una dirección reconocible (la crea si la fábrica no lo hizo)."""
    async with pg_admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenant_settings (id, tenant_id, address) VALUES (:id, :t, :a) "
                "ON CONFLICT (tenant_id) DO UPDATE SET address = EXCLUDED.address"
            ),
            {"id": uuid.uuid4(), "t": tenant_id, "a": address},
        )


@pytest.mark.postgres
@pytest.mark.asyncio(loop_scope="session")
async def test_pg_settings_aislados_y_tenant_id_del_body_ignorado(
    pg_client, pg_escenario, pg_admin_engine
):
    e = pg_escenario
    await _pg_settings(pg_admin_engine, e["tenant_a"], "Dirección sintética de A")
    await _pg_settings(pg_admin_engine, e["tenant_b"], "Dirección sintética de B")

    r = await pg_client.get(SETTINGS_URL, cookies=e["cookies_b"])
    assert r.status_code == 200, r.text
    assert r.json()["address"] == "Dirección sintética de B"
    assert "de A" not in r.text and str(e["tenant_a"]) not in r.text

    p = await pg_client.patch(
        SETTINGS_URL,
        json={"address": "Inyectada", "tenant_id": str(e["tenant_a"])},
        cookies=e["cookies_b"],
    )
    assert p.status_code in (200, 422), p.text
    async with pg_admin_engine.connect() as conn:
        dir_a = await conn.scalar(
            text("SELECT address FROM tenant_settings WHERE tenant_id = :t"), {"t": e["tenant_a"]}
        )
        filas = await conn.scalar(text("SELECT count(*) FROM tenant_settings"))
    assert dir_a == "Dirección sintética de A"
    assert filas == 2


@pytest.mark.postgres
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("cat", CATALOGOS, ids=CATALOGO_IDS)
async def test_pg_catalogo_ajeno_404_byte_identico_listado_y_body(
    pg_client, pg_escenario, pg_admin_engine, cat
):
    e = pg_escenario
    id_a = uuid.uuid4()
    async with pg_admin_engine.begin() as conn:
        await conn.execute(text(cat.sql_alta), {"id": id_a, "t": e["tenant_a"]})

    for metodo in ("GET", "PATCH", "DELETE"):
        kwargs = {"json": cat.cambio} if metodo == "PATCH" else {}
        ajeno = await pg_client.request(
            metodo, f"{cat.url}/{id_a}", cookies=e["cookies_b"], **kwargs
        )
        inexistente = await pg_client.request(
            metodo, f"{cat.url}/{uuid.uuid4()}", cookies=e["cookies_b"], **kwargs
        )
        assert ajeno.status_code == 404, (metodo, ajeno.text)
        _mismo_cuerpo(ajeno, inexistente)
    async with pg_admin_engine.connect() as conn:
        anulada = await conn.scalar(
            text(f"SELECT voided_at FROM {cat.modelo.__tablename__} WHERE id = :i"), {"i": id_a}
        )
    assert anulada is None, "el DELETE de B anuló la fila de A"

    vacio = (await pg_client.get(cat.url, cookies=e["cookies_b"])).json()
    assert vacio["items"] == [] and vacio["total"] == 0

    r = await pg_client.post(
        cat.url,
        json={**cat.alta(1), "tenant_id": str(e["tenant_a"]), "tenantId": str(e["tenant_a"])},
        cookies=e["cookies_b"],
    )
    assert r.status_code in (201, 422), r.text
    if r.status_code == 201:
        async with pg_admin_engine.connect() as conn:
            duenio = await conn.scalar(
                text(f"SELECT tenant_id FROM {cat.modelo.__tablename__} WHERE id = :i"),
                {"i": r.json()["id"]},
            )
        assert duenio == e["tenant_b"]
    de_a = (await pg_client.get(cat.url, cookies=e["cookies_a"])).json()
    assert de_a["total"] == 1 and de_a["items"][0]["id"] == str(id_a)
