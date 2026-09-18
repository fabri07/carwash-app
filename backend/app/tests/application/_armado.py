"""Armado de un lavadero sintético **a través de los servicios**, para los tests de aplicación.

No es un `conftest.py` (los fixtures globales no son de este agente): cada módulo de test
declara su fixture y llama a `armar`. Todo dato es inventado: teléfonos `1144445555`,
patentes `AB123CD`, emails `.invalid`. Nada sale de `docs/spec/` ni de `docs/legacy/`.
"""

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services import (
    BookingService,
    CatalogService,
    CustomerService,
    DepositResolutionService,
    JobService,
    PaymentInput,
    QuoteService,
    VehicleService,
)
from app.domain.enums import BookingSource, Channel, PricingMode
from app.domain.roles import Role
from app.persistence.models import Booking, Job
from app.tests.conftest import make_tenant, make_user

#: Lunes 21/9/2026, 10:00 UTC: la hora del turno de referencia.
T = datetime(2026, 9, 21, 10, tzinfo=UTC)
#: "Ahora" del server al reservar: el día anterior.
AHORA = T - timedelta(days=1)

PRECIO_FIJO = 2_000_000  # $ 20.000
PRECIO_CON_SENA = 3_000_000  # $ 30.000, seña 30 % = $ 9.000
SENA_BPS = 3000
SENA = 900_000
COMISION_CREDITO_BPS = 500  # 5 %


@dataclass
class Lavadero:
    session: AsyncSession
    tenant_id: uuid.UUID
    owner_id: uuid.UUID
    auto: uuid.UUID
    suv: uuid.UUID
    lavado: uuid.UUID  # PRECIO_FIJO sin seña, 60 min
    lavado_con_sena: uuid.UUID  # PRECIO_FIJO con seña 30 %, 90 min
    tapizado: uuid.UUID  # A_COTIZAR, 120 min
    puesto: uuid.UUID
    puesto_2: uuid.UUID
    efectivo: uuid.UUID
    credito: uuid.UUID
    cliente: uuid.UUID
    vehiculo: uuid.UUID

    def en(self, session: AsyncSession) -> "Lavadero":
        """El mismo lavadero sobre otra sesión (Postgres: una transacción por sesión)."""
        return replace(self, session=session)

    @property
    def catalogo(self) -> CatalogService:
        return CatalogService(self.session, self.tenant_id, self.owner_id)

    @property
    def turnos(self) -> BookingService:
        return BookingService(self.session, self.tenant_id, self.owner_id)

    @property
    def jobs(self) -> JobService:
        return JobService(self.session, self.tenant_id, self.owner_id)

    @property
    def cotizaciones(self) -> QuoteService:
        return QuoteService(self.session, self.tenant_id, self.owner_id)

    @property
    def clientes(self) -> CustomerService:
        return CustomerService(self.session, self.tenant_id, self.owner_id)

    @property
    def vehiculos(self) -> VehicleService:
        return VehicleService(self.session, self.tenant_id, self.owner_id)

    @property
    def senas(self) -> DepositResolutionService:
        return DepositResolutionService(self.session, self.tenant_id, self.owner_id)

    def pago(
        self,
        monto: int,
        clave: str | None = None,
        *,
        medio: uuid.UUID | None = None,
        cuando: datetime | None = None,
    ) -> PaymentInput:
        return PaymentInput(
            amount_cents=monto,
            payment_method_id=medio or self.efectivo,
            idempotency_key=clave or f"pago-{uuid.uuid4()}",
            occurred_at=cuando or T,
        )

    async def turno(
        self,
        *,
        inicio: datetime = T,
        servicio: uuid.UUID | None = None,
        puesto: uuid.UUID | None = None,
        hold: datetime | None = None,
        vehiculo: uuid.UUID | None | object = ...,
        ahora: datetime = AHORA,
        codigo: str | None = None,
    ) -> Booking:
        return await self.turnos.create(
            code=codigo or f"TUR-{uuid.uuid4().hex[:8]}",
            source=BookingSource.PANEL,
            channel=Channel.WHATSAPP,
            resource_id=puesto or self.puesto,
            start_at=inicio,
            customer_id=self.cliente,
            service_id=servicio or self.lavado,
            vehicle_size_id=self.auto,
            vehicle_id=self.vehiculo if vehiculo is ... else vehiculo,  # type: ignore[arg-type]
            hold_expires_at=hold,
            now=ahora,
        )

    async def recibido(
        self, booking: Booking | None = None, *, llegada: datetime = T, clave: str | None = None
    ) -> Job:
        booking = booking or await self.turno()
        return await self.jobs.receive(
            idempotency_key=clave or f"rec-{uuid.uuid4()}",
            arrived_at=llegada,
            booking_id=booking.id,
        )

    async def walk_in(self, *, clave: str | None = None, llegada: datetime = T) -> Job:
        return await self.jobs.receive(
            idempotency_key=clave or f"rec-{uuid.uuid4()}",
            arrived_at=llegada,
            vehicle_id=self.vehiculo,
            vehicle_size_id=self.auto,
            service_id=self.lavado,
            channel=Channel.CALLE,
        )

    async def finalizado(self, job: Job | None = None) -> Job:
        job = job or await self.walk_in()
        await self.jobs.start(job.id, idempotency_key=f"ini-{uuid.uuid4()}", occurred_at=T)
        return await self.jobs.finish(job.id, idempotency_key=f"fin-{uuid.uuid4()}", occurred_at=T)


