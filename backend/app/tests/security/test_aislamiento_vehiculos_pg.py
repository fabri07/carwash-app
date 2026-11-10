"""Aislamiento del catálogo de vehículos por RLS — FASE-4-CONTRATO A1, A6 y A10 (PR 4.4).

Todo con el rol de runtime `carwash_app` (RLS activo); el superusuario solo siembra y es el testigo.
El recorrido genérico de las cuatro tablas nuevas (una fila por tabla, B7/B14) ya lo hace
`_poblar_dominio` en `test_aislamiento_dominio_pg.py`; acá va lo propio del PR:

1. **El registro copia la base con el rol de runtime**: el `WITH CHECK` de RLS acepta las ~450
   filas porque el contexto de tenant se fija antes; el otro negocio no ve ninguna.
2. **Sin contexto, 0 filas**; con el de A, solo las de A, en las cuatro tablas.
3. **La FK compuesta** impide colgar un modelo de B de una marca de A, y un vehículo de B de un
   modelo de A, aunque se conozca el id.
4. **Por HTTP**: lo que suma A no aparece en B (A10) y el 404 ajeno es el de uno inexistente.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.vehicle_catalog import load_vehicle_base
from app.persistence.db.tenant_context import set_tenant_context

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

PASSWORD = "correct-horse-battery"
TABLAS = ("vehicle_makes", "vehicle_models", "vehicle_colors", "body_type_sizes")
BASE = load_vehicle_base()


async def _registrar(pg_client, email: str, negocio: str) -> dict[str, Any]:
    """Alta por la API; devuelve el tenant y las cookies del dueño (el jar queda limpio)."""
    r = await pg_client.post(
        "/v1/auth/register", json={"email": email, "password": PASSWORD, "tenant": negocio}
    )
    assert r.status_code == 201, r.text
    cookies = dict(r.cookies)
    pg_client.cookies.clear()
    return {"tenant_id": r.json()["tenant"]["id"], "cookies": cookies}


async def test_el_registro_copia_la_base_con_el_rol_de_runtime(pg_client, pg_admin_engine):
    a = await _registrar(pg_client, "vehiculos-a@ejemplo.invalid", "Lavadero Sintético A")
    b = await _registrar(pg_client, "vehiculos-b@ejemplo.invalid", "Lavadero Sintético B")
    async with pg_admin_engine.connect() as conn:
        for alta in (a, b):
            fila = (
                await conn.execute(
                    text(
                        "SELECT (SELECT count(*) FROM vehicle_makes WHERE tenant_id = :t), "
                        "(SELECT count(*) FROM vehicle_models WHERE tenant_id = :t), "
                        "(SELECT count(*) FROM vehicle_colors WHERE tenant_id = :t)"
                    ),
                    {"t": alta["tenant_id"]},
                )
            ).one()
            assert tuple(fila) == (len(BASE.makes), BASE.model_count, len(BASE.colors))
        cruzados = await conn.scalar(
            text(
                "SELECT count(*) FROM vehicle_models AS x JOIN vehicle_makes AS m "
                "ON m.id = x.make_id WHERE m.tenant_id <> x.tenant_id"
            )
        )
    assert cruzados == 0

    # A suma una marca; B no la ve, y el 404 ajeno es el de uno inexistente.
    propia = await pg_client.post(
        "/v1/vehicle-makes", json={"name": "Marca Sintética"}, cookies=a["cookies"]
    )
    assert propia.status_code == 201, propia.text
    en_b = await pg_client.get(
        "/v1/vehicle-makes",
        params={"q": "sintetica", "include_hidden": True},
        cookies=b["cookies"],
    )
    assert en_b.status_code == 200 and en_b.json()["total"] == 0
    pid = propia.json()["id"]
    ajeno = await pg_client.get(f"/v1/vehicle-makes/{pid}", cookies=b["cookies"])
    azar = uuid.uuid4()
    nada = await pg_client.get(f"/v1/vehicle-makes/{azar}", cookies=b["cookies"])
    assert ajeno.status_code == 404
    assert ajeno.content.replace(pid.encode(), b"<id>") == nada.content.replace(
        str(azar).encode(), b"<id>"
    )


async def _sembrar(admin, tenant_id: uuid.UUID) -> dict[str, uuid.UUID]:
    ids = {t: uuid.uuid4() for t in ("make", "model", "color", "size", "map")}
    async with admin.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO vehicle_makes (id, tenant_id, name, search_key) "
                "VALUES (:i, :t, 'Marca', 'marca')"
            ),
            {"i": ids["make"], "t": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO vehicle_models (id, tenant_id, make_id, name, body_type, search_key) "
                "VALUES (:i, :t, :m, 'Modelo', 'SUV', 'modelo')"
            ),
            {"i": ids["model"], "t": tenant_id, "m": ids["make"]},
        )
        await conn.execute(
            text(
                "INSERT INTO vehicle_colors (id, tenant_id, name, search_key) "
                "VALUES (:i, :t, 'Gris', 'gris')"
            ),
            {"i": ids["color"], "t": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO vehicle_sizes (id, tenant_id, code, label) "
                "VALUES (:i, :t, 'AUTO', 'Auto')"
            ),
            {"i": ids["size"], "t": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO body_type_sizes (id, tenant_id, body_type, vehicle_size_id) "
                "VALUES (:i, :t, 'SUV', :s)"
            ),
            {"i": ids["map"], "t": tenant_id, "s": ids["size"]},
        )
    return ids


async def test_rls_sin_contexto_nada_y_con_a_solo_lo_de_a(
    pg_session_factory, pg_admin_engine, pg_tenant_a, pg_tenant_b
):
    await _sembrar(pg_admin_engine, pg_tenant_a)
    await _sembrar(pg_admin_engine, pg_tenant_b)
    async with pg_session_factory() as s, s.begin():
        for tabla in TABLAS:
            assert await s.scalar(text(f"SELECT count(*) FROM {tabla}")) == 0, tabla
    async with pg_session_factory() as s, s.begin():
        await set_tenant_context(s, pg_tenant_a)
        for tabla in TABLAS:
            tenants = set((await s.execute(text(f"SELECT tenant_id FROM {tabla}"))).scalars())
            assert tenants == {pg_tenant_a}, tabla
        upd = await s.execute(
            text("UPDATE vehicle_makes SET name = 'pisada' WHERE tenant_id = :b"),
            {"b": pg_tenant_b},
        )
        assert upd.rowcount == 0


async def test_a_no_inserta_filas_de_b(pg_session_factory, pg_tenant_a, pg_tenant_b):
    with pytest.raises(DBAPIError):
        async with pg_session_factory() as s, s.begin():
            await set_tenant_context(s, pg_tenant_a)
            await s.execute(
                text(
                    "INSERT INTO vehicle_colors (id, tenant_id, name, search_key) "
                    "VALUES (:i, :b, 'Intruso', 'intruso')"
                ),
                {"i": uuid.uuid4(), "b": pg_tenant_b},
            )


async def test_la_fk_compuesta_no_cruza_negocios(
    pg_session_factory, pg_admin_engine, pg_tenant_a, pg_tenant_b
):
    de_a = await _sembrar(pg_admin_engine, pg_tenant_a)
    with pytest.raises(DBAPIError, match="fk_vehicle_models_tenant_id_make_id_vehicle_makes"):
        async with pg_session_factory() as s, s.begin():
            await set_tenant_context(s, pg_tenant_b)
            await s.execute(
                text(
                    "INSERT INTO vehicle_models "
                    "(id, tenant_id, make_id, name, body_type, search_key) "
                    "VALUES (:i, :b, :m, 'Intruso', 'SUV', 'intruso')"
                ),
                {"i": uuid.uuid4(), "b": pg_tenant_b, "m": de_a["make"]},
            )
    with pytest.raises(DBAPIError, match="fk_vehicles_tenant_id_model_id_vehicle_models"):
        async with pg_session_factory() as s, s.begin():
            await set_tenant_context(s, pg_tenant_b)
            await s.execute(
                text("INSERT INTO vehicles (id, tenant_id, model_id) VALUES (:i, :b, :m)"),
                {"i": uuid.uuid4(), "b": pg_tenant_b, "m": de_a["model"]},
            )
