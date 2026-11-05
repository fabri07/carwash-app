"""Aislamiento y autorización de la configuración — FASE-4-CONTRATO §4 y §11 (PR 4.2a, SQLite).

Tester-aislamiento del PR 4.2a, que no implementó los endpoints. Cubre `/v1/settings`,
`/v1/vehicle-sizes`, `/v1/resources`, `/v1/business-hours` y `/v1/payment-methods`:

1. **Permiso × endpoint.** OWNER pasa; STAFF con `CATALOGO_EDITAR` escribe el catálogo; STAFF sin
   él lee (200) pero no escribe (403); `PATCH /v1/settings` es solo del OWNER; sin sesión, 401.
2. **Aislamiento.** B no ve, edita ni anula lo de A: el 404 es byte a byte el de un id inexistente,
   el listado de B no trae filas de A y la fila de A queda intacta.
3. **`tenant_id` del cuerpo se ignora** en POST y PATCH.
4. **Validaciones de `PATCH /v1/settings`** (slug reservado/tomado, seña, contacto, textos, nulls).
5. **`Idempotency-Key` repetida** en POST → 409 `DUPLICATE_IDEMPOTENT`.

Datos sintéticos (`@example.com`).
"""

import uuid
from typing import Any

import pytest

from app.domain.permissions import ALL_PERMISSIONS, Permission
from app.domain.roles import Role
from app.tests.conftest import make_profile, make_user, session_cookies

AJENO = "ajeno"


class Recurso:
    def __init__(
        self,
        path: str,
        crear: dict[str, Any],
        crear_otro: dict[str, Any],
        editar: dict[str, Any],
        campo_editado: str,
    ) -> None:
        self.path = path
        self.crear = crear
        self.crear_otro = crear_otro
        self.editar = editar
        self.campo_editado = campo_editado

    def __repr__(self) -> str:
        return self.path


RECURSOS = [
    Recurso(
        "/v1/vehicle-sizes",
        {"code": "SEDAN", "label": "Sedán"},
        {"code": "PICKUP", "label": "Pickup"},
        {"label": "Editado"},
        "label",
    ),
    Recurso(
        "/v1/resources",
        {"name": "Puesto Uno"},
        {"name": "Puesto Dos"},
        {"name": "Editado"},
        "name",
    ),
    Recurso(
        "/v1/business-hours",
        {"weekday": 1, "opens_at": "09:00:00", "closes_at": "18:00:00"},
        {"weekday": 2, "opens_at": "09:00:00", "closes_at": "18:00:00"},
        {"closes_at": "20:00:00"},
        "closes_at",
    ),
    Recurso(
        "/v1/payment-methods",
        {"code": "MERCADOPAGO", "label": "Mercado Pago"},
        {"code": "TARJETA", "label": "Tarjeta"},
        {"label": "Editado"},
        "label",
    ),
]
IDS = [r.path for r in RECURSOS]
SETTINGS = "/v1/settings"


@pytest.fixture
async def staff_editor(db_session, tenant_a):
    perfil = await make_profile(db_session, tenant_a, "Editor", {Permission.CATALOGO_EDITAR})
    user = await make_user(db_session, tenant_a, Role.STAFF, "editor@example.com", profile=perfil)
    return session_cookies(user)


@pytest.fixture
async def staff_sin_catalogo(db_session, tenant_a):
    permisos = ALL_PERMISSIONS - {Permission.CATALOGO_EDITAR}
    perfil = await make_profile(db_session, tenant_a, "Sin catálogo", permisos)
    user = await make_user(db_session, tenant_a, Role.STAFF, "sincat@example.com", profile=perfil)
    return session_cookies(user)


@pytest.fixture
async def staff_todo(staff_cookies):
    """STAFF con TODOS los permisos (incluido CATALOGO_EDITAR): igual no edita el negocio."""
    return staff_cookies


async def _con_otro_medio_de_pago(client, recurso: Recurso, cookies) -> None:
    """El último medio de pago no se anula (409 IN_USE): el ABM completo necesita otro vivo."""
    if recurso.path == "/v1/payment-methods":
        await _crear(client, recurso, cookies, {"code": "OTRO", "label": "Otro"})


async def _crear(client, recurso: Recurso, cookies, body=None) -> dict[str, Any]:
    r = await client.post(recurso.path, json=body or recurso.crear, cookies=cookies)
    assert r.status_code == 201, r.text
    return r.json()