async def armar(session: AsyncSession, nombre: str = "Lavadero sintético") -> Lavadero:
    tenant = await make_tenant(session, nombre)
    owner = await make_user(
        session, tenant, Role.OWNER, f"owner-{uuid.uuid4().hex[:8]}@ejemplo.invalid"
    )
    return await armar_catalogo(session, tenant.id, owner.id)


async def armar_catalogo(
    session: AsyncSession, tenant_id: uuid.UUID, owner_id: uuid.UUID
) -> Lavadero:
    """Catálogo, puestos, medios de pago, un cliente y un auto, todo por los servicios."""
    catalogo = CatalogService(session, tenant_id, owner_id)
    auto = await catalogo.create_vehicle_size("AUTO", "Auto")
    suv = await catalogo.create_vehicle_size("SUV", "SUV", sort_order=1)
    lavado = await catalogo.create_service("Lavado completo", PricingMode.PRECIO_FIJO)
    con_sena = await catalogo.create_service("Lavado premium", PricingMode.PRECIO_FIJO)
    tapizado = await catalogo.create_service("Tapizado", PricingMode.A_COTIZAR)
    for size in (auto, suv):
        await catalogo.set_price(lavado.id, size.id, price_cents=PRECIO_FIJO, duration_min=60)
        await catalogo.set_price(
            con_sena.id,
            size.id,
            price_cents=PRECIO_CON_SENA,
            duration_min=90,
            deposit_bps=SENA_BPS,
        )
        await catalogo.set_price(tapizado.id, size.id, price_cents=None, duration_min=120)
    puesto = await catalogo.create_resource("Puesto 1")
    puesto_2 = await catalogo.create_resource("Puesto 2", sort_order=1)
    efectivo = await catalogo.create_payment_method("EFECTIVO", "Efectivo")
    credito = await catalogo.create_payment_method(
        "CREDITO", "Crédito", commission_bps=COMISION_CREDITO_BPS
    )
    cliente = await CustomerService(session, tenant_id, owner_id).resolve_by_phone(
        "11 4444-5555", name="Cliente Uno", channel=Channel.WHATSAPP
    )
    vehiculo = await VehicleService(session, tenant_id, owner_id).resolve_by_plate(
        "AB 123 CD", vehicle_size_id=auto.id
    )
    return Lavadero(
        session=session,
        tenant_id=tenant_id,
        owner_id=owner_id,
        auto=auto.id,
        suv=suv.id,
        lavado=lavado.id,
        lavado_con_sena=con_sena.id,
        tapizado=tapizado.id,
        puesto=puesto.id,
        puesto_2=puesto_2.id,
        efectivo=efectivo.id,
        credito=credito.id,
        cliente=cliente.customer.id,
        vehiculo=vehiculo.vehicle.id,
    )
