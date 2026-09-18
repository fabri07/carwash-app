"""ADR-0003 — un solo flavor de soft-delete."""

from sqlalchemy import CheckConstraint

import app.persistence.models  # noqa: F401
from app.persistence.db.base import Base

PROHIBIDAS = {"deleted_at", "is_deleted", "is_active", "archived_at"}


def test_no_existe_otro_flavor_de_soft_delete():
    encontradas = {
        f"{t.name}.{c}"
        for t in Base.metadata.tables.values()
        for c in t.c.keys()  # noqa: SIM118  # ColumnCollection no es un dict
        if c in PROHIBIDAS
    }
    assert not encontradas, f"otro flavor de soft-delete: {encontradas}"


def test_voided_at_y_void_reason_van_siempre_juntos():
    for t in Base.metadata.tables.values():
        assert ("voided_at" in t.c) == ("void_reason" in t.c), f"{t.name}: mitad del mixin"


def test_toda_tabla_anulable_tiene_su_check():
    for t in Base.metadata.tables.values():
        if "voided_at" not in t.c:
            continue
        nombres = {c.name for c in t.constraints if isinstance(c, CheckConstraint)}
        assert f"ck_{t.name}_void_coherente" in nombres, f"{t.name}: sin CHECK de coherencia"
