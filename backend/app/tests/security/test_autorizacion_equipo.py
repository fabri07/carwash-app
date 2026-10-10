"""A3 · A4 · A5 · FASE-4-CONTRATO §2.3 — autorización dentro del propio tenant (SQLite).

Escrito por el Tester-aislamiento del PR 4.1, que no implementó el backend. Los otros
archivos de `security/` prueban que un negocio no ve al otro; este prueba que, **dentro**
de un negocio, un empleado no hace más de lo que su perfil le da:

1. **Equipo = OWNER only (Y5, A4).** Un STAFF con TODOS los permisos igual recibe 403 en
   perfiles y staff, en todo método, idéntico para id ajeno, inexistente o propio; no puede
   editarse el perfil ni darse permisos.
2. **`require_permission` (A3).** Con una mini-app montada sobre la app real: OWNER pasa
   siempre, Cajero no anula cobros, Encargado sí; matriz permiso × ruta generada desde el
   enum; un cambio de perfil rige en el request siguiente con el MISMO token; perfil anulado
   = sin permisos.
3. **Compuerta de clave (§2.3).** Con `must_change_password` solo responden `me`,
   `change-password`, `refresh` y `logout`; el resto, 403 `PASSWORD_CHANGE_REQUIRED`.
4. **Revocación (A5).** Anular o resetear a un empleado mata su cookie en el request
   siguiente; anulado no entra.
5. **Login por usuario o email (A5)** y unicidad global de `username` sin filtrar el tenant.

Datos sintéticos: `@example.com`, usuarios inventados.
"""

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import APIRouter, Depends
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import require_permission
from app.domain.permissions import ALL_PERMISSIONS, DEFAULT_PROFILES, Permission
from app.domain.roles import Role
from app.domain.void import VoidReason
from app.main import create_app
from app.persistence.db.redis import get_redis
from app.persistence.db.session import get_db_session
from app.persistence.models import PermissionProfile, Tenant, User
from app.tests.conftest import (
    TEST_PASSWORD,
    FakeRedis,
    make_profile,
    make_tenant,
    make_user,
    session_cookies,
)
from app.utils.cookies import ACCESS_COOKIE, REFRESH_COOKIE
from app.utils.datetime_utils import utcnow
from app.utils.security import create_refresh_token

PERFILES = "/v1/permission-profiles"
STAFF = "/v1/staff"
PRUEBA = "/v1/prueba"
NUEVA_CLAVE = "otra-clave-segura-456"


def _codigo(r: Any) -> str:
    return r.json()["detail"]["code"]


def _mismo_cuerpo(a: Any, b: Any) -> None:
    assert a.status_code == b.status_code
    assert a.content == b.content


def _refresh_cookies(user: User) -> dict[str, str]:
    claims = {"sub": str(user.id), "tenant_id": str(user.tenant_id), "ver": user.token_version}
    return {REFRESH_COOKIE: create_refresh_token(claims)}


# ── Mini-app: rutas de prueba con `require_permission` sobre la app real ──────


def _ruta_de(permiso: Permission) -> str:
    return f"{PRUEBA}/permiso/{permiso.value.lower()}"


def _router_de_prueba() -> APIRouter:
    router = APIRouter()

    async def ok() -> dict[str, bool]:
        return {"ok": True}

    # Una ruta por permiso: la matriz sale del enum, no de una lista a mano.
    for permiso in Permission:
        router.add_api_route(
            f"/permiso/{permiso.value.lower()}",
            ok,
            methods=["GET"],
            dependencies=[Depends(require_permission(permiso))],
        )
    # Crear un servicio exige los dos (§2.3): CATALOGO_EDITAR **y** PRECIOS_EDITAR.
    router.add_api_route(
        "/servicio",
        ok,
        methods=["POST"],
        dependencies=[
            Depends(require_permission(Permission.CATALOGO_EDITAR, Permission.PRECIOS_EDITAR))
        ],
    )
    return router


@pytest_asyncio.fixture
async def cliente(
    db_session: AsyncSession, fake_redis: FakeRedis
) -> AsyncGenerator[AsyncClient, None]:
    """El `client` de conftest, más las rutas de prueba montadas sobre la MISMA app."""
    from app.api.rate_limit import limiter  # noqa: PLC0415

    limiter.reset()
    app = create_app()
    app.include_router(_router_de_prueba(), prefix=PRUEBA)

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def override_redis() -> FakeRedis:
        return fake_redis

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_redis] = override_redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as ac:
        yield ac


