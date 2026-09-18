"""Cruces entre lavaderos que los tests genéricos (B7/B14) no cubren — Tester-aislamiento de T3.

`test_aislamiento_dominio_pg.py` prueba los cuatro intentos básicos por tabla (leer, modificar,
insertar con tenant ajeno, apuntar a un padre ajeno). Acá van las vías laterales:

1. **Catálogo:** lo que la base tiene de verdad (no la metadata del ORM): FKs compuestas en
   `pg_constraint`, únicos y `EXCLUDE` que incluyen `tenant_id`, sin vistas ni secuencias
   compartidas, una sola función `SECURITY DEFINER`, y un rol de runtime sin `TRUNCATE`
   (que no pasa por RLS), sin `DELETE`, sin `REFERENCES` ni `TRIGGER`, sin `CREATE`.
2. **Escrituras con lectura adentro:** `INSERT … SELECT` desde filas ajenas, `ON CONFLICT`
   sobre el id de una fila ajena.
3. **FKs:** hacia un padre **anulado** de otro lavadero, y el ciclo `bookings.quote_id` ↔
   `quotes.booking_id` (FK con `use_alter`) cruzando lavaderos.
4. **`EXCLUDE`:** el turno de A no bloquea el mismo horario en B; el de B sí se bloquea a sí mismo.
5. **Otras puertas:** `COPY`, el dueño (`carwash_owner`) sin contexto, un `app.tenant_id` que no
   es un lavadero, una función no-leakproof en el `WHERE`, y `pg_stats`.
"""

import io
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import ForeignKeyConstraint, Table, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import app.persistence.models  # noqa: F401
from app.persistence.db.base import Base
from app.persistence.db.rls import APP_ROLE, AUTH_LOOKUP_FUNCTION
from app.persistence.db.tenant_context import set_tenant_context
from app.tests.conftest_pg import owner_url
from app.tests.security._poblar_dominio import FIN_TURNO, INICIO_TURNO, poblar

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

TABLAS_TENANT: dict[str, Table] = {
    t.name: t for t in Base.metadata.sorted_tables if "tenant_id" in t.c
}
APPEND_ONLY = {"job_events"}
ESCRIBIBLES = sorted(set(TABLAS_TENANT) - APPEND_ONLY)

#: Únicos que a propósito NO llevan `tenant_id`. Cada entrada, con su motivo.
UNICOS_GLOBALES_PERMITIDOS = {
    # El login es por email solo, sin elegir lavadero (ADR de F2, `models/user.py`).
    ("users", ("email",)),
}


def _columna_hija(fk: ForeignKeyConstraint) -> str:
    return next(c.name for c in fk.columns if c.name != "tenant_id")


def _un_caso_por_padre() -> list[Any]:
    """Una FK entre tablas de tenant por cada padre distinto (para no multiplicar la suite)."""
    vistos: dict[str, Any] = {}
    for tabla in TABLAS_TENANT.values():
        for fk in tabla.foreign_key_constraints:
            padre = fk.referred_table.name
            if padre == "tenants" or padre in vistos or tabla.name in APPEND_ONLY:
                continue
            columna = _columna_hija(fk)
            vistos[padre] = pytest.param(
                tabla.name, columna, padre, id=f"{tabla.name}.{columna}->{padre}"
            )
    return list(vistos.values())


CASOS_PADRE_ANULADO = _un_caso_por_padre()


@pytest.fixture
async def dos_lavaderos(pg_admin_engine, pg_tenant_a, pg_tenant_b):
    filas_a = await poblar(pg_admin_engine, pg_tenant_a)
    filas_b = await poblar(pg_admin_engine, pg_tenant_b)
    return pg_tenant_a, filas_a, pg_tenant_b, filas_b


async def _foto(admin: AsyncEngine, tabla: str, fila_id: uuid.UUID) -> Any:
    """La fila entera vista por el superusuario: el único testigo confiable de "no cambió"."""
    async with admin.connect() as conn:
        return await conn.scalar(
            text(f"SELECT row_to_json(t)::text FROM {tabla} AS t WHERE id = :i"), {"i": fila_id}
        )


