"""T3b #2 — los nombres de constraints e índices en Postgres son los del ORM.

`alembic check` no compara CHECKs ni nombres: una `naming_convention` aplicada dos
veces (`ck_users_ck_users_void_coherente`) pasaba en verde. Este test mira
`pg_constraint` y `pg_indexes` de la base migrada contra `Base.metadata`.
"""

import pytest
from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    UniqueConstraint,
    text,
)

import app.persistence.models  # noqa: F401
from app.persistence.db.base import Base

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

_TIPOS = {
    PrimaryKeyConstraint: "p",
    UniqueConstraint: "u",
    ForeignKeyConstraint: "f",
    CheckConstraint: "c",
}


def _esperados() -> set[tuple[str, str, str]]:
    esperados = set()
    for tabla in Base.metadata.sorted_tables:
        for constraint in tabla.constraints:
            tipo = next(v for k, v in _TIPOS.items() if isinstance(constraint, k))
            esperados.add((tabla.name, tipo, str(constraint.name)))
    return esperados


async def test_constraints_de_postgres_coinciden_con_el_orm(pg_admin_engine):
    tablas = [t.name for t in Base.metadata.sorted_tables]
    async with pg_admin_engine.connect() as conn:
        filas = (
            await conn.execute(
                text(
                    "SELECT rel.relname AS tabla, con.contype::text AS tipo, con.conname AS nombre "
                    "FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid "
                    "JOIN pg_namespace n ON n.oid = rel.relnamespace "
                    "WHERE n.nspname = 'public' AND rel.relname = ANY(:tablas) "
                    "AND con.contype IN ('p', 'u', 'f', 'c')"
                ),
                {"tablas": tablas},
            )
        ).all()
    reales = {(f.tabla, f.tipo, f.nombre) for f in filas}
    esperados = _esperados()
    assert (
        reales == esperados
    ), f"sobran en Postgres: {reales - esperados}; faltan: {esperados - reales}"
    # el caso concreto del hallazgo
    assert not any(n.count("ck_") > 1 for _, _, n in reales)


async def test_indices_de_postgres_coinciden_con_el_orm(pg_admin_engine):
    esperados = {(t.name, str(i.name)) for t in Base.metadata.sorted_tables for i in t.indexes}
    async with pg_admin_engine.connect() as conn:
        filas = (
            await conn.execute(
                text(
                    "SELECT i.tablename, i.indexname FROM pg_indexes i "
                    "LEFT JOIN pg_constraint c ON c.conname = i.indexname "
                    "WHERE i.schemaname = 'public' AND c.oid IS NULL "
                    "AND i.tablename <> 'alembic_version'"
                )
            )
        ).all()
    assert {(f.tablename, f.indexname) for f in filas} == esperados
