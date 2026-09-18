"""ADR-0004 — la PK se llama `id`."""

import inspect

import app.persistence.models  # noqa: F401
from app.persistence.db.base import Base

TABLAS_PUENTE: set[str] = set()  # vacío en Fase 2. Agregar acá es un acto deliberado.


def test_toda_tabla_entidad_tiene_pk_id():
    for t in Base.metadata.tables.values():
        if t.name in TABLAS_PUENTE:
            continue
        cols = tuple(c.name for c in t.primary_key.columns)
        assert cols == ("id",), f"{t.name}: PK {cols}, se esperaba ('id',)"


def test_tenant_id_nunca_es_pk():
    for t in Base.metadata.tables.values():
        assert "tenant_id" not in {c.name for c in t.primary_key.columns}, t.name


def test_las_tablas_puente_declaran_pk_compuesta_y_no_tienen_columnas_propias():
    for nombre in TABLAS_PUENTE:  # lista vacía en Fase 2
        t = Base.metadata.tables[nombre]
        assert len(t.primary_key.columns) == 2
        propias = set(t.c.keys()) - {c.name for c in t.primary_key.columns}
        assert not propias - {"tenant_id", "created_at", "updated_at"}


def test_ningun_modelo_declara_su_propia_pk():
    # Igual que en test_modelo_tenant: `vars()` de una clase mapeada incluye los
    # atributos instrumentados heredados, así que se mira el fuente de la clase.
    culpables = [
        m.class_.__name__
        for m in Base.registry.mappers
        if "primary_key" in inspect.getsource(m.class_)
    ]
    assert not culpables, f"declaran primary_key= a mano en vez de usar el mixin: {culpables}"