async def _empleado(
    session: AsyncSession,
    tenant: Tenant,
    permisos: frozenset[Permission] | set[Permission],
    *,
    username: str | None = None,
    must_change_password: bool = False,
) -> tuple[User, PermissionProfile]:
    perfil = await make_profile(session, tenant, f"perfil {uuid.uuid4().hex[:8]}", permisos)
    user = await make_user(
        session,
        tenant,
        Role.STAFF,
        None,
        username=username or f"emp.{uuid.uuid4().hex[:10]}",
        profile=perfil,
        must_change_password=must_change_password,
    )
    return user, perfil


# ══ 1 · Equipo: solo el OWNER ═════════════════════════════════════════════════


def _rutas_de_equipo() -> list[tuple[str, str]]:
    rutas = []
    for ruta in create_app().routes:
        if isinstance(ruta, APIRoute) and ruta.path.startswith((PERFILES, STAFF)):
            for metodo in sorted(ruta.methods):
                rutas.append((metodo, ruta.path))
    assert len(rutas) == 11  # 5 de perfiles + 6 de staff (§4): si cambia, revisar la lista
    return rutas


RUTAS_DE_EQUIPO = _rutas_de_equipo()


@pytest.mark.parametrize(("metodo", "path"), RUTAS_DE_EQUIPO, ids=lambda x: str(x))
async def test_staff_con_todos_los_permisos_recibe_403_en_equipo(
    cliente, db_session, tenant_a, tenant_b, metodo, path
):
    """Y5: no hay permiso para "configurar el equipo"; ni el Encargado (todo) entra. El 403
    sale antes de buscar el id: igual para uno propio, uno de otro tenant y uno inexistente."""
    yo, mi_perfil = await _empleado(db_session, tenant_a, ALL_PERMISSIONS)
    _, perfil_de_b = await _empleado(db_session, tenant_b, ALL_PERMISSIONS)
    cookies = session_cookies(yo)

    ids = [mi_perfil.id, perfil_de_b.id, uuid.uuid4()] if "{id}" in path else [None]
    respuestas = []
    for id_ in ids:
        url = path.replace("{id}", str(id_)) if id_ else path
        r = await cliente.request(metodo, url, cookies=cookies, json={})
        assert r.status_code == 403, (metodo, url, r.text)
        assert _codigo(r) == "FORBIDDEN"
        respuestas.append(r)
    for r in respuestas[1:]:
        _mismo_cuerpo(respuestas[0], r)


async def test_staff_no_puede_darse_permisos_ni_cambiarse_de_perfil(
    cliente, db_session, tenant_a, owner
):
    lavador, perfil = await _empleado(
        db_session, tenant_a, DEFAULT_PROFILES["Lavador"], username="lavador.uno"
    )
    encargado = await make_profile(db_session, tenant_a, "Encargado", ALL_PERMISSIONS)
    cookies = session_cookies(lavador)

    intentos = [
        ("PATCH", f"{PERFILES}/{perfil.id}", {"permissions": [p.value for p in Permission]}),
        ("PATCH", f"{STAFF}/{lavador.id}", {"permission_profile_id": str(encargado.id)}),
        ("POST", PERFILES, {"name": "mío", "permissions": [Permission.COBROS_ANULAR.value]}),
        (
            "POST",
            STAFF,
            {
                "username": "mi.otro.yo",
                "password": "clave-inicial-123",
                "permission_profile_id": str(encargado.id),
            },
        ),
        ("POST", f"{STAFF}/{owner.id}/reset-password", {"password": "le-robo-la-cuenta"}),
        ("DELETE", f"{STAFF}/{lavador.id}", None),
    ]
    for metodo, url, body in intentos:
        r = await cliente.request(metodo, url, json=body, cookies=cookies)
        assert r.status_code == 403, (metodo, url, r.text)

    await db_session.refresh(perfil)
    await db_session.refresh(lavador)
    assert set(perfil.permissions) == {p.value for p in DEFAULT_PROFILES["Lavador"]}
    assert lavador.permission_profile_id == perfil.id and lavador.voided_at is None
    assert await db_session.scalar(select(User).where(User.username == "mi.otro.yo")) is None
    # y sus permisos efectivos siguen siendo los de Lavador
    me = (await cliente.get("/v1/auth/me", cookies=cookies)).json()
    assert set(me["permissions"]) == {p.value for p in DEFAULT_PROFILES["Lavador"]}


