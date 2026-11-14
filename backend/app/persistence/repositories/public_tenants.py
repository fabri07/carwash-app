"""El negocio de una URL pública `/<slug>` — FASE-5-CONTRATO §3.7, Z7 y Z8 (PR 5.2).

No es un `*Repository`: es la única búsqueda **sin tenant** del turnero, como
`UserRepository.find_login_candidate` lo es del login. En Postgres pasa por la función
`SECURITY DEFINER` `public_tenant_by_slug` (el rol de runtime no ve `tenants` sin contexto); en
SQLite (suite rápida, sin RLS) es el `SELECT` equivalente.

Devuelve **solo el id** del negocio vivo con ese slug y el alta terminada. Inexistente, anulado o a
medio configurar son el mismo `None` (Z8).
"""

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.slug import SLUG_MAX
from app.persistence.db.rls import PUBLIC_TENANT_LOOKUP_FUNCTION
from app.persistence.db.tenant_context import is_postgres
from app.persistence.models.tenant import Tenant, TenantSettings


async def find_public_tenant_id(session: AsyncSession, slug: str) -> uuid.UUID | None:
    cleaned = (slug or "").strip().lower()
    if not cleaned or len(cleaned) > SLUG_MAX:
        return None  # no puede ser un slug: ni se consulta
    if is_postgres(session):
        found = await session.scalar(
            text(f"SELECT {PUBLIC_TENANT_LOOKUP_FUNCTION}(:slug)"), {"slug": cleaned}
        )
        return uuid.UUID(str(found)) if found is not None else None
    found_id: uuid.UUID | None = await session.scalar(
        select(Tenant.id)
        .join(TenantSettings, TenantSettings.tenant_id == Tenant.id)
        .where(
            func.lower(Tenant.slug) == cleaned,
            Tenant.voided_at.is_(None),
            TenantSettings.onboarding_completed_at.is_not(None),
        )
        .limit(1)
    )
    return found_id