def _columnas(tabla: str) -> list[str]:
    return [c.name for c in TABLAS_TENANT[tabla].c]


# ── 1 · Catálogo: lo que la base tiene de verdad ──────────────────────────────


async def _tablas_con_tenant_en_la_base(conn: Any) -> set[str]:
    filas = await conn.execute(
        text(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'tenant_id'"
        )
    )
    return set(filas.scalars())


async def test_toda_fk_entre_tablas_de_tenant_es_compuesta_en_la_base(pg_admin_engine):
    """B5 mira la metadata; esto mira `pg_constraint`. Si la migración y el ORM divergen
    (una FK simple creada a mano), acá se ve."""
    async with pg_admin_engine.connect() as conn:
        con_tenant = await _tablas_con_tenant_en_la_base(conn)
        fks = (
            await conn.execute(
                text(
                    """
                    SELECT c.conname, c.conrelid::regclass::text AS hija,
                           c.confrelid::regclass::text AS padre,
                           ARRAY(SELECT a.attname::text
                                 FROM unnest(c.conkey) WITH ORDINALITY k(n, i)
                                 JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n
                                 ORDER BY k.i) AS cols,
                           ARRAY(SELECT a.attname::text
                                 FROM unnest(c.confkey) WITH ORDINALITY k(n, i)
                                 JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.n
                                 ORDER BY k.i) AS refcols
                    FROM pg_constraint c
                    WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
                    """
                )
            )
        ).all()
    entre_tenants = [f for f in fks if f.padre != "tenants" and f.padre in con_tenant]
    assert len(entre_tenants) >= 40, "¿se migró el dominio?"
    for fk in entre_tenants:
        assert fk.hija in con_tenant, f"{fk.conname}: una tabla sin tenant apunta a {fk.padre}"
        pares = dict(zip(fk.cols, fk.refcols, strict=True))
        assert pares.get("tenant_id") == "tenant_id", f"{fk.conname} no es compuesta: {pares}"
        assert len(pares) == 2, f"{fk.conname}: {pares}"


async def test_todo_unico_y_exclude_de_tenant_incluye_tenant_id(pg_admin_engine):
    """Un único sin `tenant_id` hace que A y B choquen (DoS) y le dice a B qué valores tiene A."""
    async with pg_admin_engine.connect() as conn:
        con_tenant = await _tablas_con_tenant_en_la_base(conn)
        indices = (
            await conn.execute(
                text(
                    """
                    SELECT t.relname AS tabla, i.relname AS indice, x.indisexclusion AS excl,
                           ARRAY(SELECT coalesce(a.attname::text, '<expr>')
                                 FROM unnest(x.indkey::int2[]) WITH ORDINALITY k(n, o)
                                 LEFT JOIN pg_attribute a
                                   ON a.attrelid = x.indrelid AND a.attnum = k.n
                                 ORDER BY k.o) AS cols
                    FROM pg_index x
                    JOIN pg_class t ON t.oid = x.indrelid
                    JOIN pg_class i ON i.oid = x.indexrelid
                    WHERE t.relnamespace = 'public'::regnamespace
                      AND (x.indisunique OR x.indisexclusion) AND NOT x.indisprimary
                    """
                )
            )
        ).all()
    revisados = [i for i in indices if i.tabla in con_tenant]
    assert any(i.excl for i in revisados), "no apareció el EXCLUDE de bookings"
    for i in revisados:
        if (i.tabla, tuple(i.cols)) in UNICOS_GLOBALES_PERMITIDOS:
            continue
        assert "tenant_id" in i.cols, f"{i.tabla}.{i.indice} es global: {i.cols}"