@pytest.mark.parametrize(
    "extra",
    [{"role": "OWNER"}, {"must_change_password": False}, {"tenant_id": "B"}],
    ids=["role", "must_change_password", "tenant_id"],
)
async def test_crear_y_editar_staff_ignora_campos_que_no_son_del_contrato(
    client, db_session, tenant_a, tenant_b, cookies_a, extra
):
    """Asignación masiva: el dueño no puede crear otro OWNER, ni saltear el cambio de clave,
    ni mandar al empleado a otro tenant. `extra="ignore"`: se descarta en silencio."""
    perfil = await make_profile(db_session, tenant_a, "Lavador", DEFAULT_PROFILES["Lavador"])
    extra = {k: (str(tenant_b.id) if v == "B" else v) for k, v in extra.items()}
    r = await client.post(
        STAFF,
        json={
            "username": "nuevo.emp",
            "password": "clave-inicial-123",
            "permission_profile_id": str(perfil.id),
            **extra,
        },
        cookies=cookies_a,
    )
    assert r.status_code == 201, r.text
    creado = r.json()
    assert creado["role"] == "STAFF"
    assert creado["must_change_password"] is True
    assert creado["tenant_id"] == str(tenant_a.id)

    r = await client.patch(f"{STAFF}/{creado['id']}", json=extra, cookies=cookies_a)
    assert r.status_code == 200, r.text
    user = await db_session.get(User, uuid.UUID(creado["id"]))
    await db_session.refresh(user)
    assert user.role == Role.STAFF and user.must_change_password is True
    assert user.tenant_id == tenant_a.id


# ══ 2 · require_permission ════════════════════════════════════════════════════


async def test_el_owner_pasa_todas_las_rutas_de_permiso(cliente, owner, cookies_a):
    for permiso in Permission:
        r = await cliente.get(_ruta_de(permiso), cookies=cookies_a)
        assert r.status_code == 200, permiso
    assert (await cliente.post(f"{PRUEBA}/servicio", cookies=cookies_a)).status_code == 200


async def test_cajero_no_anula_cobros_y_encargado_si(cliente, db_session, tenant_a):
    cajero, _ = await _empleado(db_session, tenant_a, DEFAULT_PROFILES["Cajero"])
    encargado, _ = await _empleado(db_session, tenant_a, DEFAULT_PROFILES["Encargado"])

    anular = _ruta_de(Permission.COBROS_ANULAR)
    r = await cliente.get(anular, cookies=session_cookies(cajero))
    assert r.status_code == 403 and _codigo(r) == "FORBIDDEN"
    cobrar = _ruta_de(Permission.COBROS_REGISTRAR)
    assert (await cliente.get(cobrar, cookies=session_cookies(cajero))).status_code == 200
    assert (await cliente.get(anular, cookies=session_cookies(encargado))).status_code == 200


@pytest.mark.parametrize("permiso", list(Permission), ids=lambda p: p.value)
async def test_matriz_cada_permiso_abre_solo_su_ruta(cliente, db_session, tenant_a, permiso):
    """A3, generado desde el enum: con todos menos `permiso` → 403 en su ruta; con solo
    `permiso` → 200 en la suya y 403 en todas las demás."""
    sin, _ = await _empleado(db_session, tenant_a, ALL_PERMISSIONS - {permiso})
    solo, _ = await _empleado(db_session, tenant_a, {permiso})

    assert (await cliente.get(_ruta_de(permiso), cookies=session_cookies(sin))).status_code == 403
    for otro in Permission:
        r = await cliente.get(_ruta_de(otro), cookies=session_cookies(solo))
        assert r.status_code == (200 if otro == permiso else 403), (permiso, otro)


@pytest.mark.parametrize(
    "permisos",
    [{Permission.CATALOGO_EDITAR}, {Permission.PRECIOS_EDITAR}, set()],
    ids=["solo-catalogo", "solo-precios", "ninguno"],
)
async def test_crear_servicio_exige_catalogo_y_precios(cliente, db_session, tenant_a, permisos):
    emp, _ = await _empleado(db_session, tenant_a, permisos)
    r = await cliente.post(f"{PRUEBA}/servicio", cookies=session_cookies(emp))
    assert r.status_code == 403


async def test_crear_servicio_con_los_dos_permisos_pasa(cliente, db_session, tenant_a):
    emp, _ = await _empleado(
        db_session, tenant_a, {Permission.CATALOGO_EDITAR, Permission.PRECIOS_EDITAR}
    )
    assert (
        await cliente.post(f"{PRUEBA}/servicio", cookies=session_cookies(emp))
    ).status_code == 200


