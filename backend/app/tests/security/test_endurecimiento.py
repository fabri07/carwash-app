"""Arreglos de seguridad de T3 (H2, H3, H4, M1, M2, M6, M7, L1, L8, cookie prefix)."""

import logging
import uuid

import pytest
from jose import jwt
from pydantic import ValidationError

from app import bootstrap
from app.api.rate_limit import build_limiter, storage_uri_for
from app.config.settings import DEFAULT_JWT_SECRET, Settings, get_settings
from app.observability import sentry
from app.utils import security
from app.utils.cookies import ACCESS_COOKIE

SECRETO = "s" * 40
PASSWORD = "correct-horse-battery"


# ── H3 · Sentry sin variables locales ─────────────────────────────────────────


def test_sentry_no_manda_variables_locales():
    opciones = sentry.sdk_options("web")
    assert opciones["include_local_variables"] is False
    assert opciones["send_default_pii"] is False
    assert opciones["before_send"] is sentry._scrub_event


def test_scrub_limpia_el_message_de_los_breadcrumbs():
    event = {
        "breadcrumbs": {
            "values": [
                {"message": "login password=hunter2"},
                {"message": "patente AB123CD en texto"},
                {"message": "http.request"},
            ]
        }
    }
    valores = sentry._scrub_event(event, {})["breadcrumbs"]["values"]
    assert [c["message"] for c in valores] == ["[Filtered]", "[Filtered]", "http.request"]


# ── L1 / H3 · NUL y 72 bytes → 422, nunca 500 ─────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        {"email": "a\x00b@example.com", "password": PASSWORD},
        {"email": "ok@example.com", "password": "abc\x00defgh"},
    ],
    ids=["nul-en-email", "nul-en-password"],
)
async def test_nul_en_credenciales_es_422(client, body):
    assert (await client.post("/v1/auth/login", json=body)).status_code == 422
    registro = {**body, "tenant": "T"}
    assert (await client.post("/v1/auth/register", json=registro)).status_code == 422


async def test_password_de_mas_de_72_bytes_es_422(client):
    # 36 caracteres de 2 bytes = 72 bytes: entra. 37 = 74 bytes: no.
    ok = {"email": "ok72@example.com", "password": "ñ" * 36, "tenant": "T"}
    assert (await client.post("/v1/auth/register", json=ok)).status_code == 201
    largo = {"email": "largo@example.com", "password": "ñ" * 37, "tenant": "T"}
    assert (await client.post("/v1/auth/register", json=largo)).status_code == 422
    assert (
        await client.post("/v1/auth/login", json={"email": "x@example.com", "password": "a" * 73})
    ).status_code == 422


# ── H2 · el log de validación no trae lo que mandó el cliente ─────────────────


async def test_password_en_el_campo_email_no_aparece_en_el_log(client, caplog):
    secreto = "MiClaveSecreta-987"
    with caplog.at_level(logging.WARNING):
        r = await client.post("/v1/auth/login", json={"email": secreto, "password": "x"})
    assert r.status_code == 422
    assert "request.body_validation_error" in caplog.text
    assert secreto not in caplog.text
    # tampoco se refleja en la respuesta
    assert secreto not in r.text
    assert set(r.json()["detail"][0]) == {"loc", "msg", "type"}


# ── H4 · el arranque aborta con un rol que saltea RLS ─────────────────────────


class _Fila:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _ConnFalsa:
    def __init__(self, fila, dialecto="postgresql"):
        self._fila = fila
        self.dialect = type("D", (), {"name": dialecto})()

    async def execute(self, _stmt):
        fila = self._fila
        return type("R", (), {"one": lambda self: fila})()


def _fila(**cambios):
    base = {"role": "carwash_app", "rolsuper": False, "rolbypassrls": False, "owns_users": False}
    return _Fila(**{**base, **cambios})


@pytest.mark.parametrize(
    "cambio,motivo",
    [
        ({"rolsuper": True}, "superusuario"),
        ({"rolbypassrls": True}, "BYPASSRLS"),
        ({"owns_users": True}, "dueño"),
    ],
    ids=["superuser", "bypassrls", "duenio"],
)
@pytest.mark.parametrize("app_env", ["staging", "production"])
async def test_rol_inseguro_aborta_en_staging_y_prod(cambio, motivo, app_env):
    with pytest.raises(bootstrap.UnsafeDatabaseRoleError, match=motivo):
        await bootstrap.enforce_safe_runtime_role(_ConnFalsa(_fila(**cambio)), app_env)


async def test_rol_seguro_o_ambiente_local_no_aborta():
    await bootstrap.enforce_safe_runtime_role(_ConnFalsa(_fila()), "production")
    await bootstrap.enforce_safe_runtime_role(_ConnFalsa(_fila(rolsuper=True)), "local")
    await bootstrap.enforce_safe_runtime_role(_ConnFalsa(_fila(rolsuper=True)), "test")
    sqlite = _ConnFalsa(_fila(rolsuper=True), dialecto="sqlite")
    await bootstrap.enforce_safe_runtime_role(sqlite, "production")


async def test_startup_propaga_el_rol_inseguro(monkeypatch):
    async def inseguro():
        raise bootstrap.UnsafeDatabaseRoleError("x")

    monkeypatch.setattr(bootstrap, "_init_database", inseguro)
    with pytest.raises(bootstrap.UnsafeDatabaseRoleError):
        await bootstrap.startup()


# ── M1 · secreto JWT fuera de local ───────────────────────────────────────────


