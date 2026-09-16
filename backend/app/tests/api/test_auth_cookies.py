"""ADR-0009 / A9: auth por cookie httpOnly y CSRF por Origin."""

from app.utils.cookies import ACCESS_COOKIE, REFRESH_COOKIE

REGISTER = {"email": "nuevo@example.com", "password": "correct-horse-battery", "tenant": "Acep"}


def _set_cookies(response) -> list[str]:
    return response.headers.get_list("set-cookie")


async def test_el_registro_devuelve_la_cookie_y_no_el_token_en_el_body(client):
    r = await client.post("/v1/auth/register", json=REGISTER)
    assert r.status_code == 201
    cookies = _set_cookies(r)
    assert any(c.startswith(f"{ACCESS_COOKIE}=") for c in cookies)
    assert any(c.startswith(f"{REFRESH_COOKIE}=") for c in cookies)
    for cookie in cookies:
        assert (
            "HttpOnly" in cookie
            and "Secure" in cookie
            and "SameSite=lax" in cookie.replace("SameSite=Lax", "SameSite=lax")
        )
    assert "access_token" not in r.text and "refresh_token" not in r.text
    body = r.json()
    assert body["user"]["email"] == "nuevo@example.com"
    assert body["user"]["role"] == "OWNER"
    assert body["tenant"]["name"] == "Acep"


async def test_el_login_devuelve_la_cookie_y_no_el_token_en_el_body(client, owner):
    r = await client.post(
        "/v1/auth/login", json={"email": owner.email, "password": "correct-horse-battery"}
    )
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "samesite=lax" in cookie.lower()
    assert "access_token" not in r.text and "refresh_token" not in r.text


async def test_sin_cookie_es_401(client):
    r = await client.get("/v1/dummy-resources")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "UNAUTHENTICATED"


async def test_header_authorization_no_autentica(client, cookies_a):
    # El token vive en la cookie; un Bearer no abre nada.
    r = await client.get(
        "/v1/dummy-resources", headers={"Authorization": f"Bearer {cookies_a[ACCESS_COOKIE]}"}
    )
    assert r.status_code == 401


async def test_origin_ajeno_en_un_metodo_mutador_es_403(client, cookies_a):
    r = await client.post(
        "/v1/dummy-resources",
        json={"name": "x"},
        headers={"Origin": "https://evil.example"},
        cookies=cookies_a,
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "ORIGIN_NOT_ALLOWED"


async def test_origin_permitido_pasa(client, cookies_a):
    r = await client.post(
        "/v1/dummy-resources",
        json={"name": "x"},
        headers={"Origin": "https://app.carwash.test"},
        cookies=cookies_a,
    )
    assert r.status_code == 201


async def test_origin_ajeno_en_un_get_no_se_bloquea(client, cookies_a):
    r = await client.get(
        "/v1/dummy-resources", headers={"Origin": "https://evil.example"}, cookies=cookies_a
    )
    assert r.status_code == 200


async def test_en_produccion_la_cookie_siempre_es_secure(settings_env, client, owner):
    settings_env(APP_ENV="production")
    r = await client.post(
        "/v1/auth/login", json={"email": owner.email, "password": "correct-horse-battery"}
    )
    assert all("Secure" in c for c in _set_cookies(r))


async def test_en_local_la_cookie_no_es_secure(settings_env, client, owner):
    # En http://localhost `Secure` impediría setear la cookie.
    settings_env(APP_ENV="local")
    r = await client.post(
        "/v1/auth/login", json={"email": owner.email, "password": "correct-horse-battery"}
    )
    assert all("Secure" not in c for c in _set_cookies(r))