async def test_editar_el_perfil_rige_en_el_request_siguiente_con_el_mismo_token(
    cliente, db_session, tenant_a, cookies_a
):
    """§2.3: los permisos no viajan en el JWT. El dueño edita el perfil por la API y la
    MISMA cookie del empleado ya obedece al perfil nuevo, en los dos sentidos."""
    cajero, perfil = await _empleado(db_session, tenant_a, DEFAULT_PROFILES["Cajero"])
    cookies = session_cookies(cajero)
    anular = _ruta_de(Permission.COBROS_ANULAR)
    assert (await cliente.get(anular, cookies=cookies)).status_code == 403

    con_anular = [p.value for p in DEFAULT_PROFILES["Cajero"] | {Permission.COBROS_ANULAR}]
    r = await cliente.patch(
        f"{PERFILES}/{perfil.id}", json={"permissions": con_anular}, cookies=cookies_a
    )
    assert r.status_code == 200, r.text
    assert (await cliente.get(anular, cookies=cookies)).status_code == 200
    me = (await cliente.get("/v1/auth/me", cookies=cookies)).json()
    assert Permission.COBROS_ANULAR.value in me["permissions"]

    r = await cliente.patch(f"{PERFILES}/{perfil.id}", json={"permissions": []}, cookies=cookies_a)
    assert r.status_code == 200, r.text
    for permiso in Permission:
        assert (await cliente.get(_ruta_de(permiso), cookies=cookies)).status_code == 403
    assert (await cliente.get("/v1/auth/me", cookies=cookies)).json()["permissions"] == []


