"""`guarded_savepoint`: el orden flush-fuera / savepoint / flush-adentro."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.persistence.db._savepoint import (
    SavepointConflictError,
    guarded_savepoint,
    unique_violation_classifier,
)
from app.persistence.models import IdempotencyKey, Tenant

CLASSIFY = unique_violation_classifier(
    "idem",
    constraint="uq_idempotency_keys_tenant_id_key",
    columns=("idempotency_keys.tenant_id", "idempotency_keys.key"),
)


async def test_conflicto_vigilado_deja_la_transaccion_usable(db_session, tenant_a):
    db_session.add(IdempotencyKey(tenant_id=tenant_a.id, key="k", action="a"))
    await db_session.flush()

    with pytest.raises(SavepointConflictError) as info:
        async with guarded_savepoint(db_session, CLASSIFY):
            db_session.add(IdempotencyKey(tenant_id=tenant_a.id, key="k", action="a"))
    assert info.value.constraint == "idem"
    assert isinstance(info.value.original, IntegrityError)

    # La transacción externa sigue viva: se puede consultar y escribir.
    total = (await db_session.execute(select(IdempotencyKey))).scalars().all()
    assert len(total) == 1


async def test_lo_pendiente_se_drena_fuera_del_savepoint(db_session, tenant_a):
    # Un objeto agregado ANTES del bloque se flushea antes del SAVEPOINT: un rollback
    # del savepoint no se lo lleva.
    ajeno = Tenant(id=uuid.uuid4(), name="pendiente")
    db_session.add(ajeno)
    db_session.add(IdempotencyKey(tenant_id=tenant_a.id, key="dup", action="a"))
    await db_session.flush()

    with pytest.raises(SavepointConflictError):
        async with guarded_savepoint(db_session, CLASSIFY):
            db_session.add(IdempotencyKey(tenant_id=tenant_a.id, key="dup", action="a"))

    assert await db_session.get(Tenant, ajeno.id) is not None


async def test_violacion_no_vigilada_se_repropaga_intacta(db_session, tenant_a):
    # NOT NULL roto: no es el unique vigilado → IntegrityError, nunca "ya existía".
    with pytest.raises(IntegrityError):
        async with guarded_savepoint(db_session, CLASSIFY):
            db_session.add(IdempotencyKey(tenant_id=tenant_a.id, key="x", action=None))


class _Orig:
    def __init__(self, constraint_name=None, text=""):
        self.constraint_name = constraint_name
        self._text = text

    def __str__(self):
        return self._text


def _err(orig) -> IntegrityError:
    return IntegrityError("stmt", {}, orig)


def test_clasificador_postgres_por_nombre_de_constraint():
    assert CLASSIFY(_err(_Orig("uq_idempotency_keys_tenant_id_key"))) == "idem"
    assert CLASSIFY(_err(_Orig("fk_otra_cosa"))) is None


def test_clasificador_sqlite_por_columnas():
    msg = "UNIQUE constraint failed: idempotency_keys.tenant_id, idempotency_keys.key"
    assert CLASSIFY(_err(_Orig(text=msg))) == "idem"
    assert CLASSIFY(_err(_Orig(text="UNIQUE constraint failed: users.email"))) is None
    assert CLASSIFY(_err(_Orig(text="NOT NULL constraint failed: x.y"))) is None


def test_clasificador_por_nombre_en_el_texto():
    assert CLASSIFY(_err(_Orig(text="duplicate uq_idempotency_keys_tenant_id_key"))) == "idem"
