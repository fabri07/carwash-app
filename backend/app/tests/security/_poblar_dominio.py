"""Poblador de aislamiento — FASE-3-CONTRATO §6 y adenda A9 (Tester-aislamiento de T3).

`poblar(admin_engine, tenant_id)` inserta **exactamente una fila válida en cada tabla con
`tenant_id`** de `Base.metadata` para ese lavadero y devuelve `{tabla: id}`. Es la base de
B7/B14 (`test_aislamiento_dominio_pg.py`) y de los tests de F2 (`test_aislamiento_tenants_pg.py`).

Decisiones:

- **Superusuario y Core, sin servicios.** El poblador no puede depender del código que los tests
  verifican: inserta con `insert(tabla)` sobre la metadata del ORM (los enums, `jsonb` y `text[]`
  los adapta SQLAlchemy) con el superusuario, que se saltea RLS.
- **Todas las FKs llenas**, también las opcionales, y el ciclo `bookings.quote_id` ↔
  `quotes.booking_id` cerrado con un `UPDATE` posterior: con datos en cada FK, el grafo de A es
  el más rico posible y B14 lo recorre entero.
- **Enums con `Ñ`** a propósito: el turno queda en `PENDIENTE_SEÑA` y el cobro es una `SEÑA`.
- **Únicos derivados del tenant** (email, códigos, `idempotency_key`, teléfono, patente): dos
  lavaderos poblados no chocan ni siquiera en un único que por error fuera global.
- **Mismo horario en todos los lavaderos.** Los turnos de A y B se solapan en el tiempo (cada
  uno en su puesto): si el `EXCLUDE` no discriminara por tenant, poblar B fallaría.
- **Una tabla nueva sin receta falla**, no se saltea: `TablaSinPoblarError` con el nombre.

Datos sintéticos siempre (adenda A10): `@ejemplo.invalid`, `1144445555`, `AB123CD`.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import Table, insert, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

import app.persistence.models  # noqa: F401  (registra todas las tablas en la metadata)
from app.persistence.db.base import Base

#: El turno de todos los lavaderos: mismo intervalo, a propósito (ver docstring).
INICIO_TURNO = datetime(2026, 10, 1, 13, 0, tzinfo=UTC)
FIN_TURNO = INICIO_TURNO + timedelta(minutes=60)
LLEGADA = INICIO_TURNO + timedelta(minutes=5)

PRECIO_CENTS = 2_000_000  # $ 20.000,00: el caso de `parseMoney_`
SENA_BPS = 3000
SENA_CENTS = 600_000


class TablaSinPoblarError(RuntimeError):
    """Hay una tabla con `tenant_id` que el poblador no sabe llenar."""


def tablas_tenant() -> list[Table]:
    """Toda tabla con `tenant_id`, en orden de dependencia (el ciclo con `use_alter` se ignora)."""
    return [t for t in Base.metadata.sorted_tables if "tenant_id" in t.c]


class _Contexto:
    """Ids ya insertados y valores únicos derivados del tenant."""

    def __init__(self, tenant_id: uuid.UUID) -> None:
        self.tenant_id = tenant_id
        self.tag = tenant_id.hex[:12]
        self.TAG = self.tag.upper()
        # 8 dígitos estables por tenant para teléfono; 3 para la patente.
        self.digitos = f"{tenant_id.int % 10**8:08d}"
        self.ids: dict[str, uuid.UUID] = {}

    def __getitem__(self, tabla: str) -> uuid.UUID:
        return self.ids[tabla]


Receta = Callable[[_Contexto], dict[str, Any]]


RECETAS: dict[str, Receta] = {
    # ── F2 ──
    "users": lambda c: {
        "email": f"usuario-{c.tag}@ejemplo.invalid",
        "password_hash": "no-es-un-hash",
        "role": "OWNER",
    },
    "dummy_resources": lambda c: {"name": f"dummy de {c.tag}"},
    "idempotency_keys": lambda c: {"key": f"clave-{c.tag}", "action": "poblar"},
    # ── 1.1 Catálogo ──
    "vehicle_sizes": lambda c: {"code": f"AUTO_{c.TAG}", "label": "Auto", "sort_order": 1},
    "services": lambda c: {
        "name": f"Lavado completo {c.tag}",
        "pricing_mode": "PRECIO_FIJO",
        "notes": "sintético",
    },
    "service_prices": lambda c: {
        "service_id": c["services"],
        "vehicle_size_id": c["vehicle_sizes"],
        "price_cents": PRECIO_CENTS,
        "duration_min": 60,
        "deposit_bps": SENA_BPS,
    },
    "resources": lambda c: {"name": f"Puesto 1 {c.tag}", "sort_order": 1},
    "business_hours": lambda c: {
        "weekday": 4,
        "opens_at": time(9, 0),
        "closes_at": time(13, 0),
    },
    "payment_methods": lambda c: {
        "code": f"EFECTIVO_{c.TAG}",
        "label": "Efectivo",
        "commission_bps": 0,
        "settlement_account": "Caja",
        "for_income": True,
        "for_expense": True,
    },
    # ── 1.2 Clientes y vehículos ──
    "customers": lambda c: {
        "name": f"Cliente {c.tag}",
        "phone_e164": f"+54911{c.digitos}",
        "phone_raw": f"11{c.digitos}",
        "email": f"cliente-{c.tag}@ejemplo.invalid",
        "acquisition_channel": "WHATSAPP",
        "notes": "sintético",
        "legacy_ids": [f"CLI-{c.tag}"],
    },
    "vehicles": lambda c: {
        "plate": f"AB {c.digitos[-3:]} CD",
        "plate_normalized": f"AB{c.digitos[-3:]}CD",
        "plate_format": "MERCOSUR",
        "vehicle_size_id": c["vehicle_sizes"],
        "brand_model": "Fiat Cronos",
        "color": "Gris",
        "notes": "sintético",
        "legacy_id": f"VEH-{c.tag}",
    },
    "customer_vehicles": lambda c: {
        "customer_id": c["customers"],
        "vehicle_id": c["vehicles"],
        "valid_from": INICIO_TURNO - timedelta(days=30),
        "is_primary": True,
    },
    # ── 1.3 Agenda ──
    "bookings": lambda c: {
        "code": f"TUR-{c.TAG}",
        "source": "TURNERO_WEB",
        "channel": "TURNERO_WEB",
        "status": "PENDIENTE_SEÑA",
        "resource_id": c["resources"],
        "start_at": INICIO_TURNO,
        "end_at": FIN_TURNO,
        "hold_expires_at": INICIO_TURNO - timedelta(hours=1),
        "customer_id": c["customers"],
        "vehicle_id": c["vehicles"],
        "service_id": c["services"],
        "vehicle_size_id": c["vehicle_sizes"],
        "service_name_snapshot": "Lavado completo",
        "duration_min": 60,
        "price_cents": PRECIO_CENTS,
        "deposit_bps": SENA_BPS,
        "deposit_required_cents": SENA_CENTS,
        "quote_id": None,  # se cierra el ciclo después de crear la cotización
        "terms_version": "v1",
        "terms_accepted_at": INICIO_TURNO - timedelta(days=1),
        "notes": "sintético",
        "legacy_id": f"TUR-LEG-{c.tag}",
    },
    "schedule_blocks": lambda c: {
        "resource_id": c["resources"],
        "starts_at": INICIO_TURNO + timedelta(days=1),
        "ends_at": INICIO_TURNO + timedelta(days=2),
        "reason": "Bloqueo operativo",
        "created_by_user_id": c["users"],
    },
    # ── 1.4 Operación ──
    "quotes": lambda c: {
        "customer_id": c["customers"],
        "vehicle_id": c["vehicles"],
        "service_id": c["services"],
        "vehicle_size_id": c["vehicle_sizes"],
        "booking_id": c["bookings"],
        "status": "ACEPTADO",
        "requested_at": INICIO_TURNO - timedelta(days=3),
        "agreed_price_cents": PRECIO_CENTS,
        "agreed_duration_min": 60,
        "supplies_purchaser": "CLIENTE",
        "supplies_cost_cents": 0,
        "expires_at": INICIO_TURNO,
        "quoted_at": INICIO_TURNO - timedelta(days=2),
        "decided_at": INICIO_TURNO - timedelta(days=2),
        "decided_by_user_id": c["users"],
        "notes": "sintético",
        "legacy_id": f"COT-{c.tag}",
    },
    "jobs": lambda c: {
        "booking_id": c["bookings"],
        "quote_id": c["quotes"],
        "customer_id": c["customers"],
        "vehicle_id": c["vehicles"],
        "vehicle_size_id": c["vehicle_sizes"],
        "service_id": c["services"],
        "resource_id": c["resources"],
        "responsible_user_id": c["users"],
        "channel": "TURNERO_WEB",
        "status": "PRESENTE",
        "service_name_snapshot": "Lavado completo",
        "base_price_cents": PRECIO_CENTS,
        "surcharge_cents": 0,
        "discount_cents": 0,
        "deposit_required_cents": SENA_CENTS,
        "scheduled_at": INICIO_TURNO,
        "arrived_at": LLEGADA,
        "arrival_delay_min": 5,
        "notes": "sintético",
        "legacy_id": f"JOB-{c.tag}",
        "legacy_review_flags": ["sintetico"],
    },
    "job_events": lambda c: {
        "job_id": c["jobs"],
        "event_type": "JOB_RECEIVED",
        "from_status": None,
        "to_status": "PRESENTE",
        "occurred_at": LLEGADA,
        "actor_user_id": c["users"],
        "idempotency_key": f"evento-{c.tag}",
        "metadata": {"source": "poblar"},
    },
    "job_inspections": lambda c: {
        "job_id": c["jobs"],
        "dirt_level": "NORMAL",
        "pre_existing_damage": "ninguno",
        "valuables": "ninguno",
        "photo_consent": True,
        "checklist": {"ruedas": True},
        "inspected_by_user_id": c["users"],
        "inspected_at": LLEGADA,
    },
    # ── 1.5 Dinero ──
    "payments": lambda c: {
        "job_id": c["jobs"],
        "booking_id": c["bookings"],
        "kind": "SEÑA",
        "amount_cents": SENA_CENTS,
        "payment_method_id": c["payment_methods"],
        "commission_bps": 0,
        "commission_cents": 0,
        "occurred_at": INICIO_TURNO - timedelta(days=1),
        "actor_user_id": c["users"],
        "idempotency_key": f"cobro-{c.tag}",
        "reference": "sintético",
        "notes": "sintético",
        "legacy_id": f"PAG-{c.tag}",
        "legacy_review_flags": ["sintetico"],
    },
    "cancellations": lambda c: {
        "booking_id": c["bookings"],
        "initiator": "CLIENTE",
        "classification": "NORMAL",
        "requested_at": INICIO_TURNO - timedelta(days=2),
        "anticipation_min": 2880,
        "reason": "Prefiere no informarlo",
        "previous_status": "PENDIENTE_SEÑA",
        "resulting_status": "CANCELADO_CLIENTE",
        "deposit_paid_cents": SENA_CENTS,
        "deposit_status": "DEVUELTA",
        "resolved_at": INICIO_TURNO - timedelta(days=1),
        "resolved_by_user_id": c["users"],
        "resolution_reason": "devolución sintética",
        "refund_payment_id": c["payments"],
        "actor_user_id": c["users"],
        "terms_version": "v1",
    },
    "cash_movements": lambda c: {
        "direction": "ENTRADA",
        "kind": "COBRO",
        "amount_cents": SENA_CENTS,
        "payment_method_id": c["payment_methods"],
        "payment_id": c["payments"],
        "occurred_at": INICIO_TURNO - timedelta(days=1),
        "actor_user_id": c["users"],
        "idempotency_key": f"caja-{c.tag}",
        "notes": "sintético",
    },
}


def verificar_recetas() -> None:
    """Falla con un mensaje claro si la metadata y las recetas no coinciden."""
    en_metadata = {t.name for t in tablas_tenant()}
    faltan = sorted(en_metadata - set(RECETAS))
    if faltan:
        raise TablaSinPoblarError(
            f"tablas con tenant_id sin receta en _poblar_dominio.RECETAS: {faltan}. "
            "Agregá una fila válida para cada una: sin eso el aislamiento de esa tabla "
            "no se prueba (FASE-3-CONTRATO B7/B14)."
        )
    sobran = sorted(set(RECETAS) - en_metadata)
    if sobran:
        raise TablaSinPoblarError(
            f"recetas de tablas que ya no existen o no tienen tenant: {sobran}"
        )


async def _insertar(conn: AsyncConnection, tabla: Table, valores: dict[str, Any]) -> uuid.UUID:
    fila_id = uuid.uuid4()
    await conn.execute(insert(tabla).values(id=fila_id, **valores))
    return fila_id


async def poblar(
    admin_engine: AsyncEngine, tenant_id: uuid.UUID, *, user_id: uuid.UUID | None = None
) -> dict[str, uuid.UUID]:
    """Una fila válida por tabla con `tenant_id` para `tenant_id`. Devuelve `{tabla: id}`.

    `user_id`: usuario ya existente del tenant (p. ej. creado con `pg_user_factory` para
    poder loguearse). Si viene, no se crea otro y se devuelve en `"users"`.
    """
    verificar_recetas()
    ctx = _Contexto(tenant_id)
    async with admin_engine.begin() as conn:
        for tabla in tablas_tenant():
            if tabla.name == "users" and user_id is not None:
                ctx.ids["users"] = user_id
                continue
            valores = {"tenant_id": tenant_id, **RECETAS[tabla.name](ctx)}
            desconocidas = set(valores) - set(tabla.c.keys())
            if desconocidas:
                raise TablaSinPoblarError(
                    f"la receta de {tabla.name} usa columnas que no existen: {sorted(desconocidas)}"
                )
            ctx.ids[tabla.name] = await _insertar(conn, tabla, valores)
        # Ciclo `bookings.quote_id` ↔ `quotes.booking_id` (FK con `use_alter`).
        bookings = Base.metadata.tables["bookings"]
        await conn.execute(
            update(bookings).where(bookings.c.id == ctx["bookings"]).values(quote_id=ctx["quotes"])
        )
    return dict(ctx.ids)