async def test_cambiar_al_empleado_de_perfil_rige_en_el_request_siguiente(
    cliente, db_session, tenant_a, cookies_a
):
    lavador, _ = await _empleado(db_session, tenant_a, DEFAULT_PROFILES["Lavador"])
    encargado = await make_profile(db_session, tenant_a, "Encargado", ALL_PERMISSIONS)
    cookies = session_cookies(lavador)
    caja = _ruta_de(Permission.CAJA_VER)
    assert (await cliente.get(caja, cookies=cookies)).status_code == 403

    r = await cliente.patch(
        f"{STAFF}/{lavador.id}",
        json={"permission_profile_id": str(encargado.id)},
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    assert (await cliente.get(caja, cookies=cookies)).status_code == 200


async def test_perfil_anulado_es_sin_permisos(cliente, db_session, tenant_a, cookies_a):
    """La API no deja anular un perfil en uso (409); si igual quedara anulado (carrera,
    carga a mano), el empleado se queda sin permisos: falla cerrado."""
    emp, perfil = await _empleado(db_session, tenant_a, ALL_PERMISSIONS)
    cookies = session_cookies(emp)

    r = await cliente.delete(f"{PERFILES}/{perfil.id}", cookies=cookies_a)
    assert r.status_code == 409 and _codigo(r) == "PROFILE_IN_USE"
    assert (await cliente.get(_ruta_de(Permission.AGENDA_VER), cookies=cookies)).status_code == 200

    perfil.voided_at = utcnow()
    perfil.void_reason = VoidReason.PEDIDO_DEL_USUARIO
    await db_session.flush()
    for permiso in Permission:
        assert (await cliente.get(_ruta_de(permiso), cookies=cookies)).status_code == 403
    me = await cliente.get("/v1/auth/me", cookies=cookies)
    assert me.status_code == 200 and me.json()["permissions"] == []


async def test_no_se_asigna_un_perfil_anulado(client, db_session, tenant_a, cookies_a):
    perfil = await make_profile(db_session, tenant_a, "viejo", {Permission.AGENDA_VER})
    assert (await client.delete(f"{PERFILES}/{perfil.id}", cookies=cookies_a)).status_code == 204
    r = await client.post(
        STAFF,
        json={
            "username": "con.perfil.viejo",
            "password": "clave-inicial-123",
            "permission_profile_id": str(perfil.id),
        },
        cookies=cookies_a,
    )
    assert r.status_code == 404


async def test_el_perfil_de_otro_tenant_no_da_permisos(cliente, db_session, tenant_a, tenant_b):
    """Si alguna vez un STAFF quedara apuntando a un perfil de otro tenant (la FK compuesta
    lo impide en Postgres), `effective_permissions` filtra por tenant: no hereda nada."""
    emp, _ = await _empleado(db_session, tenant_a, {Permission.AGENDA_VER})
    de_b = await make_profile(db_session, tenant_b, "Encargado", ALL_PERMISSIONS)
    emp.permission_profile_id = de_b.id
    await db_session.flush()
    for permiso in Permission:
        r = await cliente.get(_ruta_de(permiso), cookies=session_cookies(emp))
        assert r.status_code == 403, permiso


# ══ 3 · Compuerta del cambio de clave ═════════════════════════════════════════


def _rutas_cerradas_por_la_compuerta() -> list[tuple[str, str]]:
    """Toda ruta de `/v1` salvo las 4 que §2.3 deja abiertas (y register/login, públicas)."""
    abiertas = {
        "/v1/auth/me",
        "/v1/auth/change-password",
        "/v1/auth/refresh",
        "/v1/auth/logout",
        "/v1/auth/login",
        "/v1/auth/register",
    }
    app = create_app()
    app.include_router(_router_de_prueba(), prefix=PRUEBA)
    rutas = []
    for ruta in app.routes:
        if isinstance(ruta, APIRoute) and ruta.path.startswith("/v1/"):
            if ruta.path in abiertas:
                continue
            for metodo in sorted(ruta.methods):
                rutas.append((metodo, ruta.path))
    return rutas


@pytest.mark.parametrize(
    ("metodo", "path"), _rutas_cerradas_por_la_compuerta(), ids=lambda x: str(x)
)
async def test_con_clave_pendiente_toda_otra_ruta_es_403(
    cliente, db_session, tenant_a, metodo, path
):
    """Cortado en el servidor (desde `CurrentUser`), para el OWNER y para el STAFF: la
    compuerta va antes del rol y del permiso, y antes de validar el cuerpo."""
    duenio = await make_user(
        db_session, tenant_a, Role.OWNER, "duenio-pendiente@example.com", must_change_password=True
    )
    emp, _ = await _empleado(db_session, tenant_a, ALL_PERMISSIONS, must_change_password=True)
    url = path.replace("{id}", str(uuid.uuid4()))
    for user in (duenio, emp):
        r = await cliente.request(metodo, url, cookies=session_cookies(user))
        assert r.status_code == 403, (user.role, metodo, url, r.text)
        assert _codigo(r) == "PASSWORD_CHANGE_REQUIRED"


async def test_con_clave_pendiente_responden_me_refresh_logout(cliente, db_session, tenant_a):
    emp, _ = await _empleado(
        db_session, tenant_a, DEFAULT_PROFILES["Cajero"], must_change_password=True
    )
    me = await cliente.get("/v1/auth/me", cookies=session_cookies(emp))
    assert me.status_code == 200, me.text
    assert me.json()["must_change_password"] is True
    # `/me` informa los permisos igual: el frontend arma el menú después del cambio
    assert set(me.json()["permissions"]) == {p.value for p in DEFAULT_PROFILES["Cajero"]}

    refresh = await cliente.post("/v1/auth/refresh", cookies=_refresh_cookies(emp))
    assert refresh.status_code == 200, refresh.text
    assert refresh.json()["must_change_password"] is True  # el refresh conserva el flag
    # el access nuevo que emitió el refresh sigue frenado por la compuerta
    nuevo = {ACCESS_COOKIE: refresh.cookies[ACCESS_COOKIE]}
    r = await cliente.get(_ruta_de(Permission.AGENDA_VER), cookies=nuevo)
    assert r.status_code == 403 and _codigo(r) == "PASSWORD_CHANGE_REQUIRED"

    logout = await cliente.post("/v1/auth/logout", cookies=_refresh_cookies(emp))
    assert logout.status_code == 204


async def test_cambiar_la_clave_abre_las_rutas_y_revoca_el_token_viejo(
    cliente, db_session, tenant_a
):
    emp, _ = await _empleado(
        db_session,
        tenant_a,
        DEFAULT_PROFILES["Lavador"],
        username="nuevo.lavador",
        must_change_password=True,
    )
    viejo = session_cookies(emp)
    agenda = _ruta_de(Permission.AGENDA_VER)
    assert (await cliente.get(agenda, cookies=viejo)).status_code == 403

    # clave actual incorrecta → 400 (no 401: el frontend no debe intentar refrescar)
    mal = await cliente.post(
        "/v1/auth/change-password",
        json={"current_password": "no-es-esta", "new_password": NUEVA_CLAVE},
        cookies=viejo,
    )
    assert mal.status_code == 400
    # la misma clave de nuevo → 400
    igual = await cliente.post(
        "/v1/auth/change-password",
        json={"current_password": TEST_PASSWORD, "new_password": TEST_PASSWORD},
        cookies=viejo,
    )
    assert igual.status_code == 400

    r = await cliente.post(
        "/v1/auth/change-password",
        json={"current_password": TEST_PASSWORD, "new_password": NUEVA_CLAVE},
        cookies=viejo,
    )
    assert r.status_code == 200, r.text
    assert r.json()["must_change_password"] is False
    nuevo = {ACCESS_COOKIE: r.cookies[ACCESS_COOKIE]}

    assert (await cliente.get(agenda, cookies=nuevo)).status_code == 200
    assert (await cliente.get(agenda, cookies=viejo)).status_code == 401
    assert (await cliente.get("/v1/auth/me", cookies=viejo)).status_code == 401
    # el refresh viejo tampoco sirve
    assert (
        await cliente.post("/v1/auth/refresh", cookies=_refresh_cookies_viejas(emp))
    ).status_code == 401

    # y entra con la clave nueva, no con la vieja
    cliente.cookies.clear()
    ok = await cliente.post(
        "/v1/auth/login", json={"identifier": "nuevo.lavador", "password": NUEVA_CLAVE}
    )
    assert ok.status_code == 200 and ok.json()["must_change_password"] is False
    cliente.cookies.clear()
    viejo_login = await cliente.post(
        "/v1/auth/login", json={"identifier": "nuevo.lavador", "password": TEST_PASSWORD}
    )
    assert viejo_login.status_code == 401


def _refresh_cookies_viejas(user: User) -> dict[str, str]:
    """Refresh firmado con la versión ANTERIOR del usuario (la que tenía antes del cambio)."""
    claims = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "ver": user.token_version - 1,
    }
    return {REFRESH_COOKIE: create_refresh_token(claims)}