def _sin_marcas(cuerpo: dict[str, Any]) -> dict[str, Any]:
    """SQLite devuelve las fechas sin zona y el POST con `Z`: se comparan sin ellas."""
    return {k: v for k, v in cuerpo.items() if k not in ("created_at", "updated_at")}


def _codigo(r) -> str:
    return r.json()["detail"]["code"]


# ══ 1 · Permiso × endpoint ═══════════════════════════════════════════════════


@pytest.mark.parametrize("recurso", RECURSOS, ids=IDS)
async def test_owner_hace_el_abm_completo(client, cookies_a, recurso):
    await _con_otro_medio_de_pago(client, recurso, cookies_a)
    creado = await _crear(client, recurso, cookies_a)
    url = f"{recurso.path}/{creado['id']}"
    assert (await client.get(url, cookies=cookies_a)).status_code == 200
    r = await client.patch(url, json=recurso.editar, cookies=cookies_a)
    assert r.status_code == 200, r.text
    assert (await client.delete(url, cookies=cookies_a)).status_code == 204
    assert (await client.get(url, cookies=cookies_a)).status_code == 404


@pytest.mark.parametrize("recurso", RECURSOS, ids=IDS)
async def test_staff_con_catalogo_editar_escribe_el_catalogo(client, staff_editor, recurso):
    await _con_otro_medio_de_pago(client, recurso, staff_editor)
    creado = await _crear(client, recurso, staff_editor)
    url = f"{recurso.path}/{creado['id']}"
    assert (await client.patch(url, json=recurso.editar, cookies=staff_editor)).status_code == 200
    assert (await client.delete(url, cookies=staff_editor)).status_code == 204


@pytest.mark.parametrize("recurso", RECURSOS, ids=IDS)
async def test_staff_sin_catalogo_editar_lee_pero_no_escribe(
    client, cookies_a, staff_sin_catalogo, recurso
):
    creado = await _crear(client, recurso, cookies_a)
    url = f"{recurso.path}/{creado['id']}"
    for cookies in (staff_sin_catalogo,):
        assert (await client.get(recurso.path, cookies=cookies)).status_code == 200
        assert (await client.get(url, cookies=cookies)).status_code == 200
        escrituras = [
            await client.post(recurso.path, json=recurso.crear_otro, cookies=cookies),
            await client.patch(url, json=recurso.editar, cookies=cookies),
            await client.delete(url, cookies=cookies),
        ]
        for r in escrituras:
            assert r.status_code == 403, (r.request.method, r.text)
            assert _codigo(r) == "FORBIDDEN"
    # Nada cambió.
    intacto = (await client.get(url, cookies=cookies_a)).json()
    assert _sin_marcas(intacto) == _sin_marcas(creado)


@pytest.mark.parametrize("recurso", RECURSOS, ids=IDS)
async def test_sin_sesion_todo_es_401(client, cookies_a, recurso):
    creado = await _crear(client, recurso, cookies_a)
    url = f"{recurso.path}/{creado['id']}"
    respuestas = [
        await client.get(recurso.path),
        await client.get(url),
        await client.post(recurso.path, json=recurso.crear_otro),
        await client.patch(url, json=recurso.editar),
        await client.delete(url),
    ]
    assert [r.status_code for r in respuestas] == [401] * 5


async def test_settings_get_lo_lee_cualquier_usuario_autenticado(
    client, cookies_a, staff_sin_catalogo, staff_editor
):
    cuerpos = []
    for cookies in (cookies_a, staff_sin_catalogo, staff_editor):
        r = await client.get(SETTINGS, cookies=cookies)
        assert r.status_code == 200, r.text
        cuerpos.append(r.json())
    assert cuerpos[0] == cuerpos[1] == cuerpos[2]


async def test_settings_patch_es_solo_del_owner(client, cookies_a, staff_todo, staff_editor):
    antes = (await client.get(SETTINGS, cookies=cookies_a)).json()
    for cookies in (staff_todo, staff_editor):
        for body in ({"hold_min": 10}, {"slug": "otro-nombre"}, {}):
            r = await client.patch(SETTINGS, json=body, cookies=cookies)
            assert r.status_code == 403, (body, r.text)
            assert _codigo(r) == "FORBIDDEN"
    assert (await client.get(SETTINGS, cookies=cookies_a)).json() == antes
    ok = await client.patch(SETTINGS, json={"hold_min": 10}, cookies=cookies_a)
    assert ok.status_code == 200 and ok.json()["hold_min"] == 10


