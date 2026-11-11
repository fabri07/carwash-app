"""Personalización (PR 4.5): catálogo, paleta y tipografía, logo y paleta sugerida.

A11 (logo: no-imagen, > 2 MB, < 200×200, sugerencia, 503 sin R2), A3/A4 (solo el OWNER escribe;
el empleado, aun con todos los permisos, recibe 403) y A2 donde aplica (el logo se guarda bajo el
tenant del token aunque el formulario traiga otro `tenant_id`). R2 nunca se toca: la dependencia
`get_storage` se reemplaza por `InMemoryStorage` (o por `None` para el 503). Datos sintéticos.
"""

import io
import uuid

import pytest
from PIL import Image

from app.application.services.branding_service import MAX_LOGO_BYTES
from app.domain.branding import DEFAULT_FONT, DEFAULT_PALETTE, FONTS, PALETTES
from app.infrastructure.storage import InMemoryStorage, StorageError, get_storage

PUBLIC = "https://logos.example.com"


def png(size=(300, 300), color=(210, 20, 30), fmt="PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    return buf.getvalue()


def archivo(data: bytes, nombre="logo.png", tipo="image/png"):
    return {"file": (nombre, data, tipo)}


def _usar(client, storage) -> None:
    client._transport.app.dependency_overrides[get_storage] = lambda: storage


@pytest.fixture
def storage(client) -> InMemoryStorage:
    mem = InMemoryStorage()
    _usar(client, mem)
    return mem


@pytest.fixture
def con_url_publica(settings_env):
    settings_env(R2_PUBLIC_BASE_URL=PUBLIC + "/")


def _codigo(r) -> str:
    return r.json()["detail"]["code"]


# ── Catálogo y PATCH de paleta/tipografía ─────────────────────────────────────


async def test_el_catalogo_lo_lee_cualquier_usuario(client, cookies_a, staff_cookies):
    for cookies in (cookies_a, staff_cookies):
        r = await client.get("/v1/branding/catalog", cookies=cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        assert [p["code"] for p in body["palettes"]] == list(PALETTES)
        assert [f["code"] for f in body["fonts"]] == list(FONTS)
        assert body["default_palette"] == DEFAULT_PALETTE
        assert body["default_font"] == DEFAULT_FONT
        bosque = body["palettes"][1]
        assert bosque["primary"]["hex"].startswith("#")
        assert bosque["primary"]["oklch"].startswith("oklch(")
    assert (await client.get("/v1/branding/catalog")).status_code == 401


async def test_settings_nacen_con_default_y_sin_logo(client, cookies_a):
    s = (await client.get("/v1/settings", cookies=cookies_a)).json()
    assert (s["palette"], s["font"], s["logo_key"], s["logo_url"]) == (
        DEFAULT_PALETTE,
        DEFAULT_FONT,
        None,
        None,
    )


async def test_el_dueño_elige_paleta_y_tipografia(client, cookies_a):
    r = await client.patch(
        "/v1/settings", json={"palette": "Bosque", "font": "inter"}, cookies=cookies_a
    )
    assert r.status_code == 200, r.text
    assert (r.json()["palette"], r.json()["font"]) == ("bosque", "inter")


@pytest.mark.parametrize(
    "body",
    [{"palette": "rosa"}, {"font": "comic_sans"}, {"palette": None}, {"font": ""}],
)
async def test_paleta_o_tipografia_fuera_del_catalogo_es_422(client, cookies_a, body):
    r = await client.patch("/v1/settings", json=body, cookies=cookies_a)
    assert r.status_code == 422, r.text
    assert (await client.get("/v1/settings", cookies=cookies_a)).json()["palette"] == "default"


async def test_el_empleado_no_cambia_la_paleta(client, cookies_a, staff_cookies):
    r = await client.patch("/v1/settings", json={"palette": "bosque"}, cookies=staff_cookies)
    assert r.status_code == 403
    assert (await client.get("/v1/settings", cookies=cookies_a)).json()["palette"] == "default"


async def test_logo_key_no_se_mueve_por_patch(client, cookies_a):
    r = await client.patch("/v1/settings", json={"logo_key": "logos/otro/x.png"}, cookies=cookies_a)
    assert r.status_code == 200 and r.json()["logo_key"] is None


# ── Subir el logo (A11) ───────────────────────────────────────────────────────


async def test_sube_el_logo_y_sugiere_paleta(client, cookies_a, owner, storage, con_url_publica):
    data = png()
    r = await client.post("/v1/branding/logo", files=archivo(data), cookies=cookies_a)
    assert r.status_code == 200, r.text
    body = r.json()
    key = body["logo_key"]
    assert key.startswith(f"logos/{owner.tenant_id}/") and key.endswith(".png")
    assert body["logo_url"] == f"{PUBLIC}/{key}"
    assert body["suggestion"]["palette"] == "competicion"
    assert body["suggestion"]["dominant_color"].startswith("#")
    guardado = storage.objects[key]
    assert guardado.data == data and guardado.content_type == "image/png"
    assert "immutable" in guardado.cache_control
    s = (await client.get("/v1/settings", cookies=cookies_a)).json()
    assert (s["logo_key"], s["logo_url"]) == (key, f"{PUBLIC}/{key}")


async def test_la_extension_la_decide_el_contenido(client, cookies_a, storage):
    """Un JPEG que dice llamarse `.png` y ser `image/png` se guarda como `.jpg`/`image/jpeg`."""
    r = await client.post("/v1/branding/logo", files=archivo(png(fmt="JPEG")), cookies=cookies_a)
    assert r.status_code == 200, r.text
    key = r.json()["logo_key"]
    assert key.endswith(".jpg") and storage.objects[key].content_type == "image/jpeg"


async def test_sin_url_publica_el_logo_se_guarda_pero_no_hay_url(client, cookies_a, storage):
    r = await client.post("/v1/branding/logo", files=archivo(png()), cookies=cookies_a)
    assert r.status_code == 200 and r.json()["logo_url"] is None


async def test_reemplazar_borra_el_anterior_y_repetir_no(client, cookies_a, storage):
    primero = (
        await client.post("/v1/branding/logo", files=archivo(png()), cookies=cookies_a)
    ).json()["logo_key"]
    # Subir la misma imagen da la misma clave: no se borra a sí misma.
    again = await client.post("/v1/branding/logo", files=archivo(png()), cookies=cookies_a)
    assert again.json()["logo_key"] == primero and primero in storage.objects
    segundo = (
        await client.post(
            "/v1/branding/logo", files=archivo(png(color=(20, 120, 40))), cookies=cookies_a
        )
    ).json()["logo_key"]
    assert segundo != primero
    assert set(storage.objects) == {segundo}


async def test_quitar_el_logo(client, cookies_a, storage):
    key = (await client.post("/v1/branding/logo", files=archivo(png()), cookies=cookies_a)).json()[
        "logo_key"
    ]
    assert (await client.delete("/v1/branding/logo", cookies=cookies_a)).status_code == 204
    s = (await client.get("/v1/settings", cookies=cookies_a)).json()
    assert s["logo_key"] is None and s["logo_url"] is None
    assert key not in storage.objects
    # Sin logo, quitarlo de nuevo no falla.
    assert (await client.delete("/v1/branding/logo", cookies=cookies_a)).status_code == 204


async def test_quitar_el_logo_funciona_sin_r2(client, cookies_a, storage):
    await client.post("/v1/branding/logo", files=archivo(png()), cookies=cookies_a)
    _usar(client, None)
    assert (await client.delete("/v1/branding/logo", cookies=cookies_a)).status_code == 204
    assert (await client.get("/v1/settings", cookies=cookies_a)).json()["logo_key"] is None


async def test_sin_credenciales_r2_el_logo_es_503(client, cookies_a):
    _usar(client, None)
    r = await client.post("/v1/branding/logo", files=archivo(png()), cookies=cookies_a)
    assert r.status_code == 503 and _codigo(r) == "STORAGE_UNAVAILABLE"
    # y el resto de la app sigue
    assert (await client.get("/v1/settings", cookies=cookies_a)).status_code == 200
    assert (
        await client.post("/v1/branding/suggest-palette", files=archivo(png()), cookies=cookies_a)
    ).status_code == 200


async def test_sin_variables_r2_la_dependencia_real_da_503(client, cookies_a, settings_env):
    """Sin override: `get_storage` lee la configuración (vacía en tests) y devuelve `None`."""
    settings_env(R2_ACCOUNT_ID="", R2_BUCKET="")
    r = await client.post("/v1/branding/logo", files=archivo(png()), cookies=cookies_a)
    assert r.status_code == 503


async def test_si_r2_falla_es_503_y_no_cambia_nada(client, cookies_a):
    class Rota(InMemoryStorage):
        async def put(self, key, data, content_type):
            raise StorageError("boom")

    _usar(client, Rota())
    r = await client.post("/v1/branding/logo", files=archivo(png()), cookies=cookies_a)
    assert r.status_code == 503 and _codigo(r) == "STORAGE_UNAVAILABLE"
    assert (await client.get("/v1/settings", cookies=cookies_a)).json()["logo_key"] is None


async def test_si_falla_borrar_el_anterior_igual_responde(client, cookies_a):
    class NoBorra(InMemoryStorage):
        async def delete(self, key):
            raise StorageError("boom")

    mem = NoBorra()
    _usar(client, mem)
    await client.post("/v1/branding/logo", files=archivo(png()), cookies=cookies_a)
    r = await client.post(
        "/v1/branding/logo", files=archivo(png(color=(0, 40, 100))), cookies=cookies_a
    )
    assert r.status_code == 200 and len(mem.objects) == 2  # el viejo queda huérfano


@pytest.mark.parametrize("ruta", ["/v1/branding/logo", "/v1/branding/suggest-palette"])
@pytest.mark.parametrize(
    ("data", "status", "codigo"),
    [
        (b"no soy una imagen", 422, "INVALID_IMAGE"),
        (b'<svg xmlns="http://www.w3.org/2000/svg" width="900"/>', 422, "INVALID_IMAGE"),
        (png(size=(199, 300)), 422, "IMAGE_TOO_SMALL"),
        (b"\x89PNG" + b"0" * MAX_LOGO_BYTES, 413, "FILE_TOO_LARGE"),
    ],
    ids=["texto", "svg", "chica", "pesada"],
)
async def test_rechaza_lo_que_no_sirve(client, cookies_a, storage, ruta, data, status, codigo):
    r = await client.post(ruta, files=archivo(data), cookies=cookies_a)
    assert r.status_code == status, r.text
    assert _codigo(r) == codigo
    assert storage.objects == {}
    assert (await client.get("/v1/settings", cookies=cookies_a)).json()["logo_key"] is None


async def test_sin_archivo_es_422(client, cookies_a, storage):
    r = await client.post("/v1/branding/logo", cookies=cookies_a)
    assert r.status_code == 422


async def test_sugerir_no_guarda_nada(client, cookies_a, storage):
    r = await client.post(
        "/v1/branding/suggest-palette",
        files=archivo(png(color=(20, 120, 40))),
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    assert r.json()["palette"] == "bosque"
    assert storage.objects == {}
    assert (await client.get("/v1/settings", cookies=cookies_a)).json()["logo_key"] is None


async def test_logo_transparente_sugiere_la_paleta_por_defecto(client, cookies_a):
    buf = io.BytesIO()
    Image.new("RGBA", (300, 300), (0, 0, 0, 0)).save(buf, "PNG")
    r = await client.post(
        "/v1/branding/suggest-palette", files=archivo(buf.getvalue()), cookies=cookies_a
    )
    assert r.json() == {"palette": DEFAULT_PALETTE, "dominant_color": None}


# ── Autorización (A3/A4) y aislamiento (A2) ───────────────────────────────────


async def test_el_empleado_no_toca_el_logo(client, cookies_a, staff_cookies, storage):
    """`staff_cookies` tiene TODOS los permisos: la marca es solo del dueño (Y5)."""
    for r in (
        await client.post("/v1/branding/logo", files=archivo(png()), cookies=staff_cookies),
        await client.post(
            "/v1/branding/suggest-palette", files=archivo(png()), cookies=staff_cookies
        ),
        await client.delete("/v1/branding/logo", cookies=staff_cookies),
    ):
        assert r.status_code == 403, r.text
        assert _codigo(r) == "FORBIDDEN"
    assert storage.objects == {}


async def test_sin_sesion_es_401(client, storage):
    for r in (
        await client.post("/v1/branding/logo", files=archivo(png())),
        await client.post("/v1/branding/suggest-palette", files=archivo(png())),
        await client.delete("/v1/branding/logo"),
    ):
        assert r.status_code == 401


async def test_el_logo_va_al_tenant_del_token(
    client, cookies_a, cookies_b, owner, owner_b, storage
):
    """Un `tenant_id` en el formulario se ignora; B no ve ni pisa el logo de A."""
    r = await client.post(
        "/v1/branding/logo",
        files=archivo(png()),
        data={"tenant_id": str(owner_b.tenant_id)},
        cookies=cookies_a,
    )
    key = r.json()["logo_key"]
    assert key.startswith(f"logos/{owner.tenant_id}/")
    assert (await client.get("/v1/settings", cookies=cookies_b)).json()["logo_key"] is None
    # B sube la misma imagen: otra clave (otro prefijo) y el de A sigue.
    kb = (await client.post("/v1/branding/logo", files=archivo(png()), cookies=cookies_b)).json()[
        "logo_key"
    ]
    assert kb != key and kb.startswith(f"logos/{owner_b.tenant_id}/")
    assert (await client.delete("/v1/branding/logo", cookies=cookies_b)).status_code == 204
    assert key in storage.objects
    assert (await client.get("/v1/settings", cookies=cookies_a)).json()["logo_key"] == key
    assert uuid.UUID(key.split("/")[1]) == owner.tenant_id


# ── Tope del cuerpo antes de autenticar (hallazgo 1 del /code-review) ─────────


_CABECERA_MULTIPART = (
    b'--x\r\nContent-Disposition: form-data; name="file"; filename="logo.png"\r\n'
    b"Content-Type: image/png\r\n\r\n"
)


def _cuerpo_en_trozos(total: int, leidos: list[int]):
    """Un multipart válido (una parte de archivo interminable), mandado de a 64 KiB."""

    async def gen():
        leidos.append(0)
        yield _CABECERA_MULTIPART
        trozo = b"x" * 65536
        enviados = 0
        while enviados < total:
            leidos.append(len(trozo))
            enviados += len(trozo)
            yield trozo

    return gen()


@pytest.mark.parametrize("ruta", ["/v1/branding/logo", "/v1/branding/suggest-palette"])
async def test_un_anonimo_con_content_length_enorme_recibe_413_sin_leer(client, ruta):
    leidos: list[int] = []
    r = await client.post(
        ruta,
        content=_cuerpo_en_trozos(50 * 1024 * 1024, leidos),
        headers={
            "Content-Type": "multipart/form-data; boundary=x",
            "Content-Length": str(50 * 1024 * 1024),
        },
    )
    assert r.status_code == 413 and _codigo(r) == "FILE_TOO_LARGE"
    assert sum(leidos) == 0  # ni un trozo del archivo


async def test_un_cuerpo_chunked_sin_content_length_se_corta_al_pasar_el_tope(client):
    leidos: list[int] = []
    r = await client.post(
        "/v1/branding/logo",
        content=_cuerpo_en_trozos(50 * 1024 * 1024, leidos),
        headers={"Content-Type": "multipart/form-data; boundary=x"},
    )
    assert r.status_code == 413 and _codigo(r) == "FILE_TOO_LARGE"
    assert sum(leidos) < 3 * 1024 * 1024  # cortó apenas pasó ~2,5 MB


async def test_content_length_invalido_es_413(client):
    r = await client.post("/v1/branding/logo", content=b"x", headers={"Content-Length": "abc"})
    assert r.status_code == 413


async def test_el_tope_no_toca_otras_rutas(client, cookies_a):
    r = await client.patch("/v1/settings", json={"welcome_text": "Hola"}, cookies=cookies_a)
    assert r.status_code == 200


async def test_un_logo_valido_pasa_el_tope(client, cookies_a, storage):
    r = await client.post(
        "/v1/branding/logo", files=archivo(png(size=(1200, 1200))), cookies=cookies_a
    )
    assert r.status_code == 200, r.text


# ── Borrado del logo anterior sin pisar el vigente (hallazgo 4) ───────────────


async def _subir(client, cookies, data) -> str:
    r = await client.post("/v1/branding/logo", files=archivo(data), cookies=cookies)
    assert r.status_code == 200, r.text
    return r.json()["logo_key"]


async def test_carrera_a_b_a_no_borra_el_logo_vigente(client, cookies_a, storage, monkeypatch):
    """A → B → A con el borrado de A (pedido por la subida de B) corriendo tarde, después de que
    A volvió a ser el logo: no tiene que borrarlo. B sí se borra."""
    from app.api.v1 import branding as branding_api

    real = branding_api.discard_unused_logo
    pendientes: list[tuple] = []

    async def diferir(*args):
        pendientes.append(args)

    monkeypatch.setattr(branding_api, "discard_unused_logo", diferir)
    a, b = png(color=(210, 20, 30)), png(color=(20, 120, 40))
    ka = await _subir(client, cookies_a, a)
    kb = await _subir(client, cookies_a, b)
    assert await _subir(client, cookies_a, a) == ka
    assert [p[3] for p in pendientes] == [ka, kb]  # borrar A (tras B) y borrar B (tras A)

    for args in pendientes:
        await real(*args)
    assert ka in storage.objects and kb not in storage.objects
    assert (await client.get("/v1/settings", cookies=cookies_a)).json()["logo_key"] == ka


async def test_si_r2_falla_vuelve_al_logo_anterior(client, cookies_a, storage):
    ka = await _subir(client, cookies_a, png())

    class Rota(InMemoryStorage):
        async def put(self, key, data, content_type):
            raise StorageError("boom")

    _usar(client, Rota())
    r = await client.post(
        "/v1/branding/logo", files=archivo(png(color=(0, 40, 100))), cookies=cookies_a
    )
    assert r.status_code == 503
    assert (await client.get("/v1/settings", cookies=cookies_a)).json()["logo_key"] == ka


async def test_un_error_inesperado_al_borrar_queda_en_el_log(monkeypatch):
    from app.api.v1 import branding as branding_api

    avisos: list[str] = []
    monkeypatch.setattr(branding_api.logger, "warning", lambda evento, **_: avisos.append(evento))

    def sin_base():
        raise RuntimeError("sin base")

    await branding_api.discard_unused_logo(sin_base, InMemoryStorage(), uuid.uuid4(), "k")
    assert avisos == ["branding.discard_failed"]
