#!/usr/bin/env python3
"""Prepara staging para la migración `0005_configuracion_precios` — FASE-4-CONTRATO §3.2.

La 0005 hace obligatorio el precio base (D4-6) y **corta antes de alterar nada** si
encuentra precios NULL en `service_prices`, `booking_items` o `job_items`. El seed de
staging de la Fase 3 dejó el servicio a cotizar sin precio y su turno cotizado con el
ítem sin precio, así que hay que completarlos **antes** de mergear el PR 4.2.

Qué hace, por cada tenant del seed (`seed_staging.TENANTS`), contra el esquema 0004:

1. `service_prices` sin precio → precio base sintético (`PRECIO_BASE_CENTS`).
2. `booking_items` sin precio → el precio acordado de la cotización `ACEPTADO` del turno
   para ese servicio, o el precio base del servicio para el tamaño del turno si no hay
   una aceptada.
3. `bookings` sin total → la suma de sus ítems.

`job_items.price_cents` ya es NOT NULL desde la 0003: no hay nada que completar.

Corre como `carwash_app` (`DATABASE_URL`, RLS activo): sin contexto de tenant no ve
nada, así que encuentra cada tenant por el email de su OWNER con `auth_lookup_user`,
igual que el seed. Un tenant que no sea del seed y tenga precios NULL no se toca: la
guarda de la 0005 lo lista y corta el deploy.

Idempotente: la segunda corrida no cambia ninguna fila. Se niega a correr con
cualquier `APP_ENV` que no sea `staging`.

Uso (consola del servicio `api-staging` en Railway):
    python scripts/prepare_staging_0005.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from seed_staging import TENANTS, _async_url, _set_tenant  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

#: "Desde $30.000": precio base sintético del servicio a cotizar.
PRECIO_BASE_CENTS = 3_000_000

_PRECIOS = text(
    "UPDATE service_prices SET price_cents = :base "
    "WHERE price_cents IS NULL AND voided_at IS NULL"
)

_ITEMS = text(
    """
    UPDATE booking_items bi
    SET price_cents = COALESCE(
        (SELECT q.agreed_price_cents FROM quotes q
          WHERE q.booking_id = bi.booking_id AND q.service_id = bi.service_id
            AND q.status = 'ACEPTADO' AND q.voided_at IS NULL
          ORDER BY q.decided_at DESC NULLS LAST LIMIT 1),
        (SELECT sp.price_cents FROM service_prices sp JOIN bookings b ON b.id = bi.booking_id
          WHERE sp.service_id = bi.service_id AND sp.vehicle_size_id = b.vehicle_size_id
            AND sp.voided_at IS NULL
          LIMIT 1),
        :base
    )
    WHERE bi.price_cents IS NULL
    """
)

_TOTALES = text(
    """
    UPDATE bookings b
    SET price_cents = (SELECT sum(bi.price_cents) FROM booking_items bi
                        WHERE bi.booking_id = b.id AND bi.voided_at IS NULL)
    WHERE b.price_cents IS NULL
      AND EXISTS (SELECT 1 FROM booking_items bi
                   WHERE bi.booking_id = b.id AND bi.voided_at IS NULL)
    """
)


def _p(msg: str) -> None:
    print(f"[prepare-0005] {msg}", flush=True)


async def _preparar_tenant(session: AsyncSession, spec: dict[str, object]) -> int:
    tenant_id = await session.scalar(
        text("SELECT tenant_id FROM auth_lookup_user(:email)"), {"email": str(spec["owner"])}
    )
    if tenant_id is None:
        _p(f"{spec['name']}: no existe; nada que preparar")
        return 0
    await _set_tenant(session, tenant_id)
    cambios = 0
    for nombre, sentencia in (("precios", _PRECIOS), ("ítems", _ITEMS), ("totales", _TOTALES)):
        filas = (await session.execute(sentencia, {"base": PRECIO_BASE_CENTS})).rowcount
        cambios += filas
        _p(f"{spec['name']}: {nombre} completados: {filas}")
    return cambios


async def main() -> int:
    env = os.environ.get("APP_ENV", "")
    if env != "staging":
        _p(f"APP_ENV={env!r}: este script solo corre en staging. No se hace nada.")
        return 1

    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        _p("falta DATABASE_URL")
        return 1

    engine = create_async_engine(_async_url(raw))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    total = 0
    try:
        for spec in TENANTS:
            # Una transacción por tenant: el contexto de RLS es LOCAL a la transacción.
            async with factory() as session, session.begin():
                total += await _preparar_tenant(session, spec)
    finally:
        await engine.dispose()
    _p(f"OK: {total} filas cambiadas")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