# ══ 4 · Revocación ════════════════════════════════════════════════════════════


async def test_anular_un_empleado_cierra_sus_sesiones_y_no_vuelve_a_entrar(
    cliente, db_session, tenant_a, cookies_a
):
    emp, _ = await _empleado(db_session, tenant_a, ALL_PERMISSIONS, username="se.va")
    cookies, refresh = session_cookies(emp), _refresh_cookies(emp)
    assert (await cliente.get("/v1/auth/me", cookies=cookies)).status_code == 200

    assert (await cliente.delete(f"{STAFF}/{emp.id}", cookies=cookies_a)).status_code == 204

    assert (await cliente.get("/v1/auth/me", cookies=cookies)).status_code == 401
    r = await cliente.get(_ruta_de(Permission.AGENDA_VER), cookies=cookies)
    assert r.status_code == 401
    assert (await cliente.post("/v1/auth/refresh", cookies=refresh)).status_code == 401
    cliente.cookies.clear()
    login = await cliente.post(
        "/v1/auth/login", json={"identifier": "se.va", "password": TEST_PASSWORD}
    )
    assert login.status_code == 401 and _codigo(login) == "INVALID_CREDENTIALS"
    # anulado: ya no existe para el dueño (mismo 404 que uno inexistente)
    ajeno = await cliente.get(f"{STAFF}/{emp.id}", cookies=cookies_a)
    inexistente = await cliente.get(f"{STAFF}/{uuid.uuid4()}", cookies=cookies_a)
    assert ajeno.status_code == 404
    _mismo_cuerpo(ajeno, inexistente)
    listado = (await cliente.get(STAFF, cookies=cookies_a)).json()
    assert str(emp.id) not in {i["id"] for i in listado["items"]}


