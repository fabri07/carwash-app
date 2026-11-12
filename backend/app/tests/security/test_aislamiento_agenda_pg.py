"""Tester-aislamiento del PR 5.1 contra **Postgres real** — FASE-5-CONTRATO §3.7 y adenda §12.

Escrito por quien NO implementó el 5.1. El recorrido genérico de las tablas nuevas
(`service_schedule_rules`, `schedule_openings`, `customer_incident_marks`: leer, modificar,
insertar con tenant ajeno, apuntar a un padre ajeno por cada FK) ya corre en
`test_aislamiento_dominio_pg.py` y `test_cruces_dominio_pg.py` porque descubren las tablas por
metadata; y las operaciones de `AgendaService` con ids ajenos, en `_intentos_servicios`. Acá va lo
propio del PR:

1. **`public_tenant_by_slug`** atacada: entradas raras (NULL, comodines, inyección, espacios,
   mayúsculas), un negocio sin fila de configuración, sombra de objetos en `pg_temp`, y que no
   sirve para leer otra cosa (devuelve solo el `id`, no se puede llamar como otra firma).
2. **Políticas `<tabla>_owner_public_lookup`** (adenda 5.1-3): no alcanzan al rol de runtime ni con
   ni sin contexto; el runtime no puede volverse el dueño; el dueño solo **lee**: sin contexto no
   escribe `tenants` ni `tenant_settings`.
3. **HTTP con RLS y rollback reales**: B no anula el bloqueo ni la apertura de A, no cuelga una
   apertura del puesto de A y no ve los turnos de A al bloquear; la fila de A no cambia (testigo:
   el superusuario).

Datos sintéticos (`@example.com`).
"""

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.persistence.db.rls import APP_ROLE, PUBLIC_TENANT_LOOKUP_FUNCTION
from app.persistence.db.tenant_context import set_tenant_context
from app.tests.application._armado import Lavadero, armar_catalogo
from app.tests.conftest import cookie_header
from app.tests.conftest_pg import owner_url
from app.utils.cookies import ACCESS_COOKIE
from app.utils.security import create_access_token

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

FN = PUBLIC_TENANT_LOOKUP_FUNCTION
OWNER_ROLE = "carwash_owner"


async def _slug(admin: AsyncEngine, tenant: uuid.UUID) -> str:
    async with admin.connect() as conn:
        return str(await conn.scalar(text("SELECT slug FROM tenants WHERE id = :t"), {"t": tenant}))


async def _ejecutar(admin: AsyncEngine, sql: str, **params: Any) -> None:
    async with admin.begin() as conn:
        await conn.execute(text(sql), params)


async def _dar_de_alta(admin: AsyncEngine, *tenants: uuid.UUID) -> None:
    await _ejecutar(
        admin,
        "UPDATE tenant_settings SET onboarding_completed_at = :c WHERE tenant_id = ANY(:t)",
        c=datetime(2026, 11, 1, tzinfo=UTC),
        t=list(tenants),
    )


async def _buscar(fabrica, slug: Any, contexto: uuid.UUID | None = None) -> Any:
    async with fabrica() as s, s.begin():
        if contexto is not None:
            await set_tenant_context(s, contexto)
        return await s.scalar(text(f"SELECT {FN}(:s)"), {"s": slug})


def _owner_engine():
    return create_async_engine(owner_url().replace("postgresql://", "postgresql+asyncpg://", 1))


# ── 1 · public_tenant_by_slug ────────────────────────────────────────────────


@pytest.fixture
async def listos(pg_admin_engine, pg_tenant_a, pg_tenant_b) -> dict[str, Any]:
    await _dar_de_alta(pg_admin_engine, pg_tenant_a, pg_tenant_b)
    return {
        "a": pg_tenant_a,
        "b": pg_tenant_b,
        "slug_a": await _slug(pg_admin_engine, pg_tenant_a),
    }


@pytest.mark.parametrize(
    "entrada",
    [
        None,
        "",
        "%",
        "tenant-%",
        "_",
        "*",
        "' OR '1'='1",
        "x'; SELECT id FROM tenants; --",
        "tenant-\\",
    ],
    ids=lambda x: repr(x),
)
async def test_entradas_raras_no_devuelven_ningun_negocio(pg_session_factory, listos, entrada):
    assert await _buscar(pg_session_factory, entrada) is None


