"""Registro, login, refresh, logout y /me."""

from app.utils.cookies import ACCESS_COOKIE, REFRESH_COOKIE
from app.utils.security import create_access_token, create_refresh_token

PASSWORD = "correct-horse-battery"


async def test_registro_y_me_con_la_cookie_emitida(client):
    r = await client.post(
        "/v1/auth/register",
        json={"email": "Mixto@Example.com", "password": PASSWORD, "tenant": "  Lavadero Uno "},
    )
    assert r.status_code == 201
    me = await client.get("/v1/auth/me")  # el jar de httpx reenvía la cookie
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "mixto@example.com"
    assert me.json()["tenant"]["name"] == "Lavadero Uno"


async def test_registro_con_email_repetido_es_409_email_taken(client, owner):
    r = await client.post(
        "/v1/auth/register", json={"email": owner.email, "password": PASSWORD, "tenant": "Otro"}
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "EMAIL_TAKEN"


async def test_registro_invalido_es_422(client):
    r = await client.post("/v1/auth/register", json={"email": "no-es-email", "password": "x"})
    assert r.status_code == 422


async def test_login_con_password_incorrecta_es_401(client, owner):
    r = await client.post("/v1/auth/login", json={"email": owner.email, "password": "nope"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "INVALID_CREDENTIALS"


async def test_login_con_email_inexistente_da_el_mismo_401(client, owner):
    malo = await client.post("/v1/auth/login", json={"email": owner.email, "password": "nope"})
    nadie = await client.post(
        "/v1/auth/login", json={"email": "nadie@example.com", "password": "nope"}
    )
    assert malo.status_code == nadie.status_code == 401
    assert malo.json() == nadie.json()


async def test_me_devuelve_usuario_y_tenant(client, owner, tenant_a, cookies_a):
    r = await client.get("/v1/auth/me", cookies=cookies_a)
    assert r.status_code == 200
    assert r.json()["user"]["id"] == str(owner.id)
    assert r.json()["tenant"]["id"] == str(tenant_a.id)


async def test_token_invalido_es_401(client):
    r = await client.get("/v1/auth/me", cookies={ACCESS_COOKIE: "basura"})
    assert r.status_code == 401


async def test_refresh_token_no_sirve_como_access_token(client, owner):
    refresh = create_refresh_token(
        {"sub": str(owner.id), "tenant_id": str(owner.tenant_id), "ver": 0}
    )
    r = await client.get("/v1/auth/me", cookies={ACCESS_COOKIE: refresh})
    assert r.status_code == 401


async def test_token_malformado_es_401(client):
    token = create_access_token({"sub": "no-uuid", "tenant_id": "tampoco", "ver": 0})
    r = await client.get("/v1/auth/me", cookies={ACCESS_COOKIE: token})
    assert r.status_code == 401


async def test_refresh_rota_las_cookies(client, owner):
    refresh = create_refresh_token(
        {"sub": str(owner.id), "tenant_id": str(owner.tenant_id), "ver": 0}
    )
    r = await client.post("/v1/auth/refresh", cookies={REFRESH_COOKIE: refresh})
    assert r.status_code == 200
    names = {c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")}
    assert names == {ACCESS_COOKIE, REFRESH_COOKIE}
    assert r.json()["user"]["id"] == str(owner.id)


async def test_refresh_sin_cookie_o_invalido_es_401(client):
    assert (await client.post("/v1/auth/refresh")).status_code == 401
    r = await client.post("/v1/auth/refresh", cookies={REFRESH_COOKIE: "basura"})
    assert r.status_code == 401
    bad = create_refresh_token({"sub": "x", "tenant_id": "y", "ver": 0})
    assert (await client.post("/v1/auth/refresh", cookies={REFRESH_COOKIE: bad})).status_code == 401


async def test_logout_revoca_del_lado_del_servidor(client, owner, cookies_a):
    claims = {"sub": str(owner.id), "tenant_id": str(owner.tenant_id), "ver": 0}
    refresh = create_refresh_token(claims)
    r = await client.post("/v1/auth/logout", cookies={**cookies_a, REFRESH_COOKIE: refresh})
    assert r.status_code == 204
    vencidas = r.headers.get_list("set-cookie")
    assert any(c.startswith(f"{ACCESS_COOKIE}=") and "Max-Age=0" in c for c in vencidas)
    # El access token viejo quedó revocado aunque no haya vencido.
    client.cookies.clear()
    assert (await client.get("/v1/auth/me", cookies=cookies_a)).status_code == 401
    assert (
        await client.post("/v1/auth/refresh", cookies={REFRESH_COOKIE: refresh})
    ).status_code == 401


async def test_logout_sin_sesion_igual_vence_las_cookies(client):
    r = await client.post("/v1/auth/logout")
    assert r.status_code == 204


async def test_login_rate_limit_es_429_con_error_response(client, owner):
    codes = []
    for _ in range(11):
        r = await client.post("/v1/auth/login", json={"email": owner.email, "password": "nope"})
        codes.append(r.status_code)
    assert codes[-1] == 429
    assert r.json()["detail"]["code"] == "RATE_LIMITED"