async def test_sin_vistas_ni_secuencias_compartidas(pg_admin_engine):
    # Una vista sin `security_invoker` corre con los permisos de su dueño y se saltea RLS; una
    # secuencia compartida le cuenta a B cuántas filas carga A. Hoy no hay ninguna: si
    # aparece, que sea a conciencia (y con su test).
    async with pg_admin_engine.connect() as conn:
        objetos = (
            await conn.execute(
                text(
                    "SELECT relname, relkind FROM pg_class "
                    "WHERE relnamespace = 'public'::regnamespace AND relkind IN ('v', 'm', 'S')"
                )
            )
        ).all()
    assert objetos == [], objetos


async def test_la_unica_funcion_security_definer_es_el_login_y_no_devuelve_dominio(
    pg_admin_engine,
):
    async with pg_admin_engine.connect() as conn:
        definer = (
            await conn.execute(
                text(
                    "SELECT proname, pg_get_function_result(oid) AS resultado FROM pg_proc "
                    "WHERE pronamespace = 'public'::regnamespace AND prosecdef"
                )
            )
        ).all()
    assert [f.proname for f in definer] == [AUTH_LOOKUP_FUNCTION]
    assert definer[0].resultado == (
        "TABLE(id uuid, tenant_id uuid, password_hash text, token_version integer, role role)"
    )


async def test_la_funcion_de_login_no_encuentra_clientes_del_dominio(
    pg_session_factory, dos_lavaderos
):
    """`customers.email` no es una identidad: la función solo lee `users`."""
    tenant_a, _, _, _ = dos_lavaderos
    async with pg_session_factory() as session, session.begin():
        filas = (
            await session.execute(
                text(f"SELECT * FROM {AUTH_LOOKUP_FUNCTION}(:e)"),
                {"e": f"cliente-{tenant_a.hex[:12]}@ejemplo.invalid"},
            )
        ).all()
    assert filas == []


@pytest.mark.parametrize("tabla", sorted(TABLAS_TENANT))
async def test_el_runtime_no_tiene_privilegios_que_salteen_rls(pg_admin_engine, tabla):
    """`TRUNCATE` no pasa por RLS: con ese permiso B vaciaría la tabla de todos los lavaderos.
    `DELETE` no existe (ADR-0003: se anula), `REFERENCES` y `TRIGGER` abren puertas laterales."""
    async with pg_admin_engine.connect() as conn:
        for privilegio in ("TRUNCATE", "DELETE", "REFERENCES", "TRIGGER"):
            tiene = await conn.scalar(
                text("SELECT has_table_privilege(:rol, :tabla, :priv)"),
                {"rol": APP_ROLE, "tabla": f"public.{tabla}", "priv": privilegio},
            )
            assert not tiene, f"{APP_ROLE} tiene {privilegio} sobre {tabla}"
        duena = await conn.scalar(
            text("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = :t ::regclass"),
            {"t": f"public.{tabla}"},
        )
        assert duena != APP_ROLE


async def test_el_runtime_no_crea_objetos_ni_saltea_rls(pg_admin_engine):
    async with pg_admin_engine.connect() as conn:
        rol = (
            await conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"),
                {"r": APP_ROLE},
            )
        ).one()
        assert not rol.rolsuper and not rol.rolbypassrls
        assert not await conn.scalar(
            text("SELECT has_schema_privilege(:r, 'public', 'CREATE')"), {"r": APP_ROLE}
        )


# ── 2 · Escrituras que leen adentro ───────────────────────────────────────────


@pytest.mark.parametrize("tabla", sorted(TABLAS_TENANT))
async def test_insert_select_desde_filas_de_a_no_copia_nada(
    pg_session_factory, pg_admin_engine, dos_lavaderos, tabla
):
    """B intenta llevarse las filas de A a su propio lavadero: el `SELECT` interno ya pasa por
    RLS, así que no hay nada que copiar."""
    tenant_a, filas_a, tenant_b, _ = dos_lavaderos
    select = ", ".join(
        "gen_random_uuid()" if c == "id" else "CAST(:b AS uuid)" if c == "tenant_id" else c
        for c in _columnas(tabla)
    )
    async with pg_session_factory() as session, session.begin():
        await set_tenant_context(session, tenant_b)
        r = await session.execute(
            text(
                f"INSERT INTO {tabla} ({', '.join(_columnas(tabla))}) "
                f"SELECT {select} FROM {tabla} WHERE tenant_id = :a OR id = :fila_a"
            ),
            {"a": tenant_a, "b": tenant_b, "fila_a": filas_a[tabla]},
        )
        assert r.rowcount == 0
    async with pg_admin_engine.connect() as conn:
        assert (
            await conn.scalar(
                text(f"SELECT count(*) FROM {tabla} WHERE tenant_id = :b"), {"b": tenant_b}
            )
            == 1
        )