async def test_settings_sin_sesion_es_401(client):
    assert (await client.get(SETTINGS)).status_code == 401
    assert (await client.patch(SETTINGS, json={"hold_min": 10})).status_code == 401


# ══ 2 · Aislamiento entre tenants ════════════════════════════════════════════


@pytest.mark.parametrize("recurso", RECURSOS, ids=IDS)
async def test_b_no_ve_edita_ni_anula_lo_de_a_y_el_404_es_identico(
    client, cookies_a, cookies_b, recurso
):
    creado = await _crear(client, recurso, cookies_a)
    ajeno = f"{recurso.path}/{creado['id']}"
    inexistente = f"{recurso.path}/{uuid.uuid4()}"

    def normal(r, id_):
        return r.status_code, r.content.replace(str(id_).encode(), b"<id>")

    for metodo, kwargs in (
        ("GET", {}),
        ("PATCH", {"json": recurso.editar}),
        ("DELETE", {}),
    ):
        r_ajeno = await client.request(metodo, ajeno, cookies=cookies_b, **kwargs)
        r_nada = await client.request(metodo, inexistente, cookies=cookies_b, **kwargs)
        assert r_ajeno.status_code == 404, (metodo, r_ajeno.text)
        assert normal(r_ajeno, creado["id"]) == normal(r_nada, inexistente.rsplit("/", 1)[1])
        assert _codigo(r_ajeno) == _codigo(r_nada) == "NOT_FOUND"
        assert creado["id"] not in r_ajeno.text

    # La fila de A sigue intacta y viva.
    assert _sin_marcas((await client.get(ajeno, cookies=cookies_a)).json()) == _sin_marcas(creado)


@pytest.mark.parametrize("recurso", RECURSOS, ids=IDS)
async def test_el_listado_de_b_no_incluye_filas_de_a(client, cookies_a, cookies_b, recurso):
    de_a = await _crear(client, recurso, cookies_a)
    de_b = await _crear(client, recurso, cookies_b, recurso.crear_otro)
    ids_b = {i["id"] for i in (await client.get(recurso.path, cookies=cookies_b)).json()["items"]}
    ids_a = {i["id"] for i in (await client.get(recurso.path, cookies=cookies_a)).json()["items"]}
    assert de_a["id"] in ids_a and de_b["id"] not in ids_a
    assert de_b["id"] in ids_b and de_a["id"] not in ids_b
    total_b = (await client.get(recurso.path, cookies=cookies_b)).json()["total"]
    assert total_b == len(ids_b)


@pytest.mark.parametrize("recurso", RECURSOS, ids=IDS)
async def test_el_mismo_codigo_o_nombre_convive_en_a_y_b(client, cookies_a, cookies_b, recurso):
    """El único es por tenant: lo que A ya tiene no le da a B un 409 (ni le revela que existe)."""
    await _crear(client, recurso, cookies_a)
    await _crear(client, recurso, cookies_b)


