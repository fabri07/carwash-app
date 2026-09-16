"""Roles de usuario — ADR-0005.

Un solo Enum, en un solo lugar, en MAYÚSCULA. Referencia de qué **no** hacer:
`backend/app/domain/user.py:13-17` de Véktor, un Enum en minúsculas que no usa
nadie (`rg -c "UserRole" app/` → un solo archivo: el que lo define) y que no
incluye `SUPERADMIN`, que sí existe en producción. Un enum desconectado es peor
que ninguno.

No hay `SUPERADMIN` en la Fase 2 a propósito: el rol que cruza tenants es la
excepción más peligrosa de un SaaS multi-tenant y se diseña con su propia ADR.
"""

from enum import StrEnum

#: Nombre del tipo enum nativo en PostgreSQL. Lo comparten el modelo y la
#: migración para que no puedan divergir.
ROLE_ENUM_NAME = "role"


class Role(StrEnum):
    """Roles de la Fase 2. Agregar uno es una migración, no un literal."""

    OWNER = "OWNER"
    STAFF = "STAFF"