def _insert_con_id_ajeno(tabla: str, conflicto: str) -> str:
    """Copia la fila propia de B pero con el `id` de la fila de A, y resuelve el choque."""
    columnas = _columnas(tabla)
    select = ", ".join("CAST(:id_a AS uuid)" if c == "id" else c for c in columnas)
    return (
        f"INSERT INTO {tabla} ({', '.join(columnas)}) "
        f"SELECT {select} FROM {tabla} WHERE id = :fila_b ON CONFLICT (id) {conflicto}"
    )


@pytest.mark.parametrize("tabla", sorted(TABLAS_TENANT))
async def test_on_conflict_do_nothing_sobre_el_id_de_a_no_toca_nada(
    pg_session_factory, pg_admin_engine, dos_lavaderos, tabla
):
    _, filas_a, tenant_b, filas_b = dos_lavaderos
    antes = await _foto(pg_admin_engine, tabla, filas_a[tabla])
    async with pg_session_factory() as session, session.begin():
        await set_tenant_context(session, tenant_b)
        r = await session.execute(
            text(_insert_con_id_ajeno(tabla, "DO NOTHING")),
            {"id_a": filas_a[tabla], "fila_b": filas_b[tabla]},
        )
        # Observación (no fuga de datos): el choque de PK le confirma a B que el uuid existe.
        # Con uuid4 no es adivinable; se documenta en el reporte de T3.
        assert r.rowcount == 0
    assert await _foto(pg_admin_engine, tabla, filas_a[tabla]) == antes


@pytest.mark.parametrize("tabla", ESCRIBIBLES)
async def test_on_conflict_do_update_sobre_el_id_de_a_no_la_pisa(
    pg_session_factory, pg_admin_engine, dos_lavaderos, tabla
):
    """El `UPDATE` de un upsert sobre una fila invisible: Postgres corta con error de RLS
    en vez de pisarla (y mucho menos mudarla a B)."""
    _, filas_a, tenant_b, filas_b = dos_lavaderos
    antes = await _foto(pg_admin_engine, tabla, filas_a[tabla])
    async with pg_session_factory() as session:
        with pytest.raises(DBAPIError, match="row-level security"):
            async with session.begin():
                await set_tenant_context(session, tenant_b)
                await session.execute(
                    text(
                        _insert_con_id_ajeno(tabla, "DO UPDATE SET tenant_id = EXCLUDED.tenant_id")
                    ),
                    {"id_a": filas_a[tabla], "fila_b": filas_b[tabla]},
                )
    assert await _foto(pg_admin_engine, tabla, filas_a[tabla]) == antes


# ── 3 · FKs: padre anulado y el ciclo con use_alter ───────────────────────────


@pytest.mark.parametrize(("tabla", "columna", "padre"), CASOS_PADRE_ANULADO)
async def test_no_se_apunta_a_un_padre_anulado_de_a(
    pg_session_factory, pg_admin_engine, dos_lavaderos, tabla, columna, padre
):
    """Un padre anulado sigue existiendo: la FK compuesta lo rechaza igual por el tenant."""
    _, filas_a, tenant_b, filas_b = dos_lavaderos
    async with pg_admin_engine.begin() as conn:
        await conn.execute(
            text(
                f"UPDATE {padre} SET voided_at = now(), void_reason = 'ERROR_DE_CARGA' "
                "WHERE id = :i"
            ),
            {"i": filas_a[padre]},
        )
    async with pg_session_factory() as session:
        with pytest.raises(IntegrityError, match="foreign key"):
            async with session.begin():
                await set_tenant_context(session, tenant_b)
                await session.execute(
                    text(f"UPDATE {tabla} SET {columna} = :padre_a WHERE id = :fila_b"),
                    {"padre_a": filas_a[padre], "fila_b": filas_b[tabla]},
                )