async def test_el_slug_tiene_que_ser_exacto(pg_session_factory, listos):
    slug = listos["slug_a"]
    assert await _buscar(pg_session_factory, slug) == listos["a"]  # control
    for variante in (f" {slug}", f"{slug} ", slug[:-1], f"{slug}-", f"{slug}\n"):
        assert await _buscar(pg_session_factory, variante) is None, repr(variante)


async def test_con_el_contexto_de_b_devuelve_el_id_de_a_y_nada_mas(pg_session_factory, listos):
    """Es pública a propósito (el slug está en la URL): el contexto no la cambia, y lo único que
    sale es el id, que no abre filas de A por RLS."""
    assert await _buscar(pg_session_factory, listos["slug_a"], listos["b"]) == listos["a"]
    async with pg_session_factory() as s, s.begin():
        await set_tenant_context(s, listos["b"])
        filas = await s.scalar(
            text(f"SELECT count(*) FROM tenants WHERE id = {FN}(:s)"), {"s": listos["slug_a"]}
        )
        config = await s.scalar(
            text(f"SELECT count(*) FROM tenant_settings WHERE tenant_id = {FN}(:s)"),
            {"s": listos["slug_a"]},
        )
    assert filas == 0 and config == 0


async def test_un_negocio_sin_fila_de_configuracion_no_aparece(
    pg_session_factory, pg_admin_engine, listos
):
    await _ejecutar(
        pg_admin_engine, "DELETE FROM tenant_settings WHERE tenant_id = :t", t=listos["a"]
    )
    assert await _buscar(pg_session_factory, listos["slug_a"]) is None


async def test_dar_de_baja_o_desarmar_el_alta_lo_saca_en_el_acto(
    pg_session_factory, pg_admin_engine, listos
):
    await _ejecutar(
        pg_admin_engine,
        "UPDATE tenant_settings SET onboarding_completed_at = NULL WHERE tenant_id = :t",
        t=listos["a"],
    )
    assert await _buscar(pg_session_factory, listos["slug_a"]) is None
    await _dar_de_alta(pg_admin_engine, listos["a"])
    await _ejecutar(
        pg_admin_engine,
        "UPDATE tenants SET voided_at = now(), void_reason = 'DESACTIVADO' WHERE id = :t",
        t=listos["a"],
    )
    assert await _buscar(pg_session_factory, listos["slug_a"]) is None


async def test_objetos_en_pg_temp_no_secuestran_la_funcion(pg_session_factory, listos):
    """`search_path = public, pg_temp` y tablas calificadas: una tabla temporal `tenants` o
    `tenant_settings` del runtime (si puede crearlas) no cambia lo que la función lee.

    `ON COMMIT DROP`: una tabla temporal vive lo que la **conexión**, y la del pool la heredaría
    el test siguiente (pg_temp va primero en el search_path por defecto)."""
    async with pg_session_factory() as s, s.begin():
        puede = await s.scalar(
            text("SELECT has_database_privilege(current_user, current_database(), 'TEMP')")
        )
        if not puede:
            pytest.skip("el runtime no puede crear tablas temporales: no hay vector")
        falso = uuid.uuid4()
        await s.execute(
            text(
                "CREATE TEMP TABLE tenants (id uuid, slug text, voided_at timestamptz) "
                "ON COMMIT DROP"
            )
        )
        await s.execute(
            text(
                "CREATE TEMP TABLE tenant_settings "
                "(tenant_id uuid, onboarding_completed_at timestamptz) ON COMMIT DROP"
            )
        )
        await s.execute(
            text("INSERT INTO pg_temp.tenants VALUES (:i, 'negocio-falso', NULL)"), {"i": falso}
        )
        await s.execute(
            text("INSERT INTO pg_temp.tenant_settings VALUES (:i, now())"), {"i": falso}
        )
        await s.execute(text("SET LOCAL search_path = pg_temp, public"))
        assert await s.scalar(text(f"SELECT public.{FN}('negocio-falso')")) is None
        assert (
            await s.scalar(text(f"SELECT public.{FN}(:s)"), {"s": listos["slug_a"]}) == listos["a"]
        )


