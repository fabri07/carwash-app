"""ADR-0001 — `TenantMixin` es la única forma de tener `tenant_id`."""

import inspect
import re

import app.persistence.models  # noqa: F401
from app.persistence.db.base import Base

# Tablas sin columna `tenant_id`. Editar esto es un acto deliberado. `tenants` sigue acá
# porque no puede tener `tenant_id` (ES el tenant), pero ya NO está exenta de RLS: se aísla
# por `id` (L3) y `test_rls_politicas_pg.py` lo verifica. `users` sí usa TenantMixin.
TABLAS_SIN_TENANT = {"tenants", "alembic_version"}


def test_toda_tabla_es_tenant_scoped():
    faltan = {t.name for t in Base.metadata.tables.values() if "tenant_id" not in t.c}
    assert not (faltan - TABLAS_SIN_TENANT), f"tablas sin aislamiento: {faltan}"


def _declara_en_su_cuerpo(cls: type, nombre: str) -> bool:
    # `vars(cls)` no sirve (el ejemplo literal de la ADR): SQLAlchemy instala un
    # `InstrumentedAttribute` por columna en CADA clase mapeada, así que `tenant_id`
    # aparece en `vars()` de todos los modelos aunque venga del mixin. Se mira el
    # cuerpo de la clase: anotación propia o asignación en el fuente.
    if nombre in vars(cls).get("__annotations__", {}):
        return True
    fuente = inspect.getsource(cls)
    return re.search(rf"^\s+{nombre}\s*[:=]", fuente, re.MULTILINE) is not None


def test_ningun_modelo_redeclara_tenant_id():
    culpables = [
        m.class_.__name__
        for m in Base.registry.mappers
        if _declara_en_su_cuerpo(m.class_, "tenant_id")
    ]
    assert not culpables, f"redeclaran tenant_id en vez de usar TenantMixin: {culpables}"


def test_la_columna_tenant_id_es_identica_en_todas_las_tablas():
    for t in Base.metadata.tables.values():
        if (col := t.c.get("tenant_id")) is None:
            continue
        assert col.nullable is False, f"{t.name}.tenant_id es nullable"
        assert col.index is True, f"{t.name}.tenant_id sin índice"
        # `tenant_id` también es la primera columna de las FKs compuestas (FASE-3, X4):
        # esas las verifica `test_fks_compuestas.py`. Acá, la que va a `tenants`: una sola.
        fks = [fk for fk in col.foreign_keys if fk.column.table.name == "tenants"]
        assert len(fks) == 1, f"{t.name}.tenant_id sin FK a tenants"
        assert len(fks[0].constraint.columns) == 1, f"{t.name}: la FK a tenants es compuesta"
        assert fks[0].ondelete == "RESTRICT", f"{t.name}.tenant_id con ondelete={fks[0].ondelete}"
        otras = [fk for fk in col.foreign_keys if fk.column.table.name != "tenants"]
        assert all(len(fk.constraint.columns) == 2 for fk in otras), f"{t.name}: FK simple"


def test_el_detector_de_redeclaracion_funciona():
    class Culpable:
        tenant_id: int = 0

    class Limpia:
        name: str = ""

    assert _declara_en_su_cuerpo(Culpable, "tenant_id")
    assert not _declara_en_su_cuerpo(Limpia, "tenant_id")