async def test_hay_un_caso_de_padre_anulado_por_cada_padre():
    padres = {
        fk.referred_table.name
        for t in TABLAS_TENANT.values()
        if t.name not in APPEND_ONLY
        for fk in t.foreign_key_constraints
        if fk.referred_table.name != "tenants"
    }
    assert {c.values[2] for c in CASOS_PADRE_ANULADO} == padres


@pytest.mark.parametrize(
    ("tabla", "columna", "padre"),
    [("bookings", "quote_id", "quotes"), ("quotes", "booking_id", "bookings")],
)
async def test_el_ciclo_turno_cotizacion_no_cruza_lavaderos(
    pg_session_factory, pg_admin_engine, dos_lavaderos, tabla, columna, padre
):
    """`bookings.quote_id` es la FK con `use_alter` (se agrega con `ALTER` después de crear
    `quotes`): la que más fácil se pierde en una migración escrita a mano."""
    _, filas_a, tenant_b, filas_b = dos_lavaderos
    antes = await _foto(pg_admin_engine, tabla, filas_b[tabla])
    async with pg_session_factory() as session:
        with pytest.raises(IntegrityError, match="foreign key"):
            async with session.begin():
                await set_tenant_context(session, tenant_b)
                await session.execute(
                    text(f"UPDATE {tabla} SET {columna} = :padre_a WHERE id = :fila_b"),
                    {"padre_a": filas_a[padre], "fila_b": filas_b[tabla]},
                )
    assert await _foto(pg_admin_engine, tabla, filas_b[tabla]) == antes


# ── 4 · EXCLUDE por tenant ────────────────────────────────────────────────────


def _insert_turno(tabla_cols: list[str]) -> str:
    reemplazos = {
        "id": "CAST(:nuevo AS uuid)",
        "code": "CAST(:codigo AS text)",
        "resource_id": "CAST(:puesto AS uuid)",
        "quote_id": "NULL",
        "legacy_id": "NULL",
    }
    select = ", ".join(reemplazos.get(c, c) for c in tabla_cols)
    return (
        f"INSERT INTO bookings ({', '.join(tabla_cols)}) "
        f"SELECT {select} FROM bookings WHERE id = :molde"
    )


async def test_el_turno_de_a_no_bloquea_el_mismo_horario_en_b(
    pg_session_factory, pg_admin_engine, dos_lavaderos
):
    tenant_a, filas_a, tenant_b, filas_b = dos_lavaderos
    columnas = _columnas("bookings")
    async with pg_admin_engine.connect() as conn:
        rangos = (
            await conn.execute(
                text("SELECT tenant_id, start_at, end_at, status::text FROM bookings")
            )
        ).all()
    # `poblar` deja un turno bloqueante en el MISMO intervalo en A y en B.
    assert {(r.start_at, r.end_at, r.status) for r in rangos} == {
        (INICIO_TURNO, FIN_TURNO, "PENDIENTE_SEÑA")
    }
    assert {r.tenant_id for r in rangos} == {tenant_a, tenant_b}

    puesto_nuevo = uuid.uuid4()
    async with pg_session_factory() as session, session.begin():
        await set_tenant_context(session, tenant_b)
        await session.execute(
            text("INSERT INTO resources (id, tenant_id, name) VALUES (:i, :t, 'Puesto 2')"),
            {"i": puesto_nuevo, "t": tenant_b},
        )
        # Mismo horario que A (y que el otro turno de B), en otro puesto de B: entra.
        await session.execute(
            text(_insert_turno(columnas)),
            {
                "nuevo": uuid.uuid4(),
                "codigo": "TUR-B-2",
                "puesto": puesto_nuevo,
                "molde": filas_b["bookings"],
            },
        )

    # En el puesto propio de B, mismo horario: el EXCLUDE está vivo y choca con B, no con A.
    async with pg_session_factory() as session:
        with pytest.raises(IntegrityError, match="exclusion constraint") as choque:
            async with session.begin():
                await set_tenant_context(session, tenant_b)
                await session.execute(
                    text(_insert_turno(columnas)),
                    {
                        "nuevo": uuid.uuid4(),
                        "codigo": "TUR-B-3",
                        "puesto": filas_b["resources"],
                        "molde": filas_b["bookings"],
                    },
                )
    # El detalle del error nombra la fila con la que choca: tiene que ser de B.
    assert str(filas_a["resources"]) not in str(choque.value)
    assert str(tenant_a) not in str(choque.value)

    # En el puesto de A: ni siquiera llega al EXCLUDE, lo corta la FK compuesta.
    async with pg_session_factory() as session:
        with pytest.raises(IntegrityError, match="foreign key"):
            async with session.begin():
                await set_tenant_context(session, tenant_b)
                await session.execute(
                    text(_insert_turno(columnas)),
                    {
                        "nuevo": uuid.uuid4(),
                        "codigo": "TUR-B-4",
                        "puesto": filas_a["resources"],
                        "molde": filas_b["bookings"],
                    },
                )


