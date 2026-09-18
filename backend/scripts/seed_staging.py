#!/usr/bin/env python3
"""Seed SINTÉTICO de staging — ADR-0012, punto 3.

Crea dos tenants de mentira, cada uno con un OWNER, un STAFF y recursos dummy.
Todo es inventado: emails bajo el dominio reservado `.invalid` (RFC 2606), sin
nombres de personas, sin teléfonos, sin patentes.

**Prohibido** alimentar staging con datos de producción, ni restaurando un
volcado ni copiando filas: nombres, teléfonos y patentes son PII bajo la
Ley 25.326, y la patente es el PII fuerte de este dominio. Si staging necesita
más datos, se agregan ACÁ, inventados. El seed es la fuente de staging; lo que
alguien cargue a mano se puede perder en cualquier reset.

Idempotente: si el tenant ya existe (se busca por nombre) no se duplica, pero la
contraseña de sus usuarios se re-aplica desde `SEED_STAGING_PASSWORD` (obligatoria).
Lo corre `scripts/migrate.sh` solo cuando `APP_ENV=staging`, después de
`alembic upgrade head`, con `DATABASE_URL` (rol `carwash_app`, RLS activo).
Se niega a correr con cualquier otro `APP_ENV`.

Cobertura del esquema: `app/tests/scripts/test_seed_staging.py` exige al menos
una fila en toda tabla con `tenant_id`. Cuando una fase agregue una tabla de
negocio, este archivo la tiene que poblar o ese test se pone rojo.

Uso local (contra el Postgres de docker compose):
    SEED_STAGING_PASSWORD=... make seed-staging
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.domain.roles import Role  # noqa: E402
from app.persistence.models.dummy_resource import DummyResource  # noqa: E402
from app.persistence.models.idempotency_key import IdempotencyKey  # noqa: E402
from app.persistence.models.tenant import Tenant  # noqa: E402
from app.persistence.models.user import User  # noqa: E402
from app.utils.security import hash_password  # noqa: E402

#: Nombre de la variable con la contraseña de los usuarios sintéticos. NO hay
#: default: una contraseña versionada en el repo es una contraseña pública, y
#: staging es alcanzable desde internet. Se define en el panel de Railway.
PASSWORD_ENV = "SEED_STAGING_PASSWORD"

TENANTS: list[dict[str, object]] = [
    {
        "name": "Lavadero Demo Norte",
        "owner": "owner@norte.staging.invalid",
        "staff": "staff@norte.staging.invalid",
        "recursos": ["Recurso de prueba A", "Recurso de prueba B"],
    },
    {
        "name": "Lavadero Demo Sur",
        "owner": "owner@sur.staging.invalid",
        "staff": "staff@sur.staging.invalid",
        "recursos": ["Recurso de prueba C"],
    },
]


def _p(msg: str) -> None:
    print(f"[seed-staging] {msg}", flush=True)


def _async_url(raw: str) -> str:
    """El engine async exige `postgresql+asyncpg://`; Railway entrega `postgresql://`."""
    for prefix in ("postgresql+psycopg2://", "postgresql://", "postgres://"):
        if raw.startswith(prefix):
            return "postgresql+asyncpg://" + raw[len(prefix) :]
    return raw


async def _set_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    # Con RLS forzado (ADR-0002) el WITH CHECK rechaza escrituras y el USING oculta
    # filas si el contexto no está puesto. `true` = LOCAL a la transacción.
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
    )


async def _seed_tenant(session: AsyncSession, spec: dict[str, object], pw_hash: str) -> None:
    emails = {str(spec["owner"]): Role.OWNER, str(spec["staff"]): Role.STAFF}
    owner_email = str(spec["owner"])
    # El seed corre como carwash_app, con RLS forzado en TODAS las tablas —
    # `tenants` incluida—, así que sin contexto no ve nada: buscar por nombre o por
    # email devolvería siempre 0 filas y duplicaría. Se usa lo mismo que el login:
    # la función SECURITY DEFINER `auth_lookup_user(email)`, que devuelve el
    # tenant_id del usuario sin abrir la tabla al rol de runtime.
    existing = await session.scalar(
        text("SELECT tenant_id FROM auth_lookup_user(:email)"), {"email": owner_email}
    )
    if existing is not None:
        # Idempotente, pero no congelado: la contraseña se re-aplica en CADA
        # corrida. Rotar SEED_STAGING_PASSWORD en Railway + redeploy = rotada.
        await _set_tenant(session, existing)
        users = (
            await session.scalars(
                select(User).where(User.tenant_id == existing, User.email.in_(list(emails)))
            )
        ).all()
        for user in users:
            user.password_hash = pw_hash
        await session.flush()
        _p(f"{spec['name']}: ya existe; contraseña actualizada en {len(users)} usuarios")
        return

    # El contexto va ANTES del INSERT del tenant: la política de `tenants` es
    # `id = app.tenant_id` también en el WITH CHECK.
    tenant_id = uuid.uuid4()
    await _set_tenant(session, tenant_id)
    tenant = Tenant(id=tenant_id, name=str(spec["name"]))
    session.add(tenant)
    await session.flush()

    session.add_all(
        User(tenant_id=tenant.id, email=email, password_hash=pw_hash, role=role)
        for email, role in emails.items()
    )
    recursos = spec["recursos"]
    assert isinstance(recursos, list)
    session.add_all(DummyResource(tenant_id=tenant.id, name=str(n)) for n in recursos)
    # Una clave de idempotencia de ejemplo: el test de cobertura del seed exige
    # al menos una fila en TODA tabla con tenant_id, y esta también lo es.
    session.add(IdempotencyKey(tenant_id=tenant.id, key=f"seed-{tenant.id}", action="seed"))
    await session.flush()
    _p(f"{spec['name']}: creado (2 usuarios, {len(recursos)} recursos)")


async def main() -> int:
    env = os.environ.get("APP_ENV", "")
    if env != "staging":
        _p(f"APP_ENV={env!r}: este seed solo corre en staging. No se hace nada.")
        return 1 if env == "production" else 0

    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        _p("falta DATABASE_URL")
        return 1

    password = os.environ.get(PASSWORD_ENV, "")
    if len(password) < 12:
        _p(f"falta {PASSWORD_ENV} (o tiene menos de 12 caracteres): no hay contraseña por defecto")
        return 1
    pw_hash = hash_password(password)

    engine = create_async_engine(_async_url(raw))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        for spec in TENANTS:
            # Una transacción por tenant: `set_config(..., true)` es LOCAL y no
            # puede sobrevivir de un tenant al siguiente.
            async with factory() as session, session.begin():
                await _seed_tenant(session, spec, pw_hash)
    finally:
        await engine.dispose()
    _p("OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
