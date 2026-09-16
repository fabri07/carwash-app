"""Idempotencia de endpoints POST por header `Idempotency-Key`.

Adaptado de Véktor (`application/services/idempotency.py`). Se conserva el
mecanismo: INSERT directo **sin SELECT previo**, confiando en el unique index para
la carrera, dentro de `guarded_savepoint`. Se cambia: tabla propia
`idempotency_keys` en vez de `operation_fingerprints` con prefijo `idem:`.

Atomicidad: el claim y la creación de la entidad comparten la transacción del
request. Si el claim entra y la creación falla, el rollback revierte también el
claim, y un reintento con la misma key puede volver a reclamarla.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.db._savepoint import (
    SavepointConflictError,
    guarded_savepoint,
    unique_violation_classifier,
)
from app.persistence.models.idempotency_key import IDEMPOTENCY_UNIQUE, IdempotencyKey

_KEY_CONFLICT = unique_violation_classifier(
    "idempotency_key",
    constraint=IDEMPOTENCY_UNIQUE,
    columns=("idempotency_keys.tenant_id", "idempotency_keys.key"),
)


async def claim_idempotency_key(
    session: AsyncSession, tenant_id: uuid.UUID, key: str, action: str
) -> bool:
    """`True` si la key es nueva (crear la entidad); `False` si es un replay (409)."""
    try:
        async with guarded_savepoint(session, _KEY_CONFLICT):
            session.add(IdempotencyKey(tenant_id=tenant_id, key=key, action=action))
    except SavepointConflictError:
        return False
    return True