# ── 5 · Otras puertas ─────────────────────────────────────────────────────────


async def test_copy_to_solo_exporta_lo_propio_y_copy_from_se_rechaza(pg_engine, dos_lavaderos):
    tenant_a, filas_a, tenant_b, filas_b = dos_lavaderos
    async with pg_engine.connect() as conn, conn.begin():
        await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_b}'"))
        crudo = (await conn.get_raw_connection()).driver_connection
        assert crudo is not None
        for tabla in TABLAS_TENANT:
            salida = io.BytesIO()
            await crudo.copy_from_table(tabla, output=salida, format="csv")
            volcado = salida.getvalue().decode()
            assert str(tenant_a) not in volcado and str(filas_a[tabla]) not in volcado, tabla
            assert str(filas_b[tabla]) in volcado, tabla
    async with pg_engine.connect() as conn:
        with pytest.raises(Exception, match="row-level security"):
            async with conn.begin():
                await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_b}'"))
                crudo = (await conn.get_raw_connection()).driver_connection
                assert crudo is not None
                await crudo.copy_to_table(
                    "dummy_resources",
                    source=io.BytesIO(f"{uuid.uuid4()},{tenant_a},intruso\n".encode()),
                    columns=["id", "tenant_id", "name"],
                    format="csv",
                )


async def test_el_dueno_sin_contexto_no_ve_nada_salvo_la_ventana_del_login(dos_lavaderos):
    """`carwash_owner` corre las migraciones; con `FORCE` también pasa por RLS. La única
    excepción es la política `users_owner_auth_lookup` (`FOR SELECT`), que la función del login
    necesita (ver `rls.py`): el dueño lee `users`, pero no puede modificarla sin contexto."""
    _, filas_a, tenant_b, _ = dos_lavaderos
    engine = create_async_engine(owner_url().replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with engine.connect() as conn, conn.begin():
            for tabla in TABLAS_TENANT:
                total = await conn.scalar(text(f"SELECT count(*) FROM {tabla}"))
                esperado = 2 if tabla == "users" else 0
                assert total == esperado, f"{tabla}: el dueño sin contexto ve {total}"
        async with engine.connect() as conn, conn.begin():
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_b}'"))
            for tabla in ESCRIBIBLES:
                r = await conn.execute(
                    text(f"UPDATE {tabla} SET id = id WHERE id = :i"), {"i": filas_a[tabla]}
                )
                assert r.rowcount == 0, f"el dueño con contexto B modificó {tabla} de A"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("contexto", ["inexistente", "id-de-un-usuario"])