async def test_el_patch_de_settings_de_a_no_toca_a_b(client, cookies_a, cookies_b):
    antes_b = (await client.get(SETTINGS, cookies=cookies_b)).json()
    r = await client.patch(
        SETTINGS,
        json={"address": "Calle A 1", "deposit_bps": 4000, "hold_min": 11, "slug": "slug-de-a"},
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    assert (await client.get(SETTINGS, cookies=cookies_b)).json() == antes_b
    propio = (await client.get(SETTINGS, cookies=cookies_a)).json()
    assert (propio["address"], propio["slug"]) == ("Calle A 1", "slug-de-a")


# ══ 3 · `tenant_id` del cuerpo se ignora ═════════════════════════════════════


@pytest.mark.parametrize("recurso", RECURSOS, ids=IDS)
async def test_tenant_id_del_cuerpo_se_ignora(
    client, cookies_a, cookies_b, tenant_a, tenant_b, recurso
):
    r = await client.post(
        recurso.path, json={**recurso.crear, "tenant_id": str(tenant_b.id)}, cookies=cookies_a
    )
    assert r.status_code == 201, r.text
    creado = r.json()
    assert creado["tenant_id"] == str(tenant_a.id)
    # B no la ve; A sí.
    assert (
        await client.get(f"{recurso.path}/{creado['id']}", cookies=cookies_b)
    ).status_code == 404
    assert (
        await client.get(f"{recurso.path}/{creado['id']}", cookies=cookies_a)
    ).status_code == 200

    r = await client.patch(
        f"{recurso.path}/{creado['id']}",
        json={**recurso.editar, "tenant_id": str(tenant_b.id)},
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    assert r.json()["tenant_id"] == str(tenant_a.id)
    assert (
        await client.get(f"{recurso.path}/{creado['id']}", cookies=cookies_b)
    ).status_code == 404
    items_b = (await client.get(recurso.path, cookies=cookies_b)).json()["items"]
    assert items_b == []


async def test_tenant_id_en_patch_settings_se_ignora(client, cookies_a, cookies_b, tenant_b):
    antes_b = (await client.get(SETTINGS, cookies=cookies_b)).json()
    r = await client.patch(
        SETTINGS, json={"hold_min": 13, "tenant_id": str(tenant_b.id)}, cookies=cookies_a
    )
    assert r.status_code == 200, r.text
    assert r.json()["hold_min"] == 13
    assert (await client.get(SETTINGS, cookies=cookies_b)).json() == antes_b


# ══ 4 · Validaciones de PATCH /v1/settings ═══════════════════════════════════


async def test_slug_reservado_es_422_slug_reserved(client, cookies_a):
    antes = (await client.get(SETTINGS, cookies=cookies_a)).json()
    for slug in ("admin", "login", "www", "ADMIN", "health"):
        r = await client.patch(SETTINGS, json={"slug": slug}, cookies=cookies_a)
        assert r.status_code == 422, (slug, r.text)
        assert _codigo(r) == "SLUG_RESERVED"
    assert (await client.get(SETTINGS, cookies=cookies_a)).json()["slug"] == antes["slug"]


async def test_slug_tomado_por_otro_tenant_es_409_slug_taken(client, cookies_a, cookies_b):
    r = await client.patch(SETTINGS, json={"slug": "el-de-b"}, cookies=cookies_b)
    assert r.status_code == 200, r.text
    antes = (await client.get(SETTINGS, cookies=cookies_a)).json()
    r = await client.patch(SETTINGS, json={"slug": "el-de-b", "hold_min": 9}, cookies=cookies_a)
    assert r.status_code == 409, r.text
    assert _codigo(r) == "SLUG_TAKEN"
    # (Que el PATCH no aplique nada a medias lo prueba el test de Postgres: la transacción por
    # request se revierte; la sesión compartida de SQLite no.)
    assert (await client.get(SETTINGS, cookies=cookies_a)).json()["slug"] == antes["slug"]
    assert (await client.get(SETTINGS, cookies=cookies_b)).json()["slug"] == "el-de-b"


async def test_slug_propio_repetido_no_es_conflicto(client, cookies_a):
    slug = (await client.get(SETTINGS, cookies=cookies_a)).json()["slug"]
    r = await client.patch(SETTINGS, json={"slug": slug}, cookies=cookies_a)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("slug", ["ab", "con espacio", "x" * 41, "ñandú", "con_guion_bajo"])
async def test_slug_con_formato_invalido_es_422(client, cookies_a, slug):
    r = await client.patch(SETTINGS, json={"slug": slug}, cookies=cookies_a)
    assert r.status_code == 422, r.text


INVALIDOS = [
    ("deposit_bps", 0),
    ("deposit_bps", 10001),
    ("deposit_bps", -5),
    ("whatsapp_e164", "1144445555"),
    ("whatsapp_e164", "+54 9 351 555 1234"),
    ("whatsapp_e164", "abc"),
    ("instagram_url", "https://facebook.com/lavadero"),
    ("instagram_url", "http://instagram.com/lavadero"),
    ("instagram_url", "https://instagram.com.evil.example/lavadero"),
    ("instagram_url", "https://instagram.com/"),
    ("instagram_url", "lavadero"),
    ("welcome_text", "x" * 281),
    ("tolerance_min", 0),
    ("hold_min", -1),
    ("quote_validity_hours", 0),
    ("palette", "Con Espacio"),
    ("deposit_bps", "mucho"),
]


@pytest.mark.parametrize(("campo", "valor"), INVALIDOS, ids=lambda x: str(x)[:30])
async def test_valores_invalidos_son_422_y_no_cambian_nada(client, cookies_a, campo, valor):
    antes = (await client.get(SETTINGS, cookies=cookies_a)).json()
    r = await client.patch(SETTINGS, json={campo: valor}, cookies=cookies_a)
    assert r.status_code == 422, (campo, valor, r.text)
    assert (await client.get(SETTINGS, cookies=cookies_a)).json() == antes


@pytest.mark.parametrize("valor", [1, 10000])
async def test_deposit_bps_en_los_bordes_es_valido(client, cookies_a, valor):
    r = await client.patch(SETTINGS, json={"deposit_bps": valor}, cookies=cookies_a)
    assert r.status_code == 200 and r.json()["deposit_bps"] == valor


async def test_welcome_text_de_280_es_valido(client, cookies_a):
    r = await client.patch(SETTINGS, json={"welcome_text": "x" * 280}, cookies=cookies_a)
    assert r.status_code == 200, r.text


OBLIGATORIOS = [
    "slug",
    "deposit_enabled",
    "deposit_bps",
    "tolerance_min",
    "late_cancel_threshold_min",
    "hold_min",
    "min_advance_min",
    "quote_validity_hours",
    "palette",
    "font",
]


@pytest.mark.parametrize("campo", OBLIGATORIOS)
async def test_null_en_un_campo_obligatorio_es_422_nunca_500(client, cookies_a, campo):
    antes = (await client.get(SETTINGS, cookies=cookies_a)).json()
    r = await client.patch(SETTINGS, json={campo: None}, cookies=cookies_a)
    assert r.status_code == 422, (campo, r.status_code, r.text)
    assert (await client.get(SETTINGS, cookies=cookies_a)).json() == antes


@pytest.mark.parametrize(
    "campo", ["address", "whatsapp_e164", "instagram_url", "welcome_text", "cancellation_policy"]
)
async def test_null_en_un_opcional_lo_borra(client, cookies_a, campo):
    valores = {
        "address": "Calle 1",
        "whatsapp_e164": "+5493515551234",
        "instagram_url": "https://instagram.com/lavadero",
        "welcome_text": "Hola",
        "cancellation_policy": "Política",
    }
    assert (await client.patch(SETTINGS, json=valores, cookies=cookies_a)).status_code == 200
    r = await client.patch(SETTINGS, json={campo: None}, cookies=cookies_a)
    assert r.status_code == 200, r.text
    assert r.json()[campo] is None


async def test_campos_no_editables_se_ignoran(client, cookies_a, tenant_a):
    antes = (await client.get(SETTINGS, cookies=cookies_a)).json()
    r = await client.patch(
        SETTINGS,
        json={
            "name": "Renombrado",
            "currency": "USD",
            "timezone": "UTC",
            "logo_key": "x",
            "onboarding_completed_at": "2026-01-01T00:00:00Z",
        },
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    assert (await client.get(SETTINGS, cookies=cookies_a)).json() == antes


# ══ 5 · Idempotency-Key ══════════════════════════════════════════════════════


@pytest.mark.parametrize("recurso", RECURSOS, ids=IDS)
async def test_idempotency_key_repetida_es_409_duplicate_idempotent(client, cookies_a, recurso):
    key = {"Idempotency-Key": f"k-{uuid.uuid4()}"}
    r1 = await client.post(recurso.path, json=recurso.crear, cookies=cookies_a, headers=key)
    assert r1.status_code == 201, r1.text
    r2 = await client.post(recurso.path, json=recurso.crear, cookies=cookies_a, headers=key)
    assert r2.status_code == 409, r2.text
    assert _codigo(r2) == "DUPLICATE_IDEMPOTENT"
    assert (await client.get(recurso.path, cookies=cookies_a)).json()["total"] == 1


@pytest.mark.parametrize("recurso", RECURSOS, ids=IDS)
async def test_la_misma_idempotency_key_en_otro_tenant_no_es_replay(
    client, cookies_a, cookies_b, recurso
):
    key = {"Idempotency-Key": f"k-{uuid.uuid4()}"}
    r1 = await client.post(recurso.path, json=recurso.crear, cookies=cookies_a, headers=key)
    r2 = await client.post(recurso.path, json=recurso.crear, cookies=cookies_b, headers=key)
    assert r1.status_code == r2.status_code == 201, (r1.text, r2.text)
