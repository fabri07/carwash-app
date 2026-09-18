"""B5 · FASE-3-CONTRATO, X4 — toda FK entre tablas de tenant lleva `tenant_id`.

RLS filtra las filas que una sesión ve, pero el chequeo de integridad referencial
de Postgres **no pasa por RLS**: con una FK simple `jobs.customer_id → customers.id`,
el lavadero B podría insertar un job que apunte al cliente de A con solo conocer su
UUID. La FK compuesta `(tenant_id, customer_id) → customers(tenant_id, id)` lo hace
imposible en la base, sin depender de que el servicio se acuerde de validarlo.
"""

from sqlalchemy import ForeignKeyConstraint, Table, UniqueConstraint

import app.persistence.models  # noqa: F401
from app.persistence.db.base import Base

TABLAS = list(Base.metadata.tables.values())


def _es_de_tenant(tabla: Table) -> bool:
    return "tenant_id" in tabla.c


def _fks_entre_tenants(tabla: Table) -> list[ForeignKeyConstraint]:
    """FKs de una tabla de tenant hacia otra tabla de tenant (sin contar la de `tenants`)."""
    return [
        fk
        for fk in tabla.foreign_key_constraints
        if fk.referred_table.name != "tenants" and _es_de_tenant(fk.referred_table)
    ]


def test_toda_fk_entre_tablas_de_tenant_es_compuesta_con_tenant_id():
    culpables = []
    for tabla in filter(_es_de_tenant, TABLAS):
        for fk in _fks_entre_tenants(tabla):
            locales = [c.name for c in fk.columns]
            remotas = [e.column.name for e in fk.elements]
            if len(locales) != 2 or locales[0] != "tenant_id" or remotas != ["tenant_id", "id"]:
                culpables.append(f"{tabla.name}({', '.join(locales)}) → {fk.referred_table.name}")
    assert not culpables, f"FKs que cruzan tenants sin tenant_id: {culpables}"


def test_toda_fk_entre_tenants_es_restrict():
    culpables = [
        f"{tabla.name}.{fk.name}: ondelete={fk.ondelete}"
        for tabla in filter(_es_de_tenant, TABLAS)
        for fk in _fks_entre_tenants(tabla)
        if fk.ondelete != "RESTRICT"
    ]
    assert not culpables, culpables


def test_todo_padre_referenciado_tiene_unique_tenant_id_id():
    # Postgres exige un UNIQUE sobre las columnas referenciadas; SQLite no. Sin este
    # test, la suite rápida pasaría con un modelo que la migración no puede crear.
    padres = {
        fk.referred_table
        for tabla in filter(_es_de_tenant, TABLAS)
        for fk in _fks_entre_tenants(tabla)
    }
    sin_unique = [
        padre.name
        for padre in padres
        if not any(
            isinstance(c, UniqueConstraint)
            and [col.name for col in c.columns] == ["tenant_id", "id"]
            for c in padre.constraints
        )
    ]
    assert not sin_unique, f"padres sin UNIQUE (tenant_id, id): {sin_unique}"


def test_el_detector_ve_una_fk_simple_hacia_otro_tenant():
    from sqlalchemy import Column, ForeignKey, MetaData, Uuid  # noqa: PLC0415

    md = MetaData()
    Table("tenants", md, Column("id", Uuid, primary_key=True))
    Table(
        "padres",
        md,
        Column("id", Uuid, primary_key=True),
        Column("tenant_id", Uuid, ForeignKey("tenants.id")),
    )
    hija = Table(
        "hijas",
        md,
        Column("id", Uuid, primary_key=True),
        Column("tenant_id", Uuid, ForeignKey("tenants.id")),
        Column("padre_id", Uuid, ForeignKey("padres.id")),
    )
    fks = _fks_entre_tenants(hija)
    assert len(fks) == 1
    assert [c.name for c in fks[0].columns] == ["padre_id"]  # simple: el test de arriba la marcaría
