"""Piezas comunes de los servicios de aplicación — FASE-3-CONTRATO §4.

Todo servicio se construye con `(session, tenant_id, actor_user_id)`:

- el **tenant** viene del contexto de auth del llamador (el token verificado), nunca de un
  campo de un DTO. Por eso ningún método público recibe `tenant_id`;
- la **transacción** es del llamador: el servicio hace `flush`, nunca `commit`. Ante
  cualquier excepción el llamador hace rollback (así funciona `get_db_session`);
- el servicio **re-fija** `app.tenant_id` (`SET LOCAL`) una vez por transacción. La
  dependency de F2 ya lo hace; esto es la defensa para quien use el servicio fuera de un
  request (el seed de staging, un job de F5).
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import SessionTransaction

from app.application.services.errors import NotFoundError
from app.domain.delay import require_aware
from app.domain.exceptions import GuardFailedError
from app.persistence.db._savepoint import unique_violation_classifier
from app.persistence.db.base import TenantScopedModel
from app.persistence.db.tenant_context import set_tenant_context
from app.persistence.repositories.base import BaseRepository

Classifier = Callable[[IntegrityError], str | None]


def first_match(*classifiers: Classifier) -> Classifier:
    """Combina clasificadores de `_savepoint`: gana el primero que reconoce la violación."""

    def _classify(exc: IntegrityError) -> str | None:
        for classify in classifiers:
            label = classify(exc)
            if label is not None:
                return label
        return None

    return _classify


def alive_unique(table: str, *columns: str) -> Classifier:
    """Clasificador del único "entre vivos" `ux_<tabla>_tenant_id_<cols>` (X7).

    El nombre es el que arma `models._constraints.unique_alive`; SQLite reporta las columnas.
    """
    name = f"ux_{table}_tenant_id_{'_'.join(columns)}"
    return unique_violation_classifier(
        name, constraint=name, columns=(f"{table}.tenant_id", *(f"{table}.{c}" for c in columns))
    )


def as_aware(value: datetime) -> datetime:
    """Instante con zona. Postgres (asyncpg) devuelve `timestamptz` siempre con zona; SQLite
    (suite rápida) la pierde al releer una fila, y lo que guardamos ahí es UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def require_key(idempotency_key: str) -> str:
    """La clave de idempotencia es obligatoria y la pone el llamador (la cola offline)."""
    cleaned = (idempotency_key or "").strip()
    if not cleaned:
        raise GuardFailedError("an idempotency key is required")
    return cleaned


def require_instant(value: datetime) -> datetime:
    """`occurred_at` y compañía: del cliente, con zona (X10)."""
    return require_aware(value)


class ServiceBase:
    def __init__(
        self, session: AsyncSession, tenant_id: uuid.UUID, actor_user_id: uuid.UUID | None
    ) -> None:
        if not isinstance(tenant_id, uuid.UUID):  # defensa: nunca un str de un DTO
            raise TypeError("tenant_id must be uuid.UUID")
        self._session = session
        self._tenant_id = tenant_id
        self._actor_user_id = actor_user_id
        self._context_tx: SessionTransaction | None = None

    @property
    def tenant_id(self) -> uuid.UUID:
        return self._tenant_id

    async def _enter(self) -> None:
        """`SET LOCAL app.tenant_id` una vez por transacción (muere con ella)."""
        current = self._session.sync_session.get_transaction()
        if current is not None and current is self._context_tx:
            return
        await set_tenant_context(self._session, self._tenant_id)
        self._context_tx = self._session.sync_session.get_transaction()

    def _actor(self) -> uuid.UUID:
        """El actor identificado (D-001.4). Solo una cancelación desde la web va sin actor."""
        if self._actor_user_id is None:
            raise GuardFailedError("this operation needs an identified actor")
        return self._actor_user_id

    async def _require[M: TenantScopedModel](
        self,
        repo: BaseRepository[M],
        id: uuid.UUID,
        *,
        include_voided: bool = False,
    ) -> M:
        entity = await repo.get_by_id(id, self._tenant_id, include_voided=include_voided)
        if entity is None:
            raise NotFoundError(repo.model.__tablename__, id)
        return entity

    async def _lock[M: TenantScopedModel](self, repo: BaseRepository[M], id: uuid.UUID) -> M:
        entity = await repo.get_for_update(id, self._tenant_id)
        if entity is None:
            raise NotFoundError(repo.model.__tablename__, id)
        return entity
