"""Armado de un lavadero sintético **a través de los servicios**, para los tests de aplicación.

No es un `conftest.py` (los fixtures globales no son de este agente): cada módulo de test
declara su fixture y llama a `armar`. Todo dato es inventado: teléfonos `1144445555`,
patentes `AB123CD`, emails `.invalid`. Nada sale de `docs/spec/` ni de `docs/legacy/`.
"""

import uuid
from collections.abc import Sequence
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
from app.application.services.settings_service import SettingsService
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
#: "Desde $25.000": precio base del servicio a cotizar (FASE-4-CONTRATO D4-6).
PRECIO_BASE_TAPIZADO = 2_500_000
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
    lavado: uuid.UUID  # PRECIO_FIJO, 60 min
    #: PRECIO_FIJO, 90 min. La seña ya es global (D4-4): `turno` la prende al 30 % cuando se
    #: pide este servicio y la apaga si no, para que los tests de F3 sigan leyendo igual.
    lavado_con_sena: uuid.UUID
    tapizado: uuid.UUID  # A_COTIZAR, base PRECIO_BASE_TAPIZADO, 120 min
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
        servicios: Sequence[uuid.UUID] | None = None,
        puesto: uuid.UUID | None = None,
        hold: datetime | None = None,
        sena: bool | None = None,
        vehiculo: uuid.UUID | None | object = ...,
        ahora: datetime = AHORA,
        codigo: str | None = None,
    ) -> Booking:
        """Ajusta la configuración del negocio antes de reservar (PR 4.2):

        - `sena`: prende la seña global al 30 % o la apaga. Por defecto, prendida si se pide
          `lavado_con_sena`.
        - `hold`: el vencimiento que esperaba el test de F3; se traduce a `hold_min`
          (`hold - ahora`), que es de donde lo calcula ahora `BookingService.create`.
        """
        pedidos = list(servicios) if servicios is not None else [servicio or self.lavado]
        await self.senar(
            self.lavado_con_sena in pedidos if sena is None else sena,
            hold_min=None if hold is None else int((hold - ahora).total_seconds() // 60),
        )
        return await self.turnos.create(
            code=codigo or f"TUR-{uuid.uuid4().hex[:8]}",
            source=BookingSource.PANEL,
            channel=Channel.WHATSAPP,
            resource_id=puesto or self.puesto,
            start_at=inicio,
            customer_id=self.cliente,
            service_ids=pedidos,
            vehicle_size_id=self.auto,
            vehicle_id=self.vehiculo if vehiculo is ... else vehiculo,  # type: ignore[arg-type]
            now=ahora,
        )

    async def senar(
        self, activa: bool, bps: int = SENA_BPS, *, hold_min: int | None = None
    ) -> None:
        """Prende o apaga la seña global del negocio (D4-4); opcionalmente cambia la retención."""
        cambios: dict[str, object] = {"deposit_enabled": activa, "deposit_bps": bps}
        if hold_min is not None:
            cambios["hold_min"] = hold_min
        await SettingsService(self.session, self.tenant_id, self.owner_id).update(cambios)

    async def recibido(
        self, booking: Booking | None = None, *, llegada: datetime = T, clave: str | None = None
    ) -> Job:
        booking = booking or await self.turno()
        return await self.jobs.receive(
            idempotency_key=clave or f"rec-{uuid.uuid4()}",
            arrived_at=llegada,
            booking_id=booking.id,
        )

    async def walk_in(
        self,
        *,
        clave: str | None = None,
        llegada: datetime = T,
        servicios: Sequence[uuid.UUID] | None = None,
        cliente: uuid.UUID | None = None,
    ) -> Job:
        return await self.jobs.receive(
            idempotency_key=clave or f"rec-{uuid.uuid4()}",
            arrived_at=llegada,
            vehicle_id=self.vehiculo,
            vehicle_size_id=self.auto,
            service_ids=servicios or [self.lavado],
            customer_id=cliente,
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
        await catalogo.set_price(con_sena.id, size.id, price_cents=PRECIO_CON_SENA, duration_min=90)
        await catalogo.set_price(
            tapizado.id, size.id, price_cents=PRECIO_BASE_TAPIZADO, duration_min=120
        )
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