async def test_resetear_la_clave_cierra_sesiones_y_obliga_a_cambiarla(
    cliente, db_session, tenant_a, cookies_a
):
    emp, _ = await _empleado(db_session, tenant_a, ALL_PERMISSIONS, username="olvido.clave")
    cookies, refresh = session_cookies(emp), _refresh_cookies(emp)

    r = await cliente.post(
        f"{STAFF}/{emp.id}/reset-password", json={"password": NUEVA_CLAVE}, cookies=cookies_a
    )
    assert r.status_code == 200, r.text
    assert r.json()["must_change_password"] is True
    assert "password_hash" not in r.text and NUEVA_CLAVE not in r.text

    assert (await cliente.get("/v1/auth/me", cookies=cookies)).status_code == 401
    assert (await cliente.post("/v1/auth/refresh", cookies=refresh)).status_code == 401

    cliente.cookies.clear()
    vieja = await cliente.post(
        "/v1/auth/login", json={"identifier": "olvido.clave", "password": TEST_PASSWORD}
    )
    assert vieja.status_code == 401
    cliente.cookies.clear()
    nueva = await cliente.post(
        "/v1/auth/login", json={"identifier": "olvido.clave", "password": NUEVA_CLAVE}
    )
    assert nueva.status_code == 200 and nueva.json()["must_change_password"] is True
    r = await cliente.get(
        _ruta_de(Permission.AGENDA_VER), cookies={ACCESS_COOKIE: nueva.cookies[ACCESS_COOKIE]}
    )
    assert r.status_code == 403 and _codigo(r) == "PASSWORD_CHANGE_REQUIRED"


async def test_editar_un_empleado_no_revoca_las_sesiones_de_otro(
    cliente, db_session, tenant_a, cookies_a
):
    uno, _ = await _empleado(db_session, tenant_a, ALL_PERMISSIONS)
    otro, _ = await _empleado(db_session, tenant_a, ALL_PERMISSIONS)
    assert (await cliente.delete(f"{STAFF}/{uno.id}", cookies=cookies_a)).status_code == 204
    assert (await cliente.get("/v1/auth/me", cookies=session_cookies(otro))).status_code == 200
    assert (await cliente.get("/v1/auth/me", cookies=cookies_a)).status_code == 200


# ══ 5 · Login por usuario o email; unicidad global sin filtrar el tenant ═════


async def test_login_por_username_y_por_email_sin_importar_mayusculas(
    client, db_session, tenant_a, owner
):
    emp, _ = await _empleado(db_session, tenant_a, {Permission.AGENDA_VER}, username="juan.p")
    emp.email = "juan.p@example.com"
    await db_session.flush()

    for identificador, esperado in (
        ("juan.p", emp),
        ("JUAN.P", emp),
        ("  Juan.P  ", emp),
        ("juan.p@example.com", emp),
        ("Juan.P@Example.COM", emp),
        ("OWNER-A@example.com", owner),
    ):
        client.cookies.clear()
        r = await client.post(
            "/v1/auth/login", json={"identifier": identificador, "password": TEST_PASSWORD}
        )
        assert r.status_code == 200, (identificador, r.text)
        assert r.json()["user"]["id"] == str(esperado.id), identificador


async def test_login_fallido_es_igual_por_usuario_inexistente_o_clave_mala(
    client, db_session, tenant_a
):
    await _empleado(db_session, tenant_a, {Permission.AGENDA_VER}, username="existe.si")
    mala = await client.post(
        "/v1/auth/login", json={"identifier": "existe.si", "password": "incorrecta"}
    )
    nadie = await client.post(
        "/v1/auth/login", json={"identifier": "existe.no", "password": "incorrecta"}
    )
    assert mala.status_code == 401
    _mismo_cuerpo(mala, nadie)


async def test_username_de_un_tenant_no_choca_con_email_de_otro(
    client, db_session, tenant_a, tenant_b
):
    """`username` nunca tiene `@`: 'pedro' (empleado de A) y 'pedro@example.com' (dueño de
    B) son identidades distintas y cada identificador entra a la suya."""
    emp_a, _ = await _empleado(db_session, tenant_a, {Permission.AGENDA_VER}, username="pedro")
    owner_b = await make_user(db_session, tenant_b, Role.OWNER, "pedro@example.com")
    for identificador, user in (("pedro", emp_a), ("pedro@example.com", owner_b)):
        client.cookies.clear()
        r = await client.post(
            "/v1/auth/login", json={"identifier": identificador, "password": TEST_PASSWORD}
        )
        assert r.status_code == 200, r.text
        assert r.json()["user"]["id"] == str(user.id)
        assert r.json()["tenant"]["id"] == str(user.tenant_id)


