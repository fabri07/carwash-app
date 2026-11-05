"""Aislamiento de `tenant_settings` por RLS y por `SettingsService` — FASE-4-CONTRATO §4 (PR 4.2a).

Tester-aislamiento del PR 4.2a. Todo con el rol de runtime `carwash_app` (RLS activo); el
superusuario solo siembra y es el testigo de "no cambió nada".

1. RLS directo sobre `tenant_settings`: sin contexto 0 filas; con A no se ve ni se actualiza ni
   se borra la de B; no se inserta una fila para B (WITH CHECK) ni una segunda para A (UNIQUE).
2. `SettingsService` de A no lee ni escribe la configuración de B, en los dos sentidos, y el
   slug de A no pisa al de B.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.services.settings_service import SettingsService, SlugTakenError
from app.persistence.db.tenant_context import set_tenant_context

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]


async def _filas(admin: AsyncEngine, tenant_id: uuid.UUID) -> list[str]:
    async with admin.connect() as conn:
        rows = await conn.execute(
            text("SELECT row_to_json(t)::text FROM tenant_settings t WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
        return [r[0] for r in rows]


async def _slug(admin: AsyncEngine, tenant_id: uuid.UUID) -> str:
    async with admin.connect() as conn:
        return str(
            await conn.scalar(text("SELECT slug FROM tenants WHERE id = :t"), {"t": tenant_id})
        )


@pytest.fixture
async def dos(pg_admin_engine, pg_tenant_a, pg_tenant_b, pg_user_factory) -> dict[str, Any]:
    owner_a = await pg_user_factory(pg_tenant_a, "owner-a@ejemplo.invalid")
    owner_b = await pg_user_factory(pg_tenant_b, "owner-b@ejemplo.invalid")
    return {"a": pg_tenant_a, "b": pg_tenant_b, "owner_a": owner_a, "owner_b": owner_b}


async def test_sin_contexto_no_se_ve_ninguna_configuracion(pg_session_factory, dos):
    async with pg_session_factory() as s, s.begin():
        assert await s.scalar(text("SELECT count(*) FROM tenant_settings")) == 0


async def test_con_el_contexto_de_a_solo_se_ve_la_de_a(pg_session_factory, dos):
    async with pg_session_factory() as s, s.begin():
        await set_tenant_context(s, dos["a"])
        tenants = set((await s.execute(text("SELECT tenant_id FROM tenant_settings"))).scalars())
    assert tenants == {dos["a"]}


async def test_a_no_actualiza_ni_borra_la_configuracion_de_b(
    pg_session_factory, pg_admin_engine, dos
):
    antes = await _filas(pg_admin_engine, dos["b"])
    async with pg_session_factory() as s, s.begin():
        await set_tenant_context(s, dos["a"])
        upd = await s.execute(
            text(
                "UPDATE tenant_settings SET deposit_bps = 9999, address = 'pisado' "
                "WHERE tenant_id = :b"
            ),
            {"b": dos["b"]},
        )
        assert upd.rowcount == 0
    # El runtime ni siquiera tiene DELETE sobre la tabla (es 1:1 y sin anulación): falla cerrado.
    with pytest.raises(DBAPIError):
        async with pg_session_factory() as s, s.begin():
            await set_tenant_context(s, dos["a"])
            await s.execute(
                text("DELETE FROM tenant_settings WHERE tenant_id = :b"), {"b": dos["b"]}
            )
    assert await _filas(pg_admin_engine, dos["b"]) == antes


async def test_a_no_puede_mudar_su_configuracion_a_b(pg_session_factory, pg_admin_engine, dos):
    antes = await _filas(pg_admin_engine, dos["a"])
    with pytest.raises(DBAPIError):
        async with pg_session_factory() as s, s.begin():
            await set_tenant_context(s, dos["a"])
            await s.execute(
                text("UPDATE tenant_settings SET tenant_id = :b WHERE tenant_id = :a"),
                {"a": dos["a"], "b": dos["b"]},
            )
    assert await _filas(pg_admin_engine, dos["a"]) == antes


async def test_a_no_inserta_configuracion_para_b_ni_una_segunda_para_si(
    pg_session_factory, pg_admin_engine, dos
):
    for destino in (dos["b"], dos["a"]):  # RLS WITH CHECK / UNIQUE (tenant_id)
        with pytest.raises(DBAPIError):
            async with pg_session_factory() as s, s.begin():
                await set_tenant_context(s, dos["a"])
                await s.execute(
                    text(
                        "INSERT INTO tenant_settings (id, tenant_id) "
                        "VALUES (gen_random_uuid(), :t)"
                    ),
                    {"t": destino},
                )
    assert len(await _filas(pg_admin_engine, dos["a"])) == 1
    assert len(await _filas(pg_admin_engine, dos["b"])) == 1


async def test_settings_service_lee_solo_lo_propio(pg_session_factory, pg_admin_engine, dos):
    async with pg_admin_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tenant_settings SET address = 'Calle A 1', deposit_bps = 1111 "
                "WHERE tenant_id = :t"
            ),
            {"t": dos["a"]},
        )
        await conn.execute(
            text(
                "UPDATE tenant_settings SET address = 'Calle B 2', deposit_bps = 2222 "
                "WHERE tenant_id = :t"
            ),
            {"t": dos["b"]},
        )
    for lado, direccion, bps in (("a", "Calle A 1", 1111), ("b", "Calle B 2", 2222)):
        async with pg_session_factory() as s, s.begin():
            actual = await SettingsService(s, dos[lado], dos[f"owner_{lado}"]).get()
        assert actual.settings.tenant_id == dos[lado]
        assert actual.tenant.id == dos[lado]
        assert (actual.settings.address, actual.settings.deposit_bps) == (direccion, bps)


async def test_settings_service_escribe_solo_lo_propio_en_los_dos_sentidos(
    pg_session_factory, pg_admin_engine, dos
):
    for lado, otro in (("a", "b"), ("b", "a")):
        antes_otro = await _filas(pg_admin_engine, dos[otro])
        slug_otro = await _slug(pg_admin_engine, dos[otro])
        async with pg_session_factory() as s, s.begin():
            await SettingsService(s, dos[lado], dos[f"owner_{lado}"]).update(
                {"address": f"Nueva {lado}", "deposit_bps": 4321}, slug=f"nuevo-{lado}-slug"
            )
        assert await _filas(pg_admin_engine, dos[otro]) == antes_otro, f"{lado} tocó a {otro}"
        assert await _slug(pg_admin_engine, dos[otro]) == slug_otro
        assert await _slug(pg_admin_engine, dos[lado]) == f"nuevo-{lado}-slug"
        async with pg_session_factory() as s, s.begin():
            propio = await SettingsService(s, dos[lado], dos[f"owner_{lado}"]).get()
        assert propio.settings.address == f"Nueva {lado}"


async def test_el_slug_de_a_no_pisa_al_de_b(pg_session_factory, pg_admin_engine, dos):
    slug_b = await _slug(pg_admin_engine, dos["b"])
    with pytest.raises(SlugTakenError):
        async with pg_session_factory() as s, s.begin():
            await SettingsService(s, dos["a"], dos["owner_a"]).update({}, slug=slug_b)
    assert await _slug(pg_admin_engine, dos["b"]) == slug_b


async def test_http_patch_con_slug_tomado_es_409_y_no_aplica_nada_a_medias(
    pg_client, pg_admin_engine, dos
):
    from app.tests.conftest import cookie_header  # noqa: PLC0415
    from app.utils.cookies import ACCESS_COOKIE  # noqa: PLC0415
    from app.utils.security import create_access_token  # noqa: PLC0415

    def sesion(user: uuid.UUID, tenant: uuid.UUID) -> dict[str, str]:
        token = create_access_token({"sub": str(user), "tenant_id": str(tenant), "ver": 0})
        return cookie_header({ACCESS_COOKIE: token})

    slug_b = await _slug(pg_admin_engine, dos["b"])
    antes = await _filas(pg_admin_engine, dos["a"])
    r = await pg_client.patch(
        "/v1/settings",
        json={"slug": slug_b, "hold_min": 9, "address": "No debe quedar"},
        headers=sesion(dos["owner_a"], dos["a"]),
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SLUG_TAKEN", r.text
    assert await _filas(pg_admin_engine, dos["a"]) == antes
    assert await _slug(pg_admin_engine, dos["b"]) == slug_b
    # y B sigue viendo solo lo suyo por la API
    vista = await pg_client.get("/v1/settings", headers=sesion(dos["owner_b"], dos["b"]))
    assert vista.status_code == 200 and vista.json()["slug"] == slug_b