async def test_la_funcion_no_tiene_otras_firmas_ni_es_volatil_y_su_config_es_la_esperada(
    pg_admin_engine,
):
    async with pg_admin_engine.connect() as conn:
        filas = (
            await conn.execute(
                text(
                    "SELECT pg_get_function_identity_arguments(p.oid) AS args, "
                    "p.provolatile::text, p.proconfig, p.proleakproof, l.lanname "
                    "FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang "
                    "WHERE p.proname = :f"
                ),
                {"f": FN},
            )
        ).all()
        # Ningún rol fuera del dueño y del runtime puede ejecutarla.
        otros = (
            await conn.execute(
                text(
                    "SELECT r.rolname FROM pg_roles r, pg_proc p WHERE p.proname = :f "
                    "AND has_function_privilege(r.oid, p.oid, 'EXECUTE') "
                    "AND NOT r.rolsuper AND r.rolname NOT IN (:o, :a)"
                ),
                {"f": FN, "o": OWNER_ROLE, "a": APP_ROLE},
            )
        ).scalars()
        otros_roles = [r for r in otros if not r.startswith("pg_")]
    assert len(filas) == 1
    f = filas[0]
    assert f.args == "p_slug text"
    assert f.provolatile == "s" and f.lanname == "sql"
    assert f.proconfig == ["search_path=public, pg_temp"]
    assert otros_roles == []


# ── 2 · Políticas de lectura del dueño (adenda 5.1-3) ────────────────────────


async def test_el_runtime_no_es_ni_puede_volverse_el_dueno(pg_admin_engine, pg_session_factory):
    async with pg_admin_engine.connect() as conn:
        miembro = await conn.scalar(
            text("SELECT pg_has_role(:a, :o, 'MEMBER')"), {"a": APP_ROLE, "o": OWNER_ROLE}
        )
    assert miembro is False
    with pytest.raises(DBAPIError):
        async with pg_session_factory() as s, s.begin():
            await s.execute(text(f"SET ROLE {OWNER_ROLE}"))


async def test_las_politicas_del_dueno_no_abren_nada_al_runtime(
    pg_session_factory, pg_tenant_factory
):
    tenants = [await pg_tenant_factory(f"Lavadero {i}") for i in range(3)]
    async with pg_session_factory() as s, s.begin():
        assert await s.scalar(text("SELECT count(*) FROM tenants")) == 0
        assert await s.scalar(text("SELECT count(*) FROM tenant_settings")) == 0
    async with pg_session_factory() as s, s.begin():
        await set_tenant_context(s, tenants[0])
        assert set((await s.execute(text("SELECT id FROM tenants"))).scalars()) == {tenants[0]}
        assert set((await s.execute(text("SELECT tenant_id FROM tenant_settings"))).scalars()) == {
            tenants[0]
        }
        # Escribir lo de otro: 0 filas (la política de lectura del dueño no cuenta).
        upd = await s.execute(
            text("UPDATE tenants SET name = 'pisado' WHERE id = :t"), {"t": tenants[1]}
        )
        assert upd.rowcount == 0


async def test_el_dueno_sin_contexto_lee_pero_no_escribe_tenants_ni_settings(
    pg_admin_engine, pg_tenant_a, pg_tenant_b
):
    # Sin fila de A: el INSERT de abajo solo lo puede cortar el WITH CHECK, no el UNIQUE.
    await _ejecutar(
        pg_admin_engine, "DELETE FROM tenant_settings WHERE tenant_id = :t", t=pg_tenant_a
    )
    engine = _owner_engine()
    try:
        async with engine.connect() as conn, conn.begin():
            for sql in (
                "UPDATE tenants SET name = 'pisado'",
                "UPDATE tenant_settings SET slot_step_min = 5",
                "DELETE FROM tenant_settings",
            ):
                r = await conn.execute(text(sql))
                assert r.rowcount == 0, sql
        with pytest.raises(DBAPIError):
            async with engine.connect() as conn, conn.begin():
                await conn.execute(
                    text(
                        "INSERT INTO tenant_settings (id, tenant_id) "
                        "VALUES (gen_random_uuid(), :t)"
                    ),
                    {"t": pg_tenant_a},
                )
    finally:
        await engine.dispose()
    async with pg_admin_engine.connect() as conn:
        nombres = set((await conn.execute(text("SELECT name FROM tenants"))).scalars())
        pasos = set(
            (await conn.execute(text("SELECT slot_step_min FROM tenant_settings"))).scalars()
        )
        n = await conn.scalar(text("SELECT count(*) FROM tenant_settings"))
    assert "pisado" not in nombres and pasos == {30} and n == 1


