"""Contexto de tenant para RLS — ADR-0002.

Portado de Véktor (`app/application/db/tenant_context.py`) con dos correcciones:

1. **`SET LOCAL`, nunca `SET` a secas.** `SET app.current_tenant_id` es de
   *sesión*: el valor sobrevive al request y la conexión vuelve al pool marcada
   con el tenant anterior. Véktor lo documenta en
   `migrations/versions/20260806_0003_audit_log_tenant_nullable.py:27-34`. Con
   `LOCAL` el valor muere con la transacción.
2. **Acá sí se llama.** En Véktor `rg set_tenant_context` solo encuentra su
   docstring y un comentario. Acá lo llama `session.get_db_session` en cada
   request autenticado, y los endpoints de auth que crean o buscan identidad.

`SET LOCAL` no acepta bind params en PostgreSQL; el valor se interpola desde un
`uuid.UUID` ya parseado (el tipo garantiza que no hay nada que inyectar). En
SQLite (suite rápida) no hay RLS y esta función es no-op.

No hay otro parámetro de sesión que abra datos: la búsqueda de identidad del login
es la función `auth_lookup_user` (ver `persistence/db/rls.py`).
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Parámetro de sesión que leen las políticas RLS.
TENANT_SETTING = "app.tenant_id"


def is_postgres(session: AsyncSession) -> bool:
    return session.get_bind().dialect.name == "postgresql"


async def set_tenant_context(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Emite `SET LOCAL app.tenant_id` dentro de la transacción en curso."""
    if not isinstance(tenant_id, uuid.UUID):  # defensa: nunca interpolar otra cosa
        raise TypeError("tenant_id debe ser uuid.UUID")
    if not is_postgres(session):
        return
    await session.execute(text(f"SET LOCAL {TENANT_SETTING} = '{tenant_id}'"))