@pytest.mark.parametrize("en", ["mismo-tenant", "otro-tenant"])
async def test_mismo_username_es_409_sin_decir_de_que_tenant_es(
    client, db_session, tenant_a, tenant_b, cookies_a, en
):
    """`username` es único global (el login no pide tenant): repetirlo en cualquier negocio
    es 409 `USERNAME_TAKEN`, y la respuesta es la misma si el dueño es de este negocio o de
    otro: no revela dónde está."""
    destino = tenant_a if en == "mismo-tenant" else tenant_b
    await _empleado(db_session, destino, {Permission.AGENDA_VER}, username="repetido")
    perfil = await make_profile(db_session, tenant_a, "Lavador", DEFAULT_PROFILES["Lavador"])
    r = await client.post(
        STAFF,
        json={
            "username": "Repetido",  # se normaliza a minúscula: choca igual
            "password": "clave-inicial-123",
            "permission_profile_id": str(perfil.id),
        },
        cookies=cookies_a,
    )
    assert r.status_code == 409, r.text
    assert _codigo(r) == "USERNAME_TAKEN"
    assert str(tenant_b.id) not in r.text and str(tenant_a.id) not in r.text
    assert r.json() == {"detail": {"code": "USERNAME_TAKEN", "message": "Username already in use."}}


async def test_renombrar_a_un_username_ajeno_es_409_y_no_cambia_nada(
    client, db_session, tenant_a, tenant_b, cookies_a
):
    await _empleado(db_session, tenant_b, {Permission.AGENDA_VER}, username="de.b")
    mio, _ = await _empleado(db_session, tenant_a, {Permission.AGENDA_VER}, username="de.a")
    r = await client.patch(f"{STAFF}/{mio.id}", json={"username": "de.b"}, cookies=cookies_a)
    assert r.status_code == 409 and _codigo(r) == "USERNAME_TAKEN"
    await db_session.refresh(mio)
    assert mio.username == "de.a"


async def test_email_de_empleado_igual_al_de_un_duenio_ajeno_es_409(
    client, db_session, tenant_a, cookies_a, owner_b
):
    perfil = await make_profile(db_session, tenant_a, "Lavador", DEFAULT_PROFILES["Lavador"])
    r = await client.post(
        STAFF,
        json={
            "username": "con.mail.ajeno",
            "password": "clave-inicial-123",
            "permission_profile_id": str(perfil.id),
            "email": "OWNER-B@example.com",
        },
        cookies=cookies_a,
    )
    assert r.status_code == 409 and _codigo(r) == "EMAIL_TAKEN"
    assert str(owner_b.tenant_id) not in r.text


async def test_username_con_arroba_o_mal_formado_es_422(client, db_session, tenant_a, cookies_a):
    perfil = await make_profile(db_session, tenant_a, "Lavador", DEFAULT_PROFILES["Lavador"])
    for malo in ("owner-a@example.com", "ab", "con espacio", "x" * 41, "ñandú"):
        r = await client.post(
            STAFF,
            json={
                "username": malo,
                "password": "clave-inicial-123",
                "permission_profile_id": str(perfil.id),
            },
            cookies=cookies_a,
        )
        assert r.status_code == 422, malo


async def test_registro_crea_los_perfiles_por_defecto_solo_en_su_tenant(client, db_session):
    """Y3 / A6: `provision_tenant` crea los 3 perfiles; dos negocios no comparten filas."""
    otro = await make_tenant(db_session, "Lavadero previo")
    await make_profile(db_session, otro, "Encargado", ALL_PERMISSIONS)
    r = await client.post(
        "/v1/auth/register",
        json={"email": "nuevo@example.com", "password": "correct-horse-battery", "tenant": "N"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["permissions"] == [p.value for p in Permission]  # el OWNER: todo
    assert r.json()["must_change_password"] is False
    perfiles = (await client.get(PERFILES)).json()
    assert {i["name"]: set(i["permissions"]) for i in perfiles["items"]} == {
        nombre: {p.value for p in perms} for nombre, perms in DEFAULT_PROFILES.items()
    }
    nuevo_tenant = uuid.UUID(r.json()["tenant"]["id"])
    assert {i["tenant_id"] for i in perfiles["items"]} == {str(nuevo_tenant)}


async def test_borrar_el_email_del_unico_identificador_no_es_un_500(
    client, db_session, tenant_a, cookies_a
):
    """Un STAFF de antes de F4 entra solo con email (la migración 0004 no le inventa usuario).
    Si el dueño le borra el email (`email: null`), se quedaría sin identificador: el CHECK
    `con_identificador` lo impide, y la API tiene que responder un 4xx, no un 500."""
    previo = await make_user(db_session, tenant_a, Role.STAFF, "previo@example.com")
    assert previo.username is None
    r = await client.patch(f"{STAFF}/{previo.id}", json={"email": None}, cookies=cookies_a)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "VALIDATION_ERROR"
    await db_session.refresh(previo)
    assert previo.email == "previo@example.com"
