#!/usr/bin/env python3
"""Prepara staging para la migración 0005 (FASE-4-CONTRATO §3.2) — PR 4.2.0.

La 0005 pasa `price_cents` a NOT NULL en `service_prices` y `booking_items` y **corta antes
de alterar nada** si queda algún NULL. El seed sintético de staging los deja a propósito
(el servicio a cotizar no tenía precio base; sus ítems de turno tampoco). Este script los
completa para que 0005 pase:

- `service_prices.price_cents IS NULL` → precio base sintético (D4-6: todo servicio tiene
  precio base por tamaño; `A_COTIZAR` = "desde $X").
- `booking_items.price_cents IS NULL` → el precio acordado de la cotización `ACEPTADO` del
  mismo turno y servicio; si no hay, el precio base del servicio para el tamaño del turno.

**Idempotente**: solo toca filas NULL, así que correrlo dos veces no cambia nada la segunda.
Se niega a correr si `APP_ENV != staging`. Producción está en 0001 sin datos: no se prepara.

SQL crudo a propósito, sin importar modelos de la app: tiene que funcionar contra el
esquema 0004 y no acoplarse al código que cambia el PR 4.2a. **Es descartable**: una vez
que 0005 corrió en staging, se borra.

Por qué es un PR aparte (4.2.0): staging se despliega al mergear a `main` (ADR-0012) y la
consola de Railway corre la imagen ya desplegada. El script tiene que estar desplegado
**antes** de que entre la migración que lo necesita.

Cómo correrlo (consola de Railway, servicio de staging; no lleva secretos, usa el
`DATABASE_URL` del servicio):

    python scripts/prepare_staging_0005.py

Cubre los tenants del seed. Si staging tiene otros (alta por /registro), se pasan los
emails de sus dueños como argumentos:

    python scripts/prepare_staging_0005.py owner@otro.example
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from typing import Any, cast

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import CursorResult  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

#: Dueños de los tenants del seed (`seed_staging.TENANTS`). Se repiten acá a propósito:
#: importar el seed arrastraría todos los servicios de la app a un script descartable.
SEED_OWNERS = ("owner@norte.staging.invalid", "owner@sur.staging.invalid")

#: Precio base sintético: $ 30.000,00 en centavos. Inventado, igual que el resto del seed.
BASE_PRICE_CENTS = 3_000_000

_SQL_PRECIOS = text("UPDATE service_prices SET price_cents = :base WHERE price_cents IS NULL")

# Acordado de la cotización aceptada; si no hay, el precio base del servicio para el tamaño
# del turno (ya completado por el UPDATE anterior).
_SQL_ITEMS = text(
    """
    UPDATE booking_items bi
       SET price_cents = COALESCE(
           (SELECT q.agreed_price_cents
              FROM quotes q
             WHERE q.booking_id = bi.booking_id
               AND q.service_id = bi.service_id
               AND q.status = 'ACEPTADO'
               AND q.agreed_price_cents IS NOT NULL
             ORDER BY q.updated_at DESC
             LIMIT 1),
           (SELECT sp.price_cents
              FROM service_prices sp
              JOIN bookings b ON b.id = bi.booking_id
             WHERE sp.service_id = bi.service_id
               AND sp.vehicle_size_id = b.vehicle_size_id
               AND sp.voided_at IS NULL
             LIMIT 1)
       )
     WHERE bi.price_cents IS NULL
    """
)

#: Lo que la 0005 va a exigir NOT NULL. Si queda algo, el script falla en vez de decir OK.
_SQL_RESTANTES = text(
    """
    SELECT (SELECT count(*) FROM service_prices WHERE price_cents IS NULL)
         + (SELECT count(*) FROM booking_items WHERE price_cents IS NULL)
         + (SELECT count(*) FROM job_items WHERE price_cents IS NULL)
    """
)


def _p(msg: str) -> None:
    print(f"[prepare-0005] {msg}", flush=True)


def _async_url(raw: str) -> str:
    """El engine async exige `postgresql+asyncpg://`; Railway entrega `postgresql://`."""
    for prefix in ("postgresql+psycopg2://", "postgresql://", "postgres://"):
        if raw.startswith(prefix):
            return "postgresql+asyncpg://" + raw[len(prefix) :]
    return raw


async def _preparar_tenant(session: AsyncSession, owner_email: str) -> int | None:
    """Completa los precios NULL de un tenant. Devuelve las filas que quedan NULL, o `None`
    si no hay un tenant con ese dueño."""
    # Con RLS forzado el rol de runtime no ve `tenants`: se busca igual que el login, con
    # la función SECURITY DEFINER `auth_lookup_user` (misma técnica que el seed).
    tenant_id: uuid.UUID | None = await session.scalar(
        text("SELECT tenant_id FROM auth_lookup_user(:email)"), {"email": owner_email}
    )
    if tenant_id is None:
        return None
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
    )
    # Un UPDATE devuelve un CursorResult (con `rowcount`); el tipo genérico no lo declara.
    precios = cast(
        CursorResult[Any], await session.execute(_SQL_PRECIOS, {"base": BASE_PRICE_CENTS})
    )
    items = cast(CursorResult[Any], await session.execute(_SQL_ITEMS))
    restantes = int(await session.scalar(_SQL_RESTANTES) or 0)
    _p(
        f"{owner_email}: {precios.rowcount} precios base y {items.rowcount} ítems completados"
        f"; quedan {restantes} NULL"
    )
    return restantes


async def main(owners: list[str]) -> int:
    env = os.environ.get("APP_ENV", "")
    if env != "staging":
        _p(f"APP_ENV={env!r}: este script solo corre en staging. No se hace nada.")
        return 1 if env == "production" else 0

    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        _p("falta DATABASE_URL")
        return 1

    engine = create_async_engine(_async_url(raw))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    quedan = 0
    try:
        for owner in owners:
            # Una transacción por tenant: `set_config(..., true)` es LOCAL.
            async with factory() as session, session.begin():
                restantes = await _preparar_tenant(session, owner)
            if restantes is None:
                _p(f"{owner}: no existe ese dueño en staging; se omite")
            else:
                quedan += restantes
    finally:
        await engine.dispose()

    if quedan:
        _p(f"FALLÓ: quedan {quedan} precios NULL; la migración 0005 va a cortar.")
        return 1
    _p("OK: los tenants indicados no tienen precios NULL.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main([*SEED_OWNERS, *sys.argv[1:]])))
