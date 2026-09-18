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

Dominio (Fase 3, adenda A9): el lavadero se siembra **a través de los servicios de
aplicación** (`app/application/services`), no con INSERTs sueltos, para que el seed
también ejercite las máquinas de estado: catálogo, puestos, franjas, medios de pago,
clientes, vehículos y su vínculo, turnos en varios estados, un bloqueo, jobs que
recorren la máquina (con eventos, inspección, cobros, anulación y retención), una
cotización aceptada y otra rechazada, cancelaciones con su seña resuelta y los
movimientos de caja. Teléfonos `11 5550-00xx` y patentes `SS0xxSS`/`SSS0xx`, inventados.
El dominio se siembra una vez por tenant, en la misma transacción: si el tenant ya
tiene catálogo, no se toca (re-correr no duplica).

Uso local (contra el Postgres de docker compose):
    SEED_STAGING_PASSWORD=... make seed-staging
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import UTC, datetime, time, timedelta  # noqa: E402

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.application.services import (  # noqa: E402
    BookingService,
    CatalogService,
    CustomerService,
    DepositResolutionService,
    JobService,
    PaymentInput,
    QuoteService,
    VehicleService,
)
from app.domain.enums import (  # noqa: E402
    BookingSource,
    Channel,
    DepositStatus,
    DirtLevel,
    PricingMode,
)
from app.domain.roles import Role  # noqa: E402
from app.persistence.models.agenda import Booking  # noqa: E402
from app.persistence.models.catalog import VehicleSize  # noqa: E402
from app.persistence.models.dummy_resource import DummyResource  # noqa: E402
from app.persistence.models.idempotency_key import IdempotencyKey  # noqa: E402
from app.persistence.models.tenant import Tenant  # noqa: E402
from app.persistence.models.user import User  # noqa: E402
from app.utils.security import hash_password  # noqa: E402

#: Nombre de la variable con la contraseña de los usuarios sintéticos. NO hay
#: default: una contraseña versionada en el repo es una contraseña pública, y
#: staging es alcanzable desde internet. Se define en el panel de Railway.
PASSWORD_ENV = "SEED_STAGING_PASSWORD"

#: Lunes 5/10/2026 10:00 en Córdoba (13:00 UTC): la mañana de los turnos sintéticos.
BASE = datetime(2026, 10, 5, 13, tzinfo=UTC)
#: "Ahora" del server al reservar: el día anterior.
RESERVA = BASE - timedelta(days=1)

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
        # Un staging sembrado antes de la Fase 3 tiene el tenant pero no el dominio.
        por_rol = {u.role: u.id for u in users}
        if (
            Role.OWNER in por_rol
            and Role.STAFF in por_rol
            and await _seed_domain(session, existing, por_rol[Role.OWNER], por_rol[Role.STAFF])
        ):
            _p(f"{spec['name']}: dominio sembrado")
        return

    # El contexto va ANTES del INSERT del tenant: la política de `tenants` es
    # `id = app.tenant_id` también en el WITH CHECK.
    tenant_id = uuid.uuid4()
    await _set_tenant(session, tenant_id)
    tenant = Tenant(id=tenant_id, name=str(spec["name"]))
    session.add(tenant)
    await session.flush()

    nuevos = {
        role: User(tenant_id=tenant.id, email=email, password_hash=pw_hash, role=role)
        for email, role in emails.items()
    }
    session.add_all(nuevos.values())
    recursos = spec["recursos"]
    assert isinstance(recursos, list)
    session.add_all(DummyResource(tenant_id=tenant.id, name=str(n)) for n in recursos)
    # Una clave de idempotencia de ejemplo: el test de cobertura del seed exige
    # al menos una fila en TODA tabla con tenant_id, y esta también lo es.
    session.add(IdempotencyKey(tenant_id=tenant.id, key=f"seed-{tenant.id}", action="seed"))
    await session.flush()
    await _seed_domain(session, tenant.id, nuevos[Role.OWNER].id, nuevos[Role.STAFF].id)
    _p(f"{spec['name']}: creado (2 usuarios, {len(recursos)} recursos, dominio sembrado)")


def _en(minutos: int) -> datetime:
    return BASE + timedelta(minutes=minutos)