async def test_las_politicas_nuevas_son_permisivas_solo_select_y_solo_del_dueno(pg_admin_engine):
    async with pg_admin_engine.connect() as conn:
        filas = (
            await conn.execute(
                text(
                    "SELECT tablename, permissive, cmd, roles::text[] AS roles, qual, with_check "
                    "FROM pg_policies WHERE policyname LIKE '%_owner_public_lookup'"
                )
            )
        ).all()
    assert sorted(f.tablename for f in filas) == ["tenant_settings", "tenants"]
    for f in filas:
        assert (f.permissive, f.cmd, list(f.roles)) == ("PERMISSIVE", "SELECT", [OWNER_ROLE])
        assert f.with_check is None


# ── 3 · HTTP con RLS real ────────────────────────────────────────────────────


def _sesion(user: uuid.UUID, tenant: uuid.UUID) -> dict[str, str]:
    token = create_access_token({"sub": str(user), "tenant_id": str(tenant), "ver": 0})
    return cookie_header({ACCESS_COOKIE: token})


HOY = datetime.now(UTC)
LUNES = (HOY + timedelta(days=(7 - HOY.weekday()) % 7 or 7)).date()


@pytest_asyncio.fixture
async def dos(pg_session_factory, pg_tenant_a, pg_tenant_b, pg_user_factory) -> dict[str, Any]:
    owner_a = await pg_user_factory(pg_tenant_a, "agenda-owner-a@example.com")
    owner_b = await pg_user_factory(pg_tenant_b, "agenda-owner-b@example.com")
    async with pg_session_factory() as s, s.begin():
        a: Lavadero = await armar_catalogo(s, pg_tenant_a, owner_a)
        await a.catalogo.add_business_hours(1, time(9, 0), time(13, 0))
    async with pg_session_factory() as s, s.begin():
        b: Lavadero = await armar_catalogo(s, pg_tenant_b, owner_b)
        await b.catalogo.add_business_hours(1, time(9, 0), time(13, 0))
    async with pg_session_factory() as s, s.begin():
        turno = await a.en(s).turno(inicio=datetime.combine(LUNES, time(13), tzinfo=UTC), ahora=HOY)
    return {
        "a": a,
        "b": b,
        "ha": _sesion(owner_a, pg_tenant_a),
        "hb": _sesion(owner_b, pg_tenant_b),
        "turno_a": turno,
    }


async def _foto(admin: AsyncEngine, tabla: str, fila: str) -> Any:
    async with admin.connect() as conn:
        return await conn.scalar(
            text(f"SELECT row_to_json(t)::text FROM {tabla} t WHERE id = :i"),  # noqa: S608
            {"i": fila},
        )


async def test_b_no_anula_el_bloqueo_ni_la_apertura_de_a(pg_client, pg_admin_engine, dos):
    bloqueo = await pg_client.post(
        "/v1/agenda/blocks", json={"date": LUNES.isoformat()}, headers=dos["ha"]
    )
    apertura = await pg_client.post(
        "/v1/agenda/openings", json={"date": LUNES.isoformat()}, headers=dos["ha"]
    )
    assert bloqueo.status_code == apertura.status_code == 201
    casos = (
        ("blocks", "schedule_blocks", bloqueo.json()["block"]["id"]),
        ("openings", "schedule_openings", apertura.json()["id"]),
    )
    for ruta, tabla, fila in casos:
        antes = await _foto(pg_admin_engine, tabla, fila)
        azar = uuid.uuid4()
        ajeno = await pg_client.delete(f"/v1/agenda/{ruta}/{fila}", headers=dos["hb"])
        nada = await pg_client.delete(f"/v1/agenda/{ruta}/{azar}", headers=dos["hb"])
        assert ajeno.status_code == nada.status_code == 404
        assert ajeno.content.replace(fila.encode(), b"<id>") == nada.content.replace(
            str(azar).encode(), b"<id>"
        )
        assert await _foto(pg_admin_engine, tabla, fila) == antes
        assert (await pg_client.get(f"/v1/agenda/{ruta}", headers=dos["hb"])).json()["total"] == 0