async def test_un_tenant_id_que_no_es_un_lavadero_no_abre_nada(
    pg_session_factory, pg_admin_engine, dos_lavaderos, contexto
):
    _, filas_a, _, _ = dos_lavaderos
    falso = uuid.uuid4() if contexto == "inexistente" else filas_a["users"]
    async with pg_session_factory() as session, session.begin():
        await set_tenant_context(session, falso)
        for tabla in TABLAS_TENANT:
            assert await session.scalar(text(f"SELECT count(*) FROM {tabla}")) == 0, tabla
    # Y escribir con ese "tenant" lo corta la FK a `tenants` (que no pasa por RLS).
    async with pg_session_factory() as session:
        with pytest.raises(IntegrityError, match="foreign key"):
            async with session.begin():
                await set_tenant_context(session, falso)
                await session.execute(
                    text("INSERT INTO resources (id, tenant_id, name) VALUES (:i, :t, 'x')"),
                    {"i": uuid.uuid4(), "t": falso},
                )


async def test_una_funcion_no_leakproof_en_el_where_no_ve_filas_de_a(pg_engine, dos_lavaderos):
    """Ataque clásico a RLS: una función barata en el `WHERE` que filtra por `RAISE NOTICE`.
    Postgres evalúa la política antes que toda función no-leakproof (y solo un superusuario
    puede marcar una como leakproof), así que la función nunca recibe filas de A."""
    tenant_a, filas_a, tenant_b, filas_b = dos_lavaderos
    vistos: list[str] = []
    async with pg_engine.connect() as conn, conn.begin():
        crudo = (await conn.get_raw_connection()).driver_connection
        assert crudo is not None

        def escuchar(_conexion: Any, mensaje: Any) -> None:
            vistos.append(str(mensaje.message))

        crudo.add_log_listener(escuchar)
        try:
            try:
                await conn.execute(
                    text(
                        "CREATE FUNCTION pg_temp.espiar(text) RETURNS boolean "
                        "LANGUAGE plpgsql COST 0.0000001 AS "
                        "$$ BEGIN RAISE NOTICE 'visto %', $1; RETURN true; END $$"
                    )
                )
            except DBAPIError:  # pragma: no cover  # sin TEMP no hay ataque posible
                return
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_b}'"))
            for tabla in TABLAS_TENANT:
                await conn.execute(
                    text(f"SELECT 1 FROM {tabla} AS t WHERE pg_temp.espiar(row_to_json(t)::text)")
                )
        finally:
            crudo.remove_log_listener(escuchar)
    assert vistos, "la función no se ejecutó: el test no prueba nada"
    todo = "\n".join(vistos)
    assert str(tenant_a) not in todo
    assert all(str(filas_a[t]) not in todo for t in TABLAS_TENANT)
    assert str(filas_b["customers"]) in todo


async def test_pg_stats_no_muestra_valores_de_tablas_con_rls(
    pg_session_factory, pg_admin_engine, dos_lavaderos
):
    """`pg_stats` guarda los valores más comunes de cada columna (teléfonos, patentes). Para
    tablas con RLS activo, la vista se los oculta al rol que no la saltea."""
    tenant_a, _, tenant_b, _ = dos_lavaderos
    async with pg_admin_engine.begin() as conn:
        for tabla in TABLAS_TENANT:
            await conn.execute(text(f"ANALYZE {tabla}"))
        # El testigo: las estadísticas existen y contienen datos de A.
        mcv = await conn.scalar(
            text(
                "SELECT count(*) FROM pg_stats WHERE schemaname = 'public' "
                "AND tablename = ANY(:t)"
            ),
            {"t": list(TABLAS_TENANT)},
        )
        assert mcv > 0
    async with pg_session_factory() as session, session.begin():
        await set_tenant_context(session, tenant_b)
        filas = (
            await session.execute(
                text(
                    "SELECT tablename, attname, most_common_vals::text AS mcv, "
                    "histogram_bounds::text AS hist FROM pg_stats "
                    "WHERE schemaname = 'public' AND tablename = ANY(:t)"
                ),
                {"t": list(TABLAS_TENANT)},
            )
        ).all()
    assert filas == [], json.dumps([f.tablename for f in filas])
    assert str(tenant_a) not in str(filas)
