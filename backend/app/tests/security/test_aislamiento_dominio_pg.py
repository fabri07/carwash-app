"""B7 + B14 · FASE-3-CONTRATO — aislamiento entre lavaderos, tabla por tabla, contra Postgres real.

Todo lo que se afirma se afirma con el rol de runtime `carwash_app` (sin BYPASSRLS),
con el contexto de tenant puesto por `set_tenant_context`, igual que en un request.

El test es genérico: sale de `Base.metadata`. Lo único específico de cada tabla es
cómo insertar una fila válida, y eso vive en `_poblar_dominio.py` (lo escribe el
Tester-aislamiento de T3, que no implementó el esquema). Si alguien agrega una tabla
de tenant y no la puebla, `test_el_poblador_cubre_todas_las_tablas` falla: una tabla
nueva no puede quedar fuera del aislamiento sin que se note.

Cuatro intentos de cruzar, por tabla:
  1. leer una fila de A por id,
  2. modificarla (0 filas afectadas, sin error: RLS la vuelve invisible),
  3. insertar una fila con el `tenant_id` de A (WITH CHECK la rechaza),
  4. hacer que una fila de B apunte a un padre de A (la FK compuesta la rechaza).
"""

import uuid

import pytest
from sqlalchemy import ForeignKeyConstraint, Table, text
from sqlalchemy.exc import DBAPIError, IntegrityError

import app.persistence.models  # noqa: F401
from app.persistence.db.base import Base
from app.persistence.db.tenant_context import set_tenant_context
from app.tests.security._poblar_dominio import poblar

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

TABLAS_TENANT: dict[str, Table] = {
    t.name: t for t in Base.metadata.sorted_tables if "tenant_id" in t.c
}
#: Append-only (X8): no se puede hacer UPDATE, así que el intento 4 va por INSERT.
APPEND_ONLY = {"job_events"}


def _fks_entre_tenants(tabla: Table) -> list[ForeignKeyConstraint]:
    return [
        fk
        for fk in tabla.foreign_key_constraints
        if fk.referred_table.name != "tenants" and "tenant_id" in fk.referred_table.c
    ]


CASOS_FK = [
    pytest.param(
        tabla.name,
        fk.columns[1].name,
        fk.referred_table.name,
        id=f"{tabla.name}.{fk.columns[1].name}",
    )
    for tabla in TABLAS_TENANT.values()
    for fk in _fks_entre_tenants(tabla)
    if len(fk.columns) == 2
]


@pytest.fixture
async def dos_lavaderos(pg_admin_engine, pg_tenant_a, pg_tenant_b):
    """Una fila en CADA tabla de tenant para A y para B. Devuelve los ids por tabla."""
    filas_a = await poblar(pg_admin_engine, pg_tenant_a)
    filas_b = await poblar(pg_admin_engine, pg_tenant_b)
    return pg_tenant_a, filas_a, pg_tenant_b, filas_b


async def test_el_poblador_cubre_todas_las_tablas(dos_lavaderos):
    _, filas_a, _, filas_b = dos_lavaderos
    faltan, sobran = set(TABLAS_TENANT) - set(filas_a), set(filas_a) - set(TABLAS_TENANT)
    assert not faltan and not sobran, f"sin poblar: {faltan}; de más: {sobran}"
    assert set(filas_b) == set(TABLAS_TENANT)


async def test_el_lavadero_b_no_ve_ninguna_fila_de_a(pg_session_factory, dos_lavaderos):
    """B14 — el test del checkpoint: en NINGÚN recurso."""
    tenant_a, _, tenant_b, _ = dos_lavaderos
    async with pg_session_factory() as session, session.begin():
        await set_tenant_context(session, tenant_b)
        for tabla in TABLAS_TENANT:
            de_a = await session.scalar(
                text(f"SELECT count(*) FROM {tabla} WHERE tenant_id = :a"), {"a": tenant_a}
            )
            total = await session.scalar(text(f"SELECT count(*) FROM {tabla}"))
            propias = await session.scalar(
                text(f"SELECT count(*) FROM {tabla} WHERE tenant_id = :b"), {"b": tenant_b}
            )
            assert de_a == 0, f"{tabla}: B ve {de_a} filas de A"
            assert total == propias >= 1, f"{tabla}: total {total}, propias {propias}"