async def test_b_no_cuelga_una_apertura_ni_un_bloqueo_del_puesto_de_a(
    pg_client, pg_admin_engine, dos
):
    for ruta in ("openings", "blocks"):
        r = await pg_client.post(
            f"/v1/agenda/{ruta}",
            json={"date": LUNES.isoformat(), "resource_id": str(dos["a"].puesto)},
            headers=dos["hb"],
        )
        assert r.status_code == 404, r.text
    async with pg_admin_engine.connect() as conn:
        colgadas = await conn.scalar(
            text(
                "SELECT (SELECT count(*) FROM schedule_openings WHERE resource_id = :p) "
                "+ (SELECT count(*) FROM schedule_blocks WHERE resource_id = :p)"
            ),
            {"p": dos["a"].puesto},
        )
    assert colgadas == 0


async def test_el_bloqueo_de_b_no_trae_los_turnos_de_a(pg_client, dos):
    r = await pg_client.post(
        "/v1/agenda/blocks", json={"date": LUNES.isoformat()}, headers=dos["hb"]
    )
    assert r.status_code == 201, r.text
    assert r.json()["overlapping_bookings"] == []
    assert dos["turno_a"].code not in r.text
    control = await pg_client.post(
        "/v1/agenda/blocks", json={"date": LUNES.isoformat()}, headers=dos["ha"]
    )
    assert [x["id"] for x in control.json()["overlapping_bookings"]] == [str(dos["turno_a"].id)]


async def test_disponibilidad_y_reglas_de_a_no_se_ven_ni_se_tocan_desde_b(
    pg_client, pg_admin_engine, dos
):
    a, b = dos["a"], dos["b"]
    params = {
        "date": LUNES.isoformat(),
        "vehicle_size_id": str(b.auto),
        "service_ids": [str(a.lavado)],
    }
    r = await pg_client.get("/v1/agenda/availability", params=params, headers=dos["hb"])
    assert r.status_code == 404, r.text
    antes = await _foto(pg_admin_engine, "services", str(a.lavado))
    r = await pg_client.patch(
        f"/v1/services/{a.lavado}",
        json={
            "booking_mode": "SOLO_A_MANO",
            "schedule_rules": [{"weekday": 1, "window_start": "09:00", "last_start": "09:00"}],
        },
        headers=dos["hb"],
    )
    assert r.status_code == 404, r.text
    assert await _foto(pg_admin_engine, "services", str(a.lavado)) == antes
    async with pg_admin_engine.connect() as conn:
        reglas = await conn.scalar(
            text("SELECT count(*) FROM service_schedule_rules WHERE service_id = :s"),
            {"s": a.lavado},
        )
    assert reglas == 0


async def test_anular_el_puesto_ocupado_de_a_desde_b_es_404_no_in_use(
    pg_client, pg_admin_engine, dos
):
    puesto = str(dos["a"].puesto)  # el del turno de A
    control = await pg_client.delete(f"/v1/resources/{puesto}", headers=dos["ha"])
    assert control.status_code == 409 and control.json()["detail"]["code"] == "IN_USE"
    ajeno = await pg_client.delete(f"/v1/resources/{puesto}", headers=dos["hb"])
    assert ajeno.status_code == 404, ajeno.text
    async with pg_admin_engine.connect() as conn:
        vivo = await conn.scalar(
            text("SELECT voided_at IS NULL FROM resources WHERE id = :p"), {"p": puesto}
        )
    assert vivo is True