@pytest.mark.parametrize("app_env", ["test", "staging", "production"])
def test_secreto_default_o_corto_fuera_de_local_no_arranca(app_env):
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(APP_ENV=app_env, JWT_SECRET_KEY=DEFAULT_JWT_SECRET)
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(APP_ENV=app_env, JWT_SECRET_KEY="corto")
    assert Settings(APP_ENV=app_env, JWT_SECRET_KEY=SECRETO).JWT_SECRET_KEY == SECRETO


def test_en_local_el_default_se_acepta():
    s = Settings(APP_ENV="local", JWT_SECRET_KEY=DEFAULT_JWT_SECRET)
    assert s.JWT_SECRET_KEY == DEFAULT_JWT_SECRET


# ── M2 · iss/aud por ambiente ─────────────────────────────────────────────────


def test_el_token_lleva_iss_y_aud_del_ambiente():
    token = security.create_access_token({"sub": "x"})
    claims = jwt.get_unverified_claims(token)
    assert claims["iss"] == "carwash-api:test" and claims["aud"] == "carwash-app:test"
    assert security.decode_access_token(token) is not None


def test_token_de_otro_ambiente_con_el_mismo_secreto_no_valida(settings_env):
    token_staging_like = jwt.encode(
        {"sub": "x", "type": "access", "iss": "carwash-api:staging", "aud": "carwash-app:staging"},
        get_settings().JWT_SECRET_KEY,
        algorithm="HS256",
    )
    assert security.decode_access_token(token_staging_like) is None
    sin_iss = jwt.encode(
        {"sub": "x", "type": "access", "aud": "carwash-app:test"},
        get_settings().JWT_SECRET_KEY,
        algorithm="HS256",
    )
    assert security.decode_access_token(sin_iss) is None


async def test_los_401_no_distinguen_token_invalido_de_usuario_inexistente(client, tenant_a):
    firmado_sin_usuario = security.create_access_token(
        {"sub": str(uuid.uuid4()), "tenant_id": str(tenant_a.id), "ver": 0}
    )
    malformado = security.create_access_token({"sub": "x", "tenant_id": "y", "ver": 0})
    respuestas = [
        await client.get("/v1/auth/me", cookies={ACCESS_COOKIE: token})
        for token in ("basura", firmado_sin_usuario, malformado)
    ]
    assert {r.status_code for r in respuestas} == {401}
    assert len({r.text for r in respuestas}) == 1


# ── M6 · bcrypt fuera del loop y limiter compartido ───────────────────────────


async def test_hash_y_verify_async_corren_en_threadpool(monkeypatch):
    usados = []

    async def fake_threadpool(fn, *args):
        usados.append(fn.__name__)
        return fn(*args)

    monkeypatch.setattr(security, "run_in_threadpool", fake_threadpool)
    hashed = await security.hash_password_async("clave-larga-1")
    assert await security.verify_password_async("clave-larga-1", hashed)
    assert usados == ["hash_password", "verify_password"]


@pytest.mark.parametrize(
    "app_env,esperado",
    [
        ("local", "memory://"),
        ("test", "memory://"),
        ("staging", "redis://r:6379/0"),
        ("production", "redis://r:6379/0"),
    ],
)
def test_limiter_usa_redis_fuera_de_local_y_test(app_env, esperado):
    s = Settings(APP_ENV=app_env, JWT_SECRET_KEY=SECRETO, REDIS_URL="redis://r:6379/0")
    assert storage_uri_for(s) == esperado
    assert build_limiter(s) is not None


# ── M7 ────────────────────────────────────────────────────────────────────────


def test_refresh_dura_7_dias():
    assert Settings.model_fields["JWT_REFRESH_TOKEN_EXPIRE_DAYS"].default == 7


# ── L8 · ids de correlación ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "valor",
    ["a" * 65, "con espacio", "inyección\nX-Evil: 1", "<script>", ""],
    ids=["largo", "espacio", "salto", "html", "vacio"],
)
async def test_ids_de_correlacion_invalidos_se_reemplazan(client, valor):
    headers = {"X-Request-ID": valor, "X-Trace-Id": valor}
    try:
        r = await client.get("/health", headers=headers)
    except Exception:  # httpx rechaza algunos valores antes de mandarlos
        return
    assert r.headers["x-request-id"] != valor and r.headers["x-trace-id"] != valor
    uuid.UUID(r.headers["x-request-id"])


async def test_ids_de_correlacion_validos_se_respetan(client):
    r = await client.get("/health", headers={"X-Request-ID": "req.1_A-b", "X-Trace-Id": "tr-9"})
    assert r.headers["x-request-id"] == "req.1_A-b" and r.headers["x-trace-id"] == "tr-9"


# ── COOKIE_NAME_PREFIX (contrato con frontend y deploy) ───────────────────────


async def test_prefijo_de_cookies(settings_env, client, owner):
    settings_env(COOKIE_NAME_PREFIX="stg_")
    r = await client.post("/v1/auth/login", json={"email": owner.email, "password": PASSWORD})
    nombres = {c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")}
    assert nombres == {"stg_access_token", "stg_refresh_token"}
    me = await client.get(
        "/v1/auth/me", cookies={"stg_access_token": r.cookies["stg_access_token"]}
    )
    assert me.status_code == 200
    # sin prefijo no autentica
    client.cookies.clear()
    sin = await client.get("/v1/auth/me", cookies={ACCESS_COOKIE: r.cookies["stg_access_token"]})
    assert sin.status_code == 401