async def _seed_domain(
    session: AsyncSession, tenant_id: uuid.UUID, owner_id: uuid.UUID, staff_id: uuid.UUID
) -> bool:
    """Siembra el dominio del lavadero con los servicios. `False` si ya estaba sembrado."""
    ya = await session.scalar(
        select(func.count()).select_from(VehicleSize).where(VehicleSize.tenant_id == tenant_id)
    )
    if ya:
        return False

    catalogo = CatalogService(session, tenant_id, owner_id)
    auto = await catalogo.create_vehicle_size("AUTO", "Auto")
    suv = await catalogo.create_vehicle_size("SUV", "SUV", sort_order=1)
    lavado = await catalogo.create_service("Lavado completo", PricingMode.PRECIO_FIJO)
    premium = await catalogo.create_service("Lavado premium", PricingMode.PRECIO_FIJO)
    tapizado = await catalogo.create_service(
        "Limpieza de tapizados", PricingMode.A_COTIZAR, notes="Se cotiza al ver el auto"
    )
    for size, extra in ((auto, 0), (suv, 500_000)):
        await catalogo.set_price(lavado.id, size.id, price_cents=2_000_000 + extra, duration_min=60)
        await catalogo.set_price(
            premium.id, size.id, price_cents=3_000_000 + extra, duration_min=90, deposit_bps=3000
        )
        await catalogo.set_price(tapizado.id, size.id, price_cents=None, duration_min=120)
    puesto_1 = await catalogo.create_resource("Puesto 1")
    puesto_2 = await catalogo.create_resource("Puesto 2", sort_order=1)
    for weekday in range(1, 7):
        await catalogo.add_business_hours(weekday, time(9), time(13))
        await catalogo.add_business_hours(weekday, time(15), time(19))
    efectivo = await catalogo.create_payment_method(
        "EFECTIVO", "Efectivo", settlement_account="Caja"
    )
    await catalogo.create_payment_method(
        "TRANSFERENCIA", "Transferencia", settlement_account="Banco", for_expense=True
    )
    credito = await catalogo.create_payment_method(
        "CREDITO", "Crédito", commission_bps=500, settlement_account="Mercado Pago Point"
    )

    clientes = CustomerService(session, tenant_id, owner_id)
    vehiculos = VehicleService(session, tenant_id, owner_id)
    gente = []
    for n in range(1, 6):
        cliente = await clientes.resolve_by_phone(
            f"11 5550-00{n:02d}", name=f"Cliente Demo {n}", channel=Channel.WHATSAPP
        )
        auto_n = await vehiculos.resolve_by_plate(
            f"SS0{n:02d}SS", vehicle_size_id=auto.id, brand_model="Sedán demo", color="Gris"
        )
        await vehiculos.link_customer(cliente.customer.id, auto_n.vehicle.id, is_primary=True)
        gente.append((cliente.customer.id, auto_n.vehicle.id))
    walk_in_auto = await vehiculos.resolve_by_plate("SSS001", vehicle_size_id=suv.id)

    turnos = BookingService(session, tenant_id, staff_id)
    jobs = JobService(session, tenant_id, staff_id)
    await turnos.add_block(
        starts_at=_en(24 * 60),
        ends_at=_en(25 * 60),
        resource_id=puesto_2.id,
        reason="Mantenimiento",
    )

    codigos = iter(range(1, 100))

    async def turno(
        n: int, minutos: int, servicio: uuid.UUID, puesto: uuid.UUID, hold: datetime | None = None
    ) -> Booking:
        cliente_id, vehiculo_id = gente[n]
        return await turnos.create(
            code=f"DEMO-{next(codigos):03d}",
            source=BookingSource.PANEL,
            channel=Channel.WHATSAPP,
            resource_id=puesto,
            start_at=_en(minutos),
            customer_id=cliente_id,
            vehicle_id=vehiculo_id,
            service_id=servicio,
            vehicle_size_id=auto.id,
            hold_expires_at=hold,
            now=RESERVA,
        )

    def pago(monto: int, clave: str, cuando: datetime, medio: uuid.UUID) -> PaymentInput:
        return PaymentInput(monto, medio, f"seed-{tenant_id}-{clave}", cuando)

    def clave(nombre: str) -> str:
        return f"seed-{tenant_id}-{nombre}"

    # 1. Turno con seña que recorre toda la máquina del job hasta RETIRADO.
    completo = await turno(0, 0, premium.id, puesto_1.id, hold=RESERVA + timedelta(hours=1))
    await turnos.confirm_deposit(
        completo.id, pago(900_000, "sena-1", RESERVA, efectivo.id), now=RESERVA
    )
    job = await jobs.receive(
        idempotency_key=clave("rec-1"), arrived_at=_en(5), booking_id=completo.id
    )
    await jobs.record_inspection(
        job.id,
        idempotency_key=clave("insp-1"),
        occurred_at=_en(6),
        dirt_level=DirtLevel.BARRO,
        photo_consent=True,
        checklist={"llantas": True, "tapizado": False},
    )
    await jobs.start(job.id, idempotency_key=clave("ini-1"), occurred_at=_en(10))
    await jobs.adjust_price(
        job.id,
        idempotency_key=clave("ajuste-1"),
        occurred_at=_en(20),
        surcharge_cents=200_000,
        discount_cents=0,
        reason="Barro extra",
    )
    await jobs.finish(job.id, idempotency_key=clave("fin-1"), occurred_at=_en(95))
    duplicado = await jobs.record_payment(job.id, pago(500_000, "cobro-dup", _en(96), efectivo.id))
    await jobs.void_payment(
        duplicado.id, idempotency_key=clave("anula-1"), occurred_at=_en(97), reason="Cargado mal"
    )
    await jobs.record_payment(job.id, pago(2_300_000, "cobro-1", _en(98), credito.id))
    await jobs.pick_up(job.id, idempotency_key=clave("retiro-1"), occurred_at=_en(120))

    # 2. Llegó tarde: cancelado por demora, con la retención revertida.
    tarde = await turno(1, 0, lavado.id, puesto_2.id)
    job_tarde = await jobs.receive(
        idempotency_key=clave("rec-2"), arrived_at=_en(40), booking_id=tarde.id
    )
    await jobs.cancel_for_delay(
        job_tarde.id, idempotency_key=clave("demora-2"), occurred_at=_en(41), tolerance_min=20
    )
    await jobs.reverse_retention(
        job_tarde.id, idempotency_key=clave("revierte-2"), occurred_at=_en(60), reason="Avisó"
    )

    # 3. Cancelado por el cliente con seña: la seña se devuelve.
    cancelado = await turno(2, 180, premium.id, puesto_1.id, hold=RESERVA + timedelta(hours=1))
    await turnos.confirm_deposit(
        cancelado.id, pago(900_000, "sena-3", RESERVA, efectivo.id), now=RESERVA
    )
    cancelacion = await turnos.cancel_by_client(cancelado.id, requested_at=RESERVA, reason="Viaje")
    await DepositResolutionService(session, tenant_id, owner_id).resolve(
        cancelacion.id,
        status=DepositStatus.DEVUELTA,
        reason="Transferido al cliente",
        resolved_at=RESERVA + timedelta(hours=2),
        refund=pago(900_000, "devolucion-3", RESERVA + timedelta(hours=2), efectivo.id),
    )

    # 4. A cotizar: cotizado y aceptado, queda confirmado para el día siguiente.
    cotizado = await turno(
        3, 24 * 60 + 180, tapizado.id, puesto_1.id, hold=RESERVA + timedelta(days=2)
    )
    cotizaciones = QuoteService(session, tenant_id, owner_id)
    assert cotizado.quote_id is not None
    await cotizaciones.quote(
        cotizado.quote_id, agreed_price_cents=4_500_000, agreed_duration_min=150, quoted_at=RESERVA
    )
    await cotizaciones.accept(cotizado.quote_id, decided_at=RESERVA, now=RESERVA)
    suelta = await cotizaciones.create(
        customer_id=gente[3][0],
        service_id=tapizado.id,
        vehicle_size_id=auto.id,
        requested_at=RESERVA,
    )
    await cotizaciones.quote(
        suelta.id, agreed_price_cents=6_000_000, agreed_duration_min=240, quoted_at=RESERVA
    )
    await cotizaciones.reject(suelta.id, decided_at=RESERVA)

    # 5. Pendiente de seña (hold vigente), vencido y no asistió.
    await turno(4, 2 * 24 * 60, premium.id, puesto_1.id, hold=RESERVA + timedelta(days=30))
    vencible = await turno(
        4, 2 * 24 * 60, premium.id, puesto_2.id, hold=RESERVA + timedelta(minutes=30)
    )
    await turnos.expire_holds(vencible.resource_id, RESERVA + timedelta(hours=1))
    ausente = await turno(0, 3 * 24 * 60, lavado.id, puesto_1.id)
    await turnos.mark_no_show(ausente.id)

    # 6. Walk-in presente, sin turno.
    await jobs.receive(
        idempotency_key=clave("walk-6"),
        arrived_at=_en(300),
        vehicle_id=walk_in_auto.vehicle.id,
        vehicle_size_id=suv.id,
        service_id=lavado.id,
        channel=Channel.CALLE,
    )
    return True


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
