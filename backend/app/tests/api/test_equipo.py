"""Equipo (PR 4.1): alta del negocio con sus perfiles, CRUD de perfiles y de empleados.

Lo funcional. La autorización (403 de STAFF, compuerta de la clave, revocación de sesiones)
y el aislamiento entre tenants los prueba el Tester-aislamiento en `tests/security/`.
"""

import uuid

from app.domain.permissions import Permission
from app.domain.roles import Role
from app.tests.conftest import make_user

PASSWORD = "correct-horse-battery"
TODOS = [p.value for p in Permission]


async def _registrar(client, email="duenio@example.com"):
    r = await client.post(
        "/v1/auth/register", json={"email": email, "password": PASSWORD, "tenant": "Lavadero"}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_el_registro_crea_los_tres_perfiles_y_el_duenio_tiene_todo(client):
    me = await _registrar(client)
    assert me["permissions"] == TODOS
    assert me["must_change_password"] is False
    assert me["user"]["role"] == "OWNER" and me["user"]["permission_profile_id"] is None

    perfiles = (await client.get("/v1/permission-profiles")).json()
    por_nombre = {p["name"]: p["permissions"] for p in perfiles["items"]}
    assert set(por_nombre) == {"Encargado", "Cajero", "Lavador"}
    assert por_nombre["Encargado"] == TODOS
    # Cajero ⊇ Lavador (D4-10): la misma persona cobra y lava con un solo perfil.
    assert set(por_nombre["Lavador"]) <= set(por_nombre["Cajero"])
    assert "COBROS_ANULAR" not in por_nombre["Cajero"]
    assert "PRECIOS_EDITAR" not in por_nombre["Cajero"]


# ── Perfiles ──────────────────────────────────────────────────────────────────


async def test_crear_perfil_normaliza_orden_y_espacios(client, cookies_a, tenant_a):
    r = await client.post(
        "/v1/permission-profiles",
        json={"name": "  Recepción ", "permissions": ["CAJA_VER", "AGENDA_VER"]},
        cookies=cookies_a,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Recepción"
    assert body["permissions"] == ["AGENDA_VER", "CAJA_VER"]  # orden del enum
    assert body["tenant_id"] == str(tenant_a.id)


async def test_perfil_con_nombre_repetido_es_409_name_taken(client, cookies_a, perfil_a):
    r = await client.post(
        "/v1/permission-profiles",
        json={"name": perfil_a.name, "permissions": []},
        cookies=cookies_a,
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NAME_TAKEN"


async def test_permiso_inexistente_o_repetido_es_422(client, cookies_a):
    for permisos in (["VOLAR"], ["AGENDA_VER", "AGENDA_VER"]):
        r = await client.post(
            "/v1/permission-profiles",
            json={"name": "x", "permissions": permisos},
            cookies=cookies_a,
        )
        assert r.status_code == 422, permisos


async def test_nombre_vacio_o_largo_es_422(client, cookies_a):
    for nombre in ("   ", "x" * 41):
        r = await client.post(
            "/v1/permission-profiles", json={"name": nombre, "permissions": []}, cookies=cookies_a
        )
        assert r.status_code == 422, nombre


async def test_patch_de_perfil_cambia_solo_lo_enviado(client, cookies_a, perfil_a):
    r = await client.patch(
        f"/v1/permission-profiles/{perfil_a.id}",
        json={"permissions": ["REPORTES_VER", "AGENDA_VER"]},
        cookies=cookies_a,
    )
    assert r.status_code == 200
    assert r.json()["name"] == "perfil de A"
    assert r.json()["permissions"] == ["AGENDA_VER", "REPORTES_VER"]


async def test_renombrar_a_un_nombre_usado_es_409(client, cookies_a, perfil_a):
    otro = (
        await client.post(
            "/v1/permission-profiles", json={"name": "Otro", "permissions": []}, cookies=cookies_a
        )
    ).json()
    r = await client.patch(
        f"/v1/permission-profiles/{otro['id']}", json={"name": perfil_a.name}, cookies=cookies_a
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NAME_TAKEN"


async def test_borrar_perfil_en_uso_es_409_y_libre_se_anula(
    client, cookies_a, db_session, tenant_a, perfil_a
):
    await make_user(db_session, tenant_a, Role.STAFF, None, username="lavador1", profile=perfil_a)
    en_uso = await client.delete(f"/v1/permission-profiles/{perfil_a.id}", cookies=cookies_a)
    assert en_uso.status_code == 409 and en_uso.json()["detail"]["code"] == "PROFILE_IN_USE"

    libre = (
        await client.post(
            "/v1/permission-profiles", json={"name": "Libre", "permissions": []}, cookies=cookies_a
        )
    ).json()
    assert (
        await client.delete(f"/v1/permission-profiles/{libre['id']}", cookies=cookies_a)
    ).status_code == 204
    assert (
        await client.get(f"/v1/permission-profiles/{libre['id']}", cookies=cookies_a)
    ).status_code == 404
    # anular libera el nombre (único entre vivos)
    assert (
        await client.post(
            "/v1/permission-profiles", json={"name": "Libre", "permissions": []}, cookies=cookies_a
        )
    ).status_code == 201


async def test_perfil_con_idempotency_key_repetida_es_409(client, cookies_a):
    h = {"Idempotency-Key": "perfil-1"}
    body = {"name": "Uno", "permissions": []}
    assert (
        await client.post("/v1/permission-profiles", json=body, headers=h, cookies=cookies_a)
    ).status_code == 201
    replay = await client.post("/v1/permission-profiles", json=body, headers=h, cookies=cookies_a)
    assert replay.status_code == 409 and replay.json()["detail"]["code"] == "DUPLICATE_IDEMPOTENT"


async def test_la_segunda_pagina_de_perfiles_es_alcanzable(client, cookies_a):
    for i in range(7):
        await client.post(
            "/v1/permission-profiles", json={"name": f"p{i}", "permissions": []}, cookies=cookies_a
        )
    p1 = (await client.get("/v1/permission-profiles?limit=5", cookies=cookies_a)).json()
    p2 = (await client.get("/v1/permission-profiles?limit=5&offset=5", cookies=cookies_a)).json()
    assert p1["total"] == 7 and p1["has_more"] is True and len(p2["items"]) == 2
    assert not {i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]}


# ── Empleados ─────────────────────────────────────────────────────────────────


async def _alta(client, cookies, perfil_id, **extra):
    body = {
        "username": "Juan.Perez",
        "password": "clave-inicial-1",
        "permission_profile_id": str(perfil_id),
        **extra,
    }
    return await client.post("/v1/staff", json=body, cookies=cookies)


async def test_alta_de_empleado_con_usuario_en_minuscula_y_cambio_de_clave(
    client, cookies_a, perfil_a
):
    r = await _alta(client, cookies_a, perfil_a.id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["username"] == "juan.perez"
    assert body["email"] is None and body["role"] == "STAFF"
    assert body["must_change_password"] is True
    assert body["permission_profile_id"] == str(perfil_a.id)
    assert "password_hash" not in r.text and "clave-inicial-1" not in r.text

    login = await client.post(
        "/v1/auth/login", json={"identifier": "JUAN.PEREZ", "password": "clave-inicial-1"}
    )
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True
    assert login.json()["permissions"] == ["AGENDA_VER"]


async def test_username_invalido_es_422(client, cookies_a, perfil_a):
    for username in ("ab", "con espacio", "con@arroba", "x" * 41):
        r = await _alta(client, cookies_a, perfil_a.id, username=username)
        assert r.status_code == 422, username


async def test_clave_inicial_corta_es_422(client, cookies_a, perfil_a):
    assert (await _alta(client, cookies_a, perfil_a.id, password="corta")).status_code == 422


async def test_username_o_email_repetido_es_409(client, cookies_a, perfil_a, owner):
    assert (await _alta(client, cookies_a, perfil_a.id)).status_code == 201
    repetido = await _alta(client, cookies_a, perfil_a.id)
    assert repetido.status_code == 409
    assert repetido.json()["detail"]["code"] == "USERNAME_TAKEN"
    mail = await _alta(client, cookies_a, perfil_a.id, username="otro", email=owner.email)
    assert mail.status_code == 409 and mail.json()["detail"]["code"] == "EMAIL_TAKEN"


async def test_alta_con_perfil_inexistente_es_404(client, cookies_a):
    assert (await _alta(client, cookies_a, uuid.uuid4())).status_code == 404


async def test_listado_de_empleados_no_incluye_al_duenio(client, cookies_a, perfil_a, owner):
    await _alta(client, cookies_a, perfil_a.id)
    listado = (await client.get("/v1/staff", cookies=cookies_a)).json()
    assert [u["username"] for u in listado["items"]] == ["juan.perez"]
    assert listado["total"] == 1
    assert (await client.get(f"/v1/staff/{owner.id}", cookies=cookies_a)).status_code == 404


async def test_patch_de_empleado_cambia_perfil_y_borra_email(
    client, cookies_a, perfil_a, db_session, tenant_a
):
    alta = (await _alta(client, cookies_a, perfil_a.id, email="juan@example.com")).json()
    otro = (
        await client.post(
            "/v1/permission-profiles", json={"name": "Otro", "permissions": []}, cookies=cookies_a
        )
    ).json()
    r = await client.patch(
        f"/v1/staff/{alta['id']}",
        json={"permission_profile_id": otro["id"], "email": None},
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    assert r.json()["permission_profile_id"] == otro["id"]
    assert r.json()["email"] is None
    assert r.json()["username"] == "juan.perez"  # ausente: no se toca


async def test_patch_sin_campos_no_cambia_nada(client, cookies_a, perfil_a):
    alta = (await _alta(client, cookies_a, perfil_a.id, email="juan@example.com")).json()
    r = await client.patch(f"/v1/staff/{alta['id']}", json={}, cookies=cookies_a)
    assert r.status_code == 200
    assert r.json()["email"] == "juan@example.com"


async def test_reset_de_clave_marca_el_cambio_obligatorio(client, cookies_a, perfil_a):
    alta = (await _alta(client, cookies_a, perfil_a.id)).json()
    r = await client.post(
        f"/v1/staff/{alta['id']}/reset-password",
        json={"password": "otra-clave-2"},
        cookies=cookies_a,
    )
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    login = await client.post(
        "/v1/auth/login", json={"identifier": "juan.perez", "password": "otra-clave-2"}
    )
    assert login.status_code == 200


async def test_desactivar_empleado_lo_saca_del_listado_y_no_entra_mas(client, cookies_a, perfil_a):
    alta = (await _alta(client, cookies_a, perfil_a.id)).json()
    assert (await client.delete(f"/v1/staff/{alta['id']}", cookies=cookies_a)).status_code == 204
    assert (await client.get("/v1/staff", cookies=cookies_a)).json()["total"] == 0
    login = await client.post(
        "/v1/auth/login", json={"identifier": "juan.perez", "password": "clave-inicial-1"}
    )
    assert login.status_code == 401


# ── Cambio de clave ───────────────────────────────────────────────────────────


async def test_cambiar_la_clave_baja_el_flag_y_rota_la_sesion(client, cookies_a, perfil_a):
    await _alta(client, cookies_a, perfil_a.id)
    client.cookies.clear()
    await client.post(
        "/v1/auth/login", json={"identifier": "juan.perez", "password": "clave-inicial-1"}
    )
    r = await client.post(
        "/v1/auth/change-password",
        json={"current_password": "clave-inicial-1", "new_password": "mi-clave-propia"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["must_change_password"] is False
    assert (await client.get("/v1/auth/me")).json()["must_change_password"] is False
    nuevo = await client.post(
        "/v1/auth/login", json={"identifier": "juan.perez", "password": "mi-clave-propia"}
    )
    assert nuevo.status_code == 200


async def test_cambiar_la_clave_con_la_actual_mal_es_400(client, cookies_a):
    r = await client.post(
        "/v1/auth/change-password",
        json={"current_password": "no-es", "new_password": "mi-clave-propia"},
        cookies=cookies_a,
    )
    assert r.status_code == 400 and r.json()["detail"]["code"] == "INVALID_CREDENTIALS"


async def test_la_clave_nueva_igual_a_la_actual_es_400(client, cookies_a):
    r = await client.post(
        "/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": PASSWORD},
        cookies=cookies_a,
    )
    assert r.status_code == 400 and r.json()["detail"]["code"] == "VALIDATION_ERROR"


async def test_las_escrituras_de_empleados_tienen_rate_limit_propio(client, cookies_a, perfil_a):
    """El 409 `EMAIL_TAKEN`/`USERNAME_TAKEN` dice si un email o usuario existe en cualquier
    negocio: sin un límite propio, `PATCH /staff/{id}` sería un oráculo a 200/min."""
    from app.api.v1.staff import STAFF_WRITE_LIMIT

    tope = int(STAFF_WRITE_LIMIT.split("/")[0])
    alta = await _alta(client, cookies_a, perfil_a.id)
    assert alta.status_code == 201
    staff_id = alta.json()["id"]
    for i in range(tope):  # cada endpoint tiene su propio contador: el alta no suma
        r = await client.patch(
            f"/v1/staff/{staff_id}", json={"email": f"probe{i}@example.com"}, cookies=cookies_a
        )
        assert r.status_code == 200, r.text
    r = await client.patch(
        f"/v1/staff/{staff_id}", json={"email": "uno-mas@example.com"}, cookies=cookies_a
    )
    assert r.status_code == 429