@pytest.mark.parametrize("tabla", sorted(TABLAS_TENANT))
async def test_b_no_lee_ni_modifica_la_fila_de_a(pg_session_factory, dos_lavaderos, tabla):
    _, filas_a, tenant_b, _ = dos_lavaderos
    async with pg_session_factory() as session, session.begin():
        await set_tenant_context(session, tenant_b)
        leida = await session.scalar(
            text(f"SELECT count(*) FROM {tabla} WHERE id = :id"), {"id": filas_a[tabla]}
        )
        assert leida == 0
        if tabla in APPEND_ONLY:
            return  # sin permiso de UPDATE: lo prueba el test de abajo
        # `SET id = id`: vale para toda tabla, también las que no tienen updated_at.
        tocadas = await session.execute(
            text(f"UPDATE {tabla} SET id = id WHERE id = :id"), {"id": filas_a[tabla]}
        )
        assert tocadas.rowcount == 0


@pytest.mark.parametrize("tabla", sorted(APPEND_ONLY))
async def test_el_runtime_no_puede_modificar_una_tabla_append_only(
    pg_session_factory, dos_lavaderos, tabla
):
    # Más fuerte que "0 filas": `carwash_app` no tiene UPDATE ni DELETE (X8), ni sobre
    # las filas propias. El trigger es la segunda red, para el dueño.
    _, _, tenant_b, filas_b = dos_lavaderos
    for sentencia in (f"UPDATE {tabla} SET id = id", f"DELETE FROM {tabla}"):
        async with pg_session_factory() as session:
            with pytest.raises(DBAPIError, match="permission denied"):
                async with session.begin():
                    await set_tenant_context(session, tenant_b)
                    await session.execute(
                        text(f"{sentencia} WHERE id = :id"), {"id": filas_b[tabla]}
                    )


@pytest.mark.parametrize("tabla", sorted(TABLAS_TENANT))
async def test_b_no_inserta_con_el_tenant_de_a(pg_session_factory, dos_lavaderos, tabla):
    tenant_a, _, tenant_b, filas_b = dos_lavaderos
    columnas = [c.name for c in TABLAS_TENANT[tabla].c]
    # Copia la fila propia de B cambiando solo id y tenant_id: todo lo demás es válido,
    # así que lo único que puede rechazarla es la política.
    select = ", ".join(
        "CAST(:nuevo AS uuid)" if c == "id" else "CAST(:a AS uuid)" if c == "tenant_id" else c
        for c in columnas
    )
    async with pg_session_factory() as session:
        with pytest.raises(DBAPIError, match="row-level security"):
            async with session.begin():
                await set_tenant_context(session, tenant_b)
                await session.execute(
                    text(
                        f"INSERT INTO {tabla} ({', '.join(columnas)}) "
                        f"SELECT {select} FROM {tabla} WHERE id = :fila_b"
                    ),
                    {"nuevo": uuid.uuid4(), "a": tenant_a, "fila_b": filas_b[tabla]},
                )


@pytest.mark.parametrize(("tabla", "columna", "padre"), CASOS_FK)
async def test_una_fila_de_b_no_apunta_a_un_padre_de_a(
    pg_session_factory, dos_lavaderos, tabla, columna, padre
):
    _, filas_a, tenant_b, filas_b = dos_lavaderos
    async with pg_session_factory() as session:
        with pytest.raises(IntegrityError, match="foreign key"):
            async with session.begin():
                await set_tenant_context(session, tenant_b)
                if tabla in APPEND_ONLY:
                    columnas = [c.name for c in TABLAS_TENANT[tabla].c]
                    reemplazos = {
                        "id": "CAST(:nuevo AS uuid)",
                        "idempotency_key": "CAST(:clave AS text)",
                        columna: "CAST(:padre_a AS uuid)",
                    }
                    select = ", ".join(reemplazos.get(c, c) for c in columnas)
                    await session.execute(
                        text(
                            f"INSERT INTO {tabla} ({', '.join(columnas)}) "
                            f"SELECT {select} FROM {tabla} WHERE id = :fila_b"
                        ),
                        {
                            "nuevo": uuid.uuid4(),
                            "clave": f"test-{uuid.uuid4()}",
                            "padre_a": filas_a[padre],
                            "fila_b": filas_b[tabla],
                        },
                    )
                else:
                    await session.execute(
                        text(f"UPDATE {tabla} SET {columna} = :padre_a WHERE id = :fila_b"),
                        {"padre_a": filas_a[padre], "fila_b": filas_b[tabla]},
                    )


def test_hay_casos_de_fk_para_cada_tabla_hija():
    # Si la parametrización quedara vacía (p. ej. porque las FKs no son compuestas),
    # el test de arriba pasaría sin probar nada.
    hijas = {tabla.name for tabla in TABLAS_TENANT.values() if _fks_entre_tenants(tabla)}
    assert hijas, "no hay FKs entre tablas de tenant: ¿se migró el dominio?"
    assert {c.values[0] for c in CASOS_FK} == hijas
