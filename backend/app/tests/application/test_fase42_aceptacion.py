"""A7 · A7b · FASE-4-CONTRATO §0 (D4-4..D4-7, D4-13), §3.2 y §5 — seña global y cotización.

Escrito por el tester de aceptación del PR 4.2, en paralelo con el implementador y a partir
del contrato (caja negra a nivel servicios de aplicación). El negocio nace por
`provision_tenant` (Y3: crea `tenant_settings` con los defaults) y el catálogo por
`CatalogService`; la seña se prende escribiendo `tenant_settings` directo.

A7 — seña:
- apagada → `deposit_required_cents = 0`, turno `CONFIRMADO`, snapshot `deposit_bps = 0`;
- prendida al 20 % → 20 % **por ítem** de precio fijo, redondeado por ítem; hold = `hold_min`;
- ítem `A_COTIZAR` sin seña al reservar; al aceptar, seña sobre el acordado con el % guardado
  al reservar aunque el negocio lo haya cambiado (o apagado) después;
- seña pagada en parte; recibir con seña impaga; saldo = precio final − pagos netos (D4-5).

A7b — cotización:
- un turno a cotizar no ocupa el puesto (D4-13, Postgres: el `EXCLUDE`);
- `confirm_deposit` y `confirm_late` rechazan un turno con cotización pendiente aunque haya
  precio base, y también con la cotización vencida;
- aceptar con el horario tomado → `SlotTakenError` y la cotización sigue `COTIZADO`;
- reservar con menos de `min_advance_min` falla.

Datos sintéticos: emails `.invalid`, teléfonos `11 0000-00xx`, patentes `ZZ0xxZZ`.
"""

import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.application.services import (
    BookingService,
    CatalogService,
    CustomerService,
    JobService,
    PaymentInput,
    QuoteService,
    VehicleService,
)
from app.application.services.errors import SlotTakenError
from app.application.services.tenant_provisioning import provision_tenant
from app.domain.enums import BookingSource, BookingStatus, Channel, PricingMode, QuoteStatus
from app.domain.exceptions import DomainError, GuardFailedError, InvalidTransition
from app.persistence.db.tenant_context import set_tenant_context
from app.persistence.models import Booking, BookingItem, Payment, Quote
from app.persistence.models.tenant import TenantSettings
from app.tests.conftest_pg import PG_TEST_URL, _as_app_role, _with_driver
from app.utils.security import hash_password

S = BookingStatus

#: Lunes 1/3/2027, 13:00 UTC: la hora del turno de referencia.
T = datetime(2027, 3, 1, 13, tzinfo=UTC)
#: "Ahora" del server al reservar: el día anterior (lejos de cualquier anticipación mínima).
AHORA = T - timedelta(days=1)

#: Precio que, al 20 %, deja medio centavo: 200_000,6 → 200_001 por ítem. Dos ítems suman
#: 400_002 redondeando por ítem y 400_001 redondeando el total: el test distingue los dos.
PRECIO_IMPAR = 1_000_003
SENA_IMPAR_20 = 200_001
#: Precio base de referencia del servicio a cotizar ("desde $X", D4-6).
BASE_TAPIZADO = 5_000_000
#: Acordado en la cotización: al 20 % → 600_000,2 → 600_000.
ACORDADO = 3_000_001
SENA_ACORDADO_20 = 600_000
DURACION_ACORDADA = 120


def _sena(precio: int, bps: int) -> int:
    """`round_half_up(precio × bps / 10000)`, el redondeo de F3 (R-T-003)."""
    return (precio * bps + 5_000) // 10_000


# ── Armado ────────────────────────────────────────────────────────────────────


@dataclass
class Negocio:
    session: AsyncSession
    tenant_id: uuid.UUID
    owner_id: uuid.UUID
    auto: uuid.UUID
    lavado: uuid.UUID  # PRECIO_FIJO, PRECIO_IMPAR, 60 min
    encerado: uuid.UUID  # PRECIO_FIJO, PRECIO_IMPAR, 30 min
    tapizado: uuid.UUID  # A_COTIZAR, base BASE_TAPIZADO, 120 min
    puesto: uuid.UUID
    puesto_2: uuid.UUID
    efectivo: uuid.UUID
    cliente: uuid.UUID
    vehiculo: uuid.UUID

    def en(self, session: AsyncSession) -> "Negocio":
        return replace(self, session=session)

    @property
    def turnos(self) -> BookingService:
        return BookingService(self.session, self.tenant_id, self.owner_id)

    @property
    def cotizaciones(self) -> QuoteService:
        return QuoteService(self.session, self.tenant_id, self.owner_id)

    @property
    def jobs(self) -> JobService:
        return JobService(self.session, self.tenant_id, self.owner_id)

    async def configurar(self, **campos: Any) -> TenantSettings:
        """Escribe `tenant_settings` directo (la seña, el hold, la anticipación…)."""
        await set_tenant_context(self.session, self.tenant_id)
        fila = await self.session.scalar(
            select(TenantSettings).where(TenantSettings.tenant_id == self.tenant_id)
        )
        assert fila is not None, "provision_tenant no creó tenant_settings (Y3, etapa 4.2)"
        for campo, valor in campos.items():
            setattr(fila, campo, valor)
        await self.session.flush()
        return fila

    def pago(self, monto: int, *, cuando: datetime = AHORA) -> PaymentInput:
        return PaymentInput(
            amount_cents=monto,
            payment_method_id=self.efectivo,
            idempotency_key=f"pago-{uuid.uuid4()}",
            occurred_at=cuando,
        )

    async def turno(
        self,
        *,
        servicios: Sequence[uuid.UUID] | None = None,
        inicio: datetime = T,
        puesto: uuid.UUID | None = None,
        ahora: datetime = AHORA,
    ) -> Booking:
        return await self.turnos.create(
            code=f"TUR-{uuid.uuid4().hex[:8]}",
            source=BookingSource.PANEL,
            channel=Channel.WHATSAPP,
            resource_id=puesto or self.puesto,
            start_at=inicio,
            customer_id=self.cliente,
            service_ids=list(servicios) if servicios is not None else [self.lavado],
            vehicle_size_id=self.auto,
            vehicle_id=self.vehiculo,
            now=ahora,
        )

    async def items(self, booking: Booking) -> list[BookingItem]:
        filas = await self.session.scalars(
            select(BookingItem)
            .where(BookingItem.booking_id == booking.id)
            .order_by(BookingItem.position)
        )
        return list(filas.all())

    async def cotizar(
        self,
        booking: Booking,
        *,
        precio: int = ACORDADO,
        duracion: int = DURACION_ACORDADA,
        vence: datetime | None = None,
    ) -> Quote:
        assert booking.quote_id is not None
        return await self.cotizaciones.quote(
            booking.quote_id,
            agreed_price_cents=precio,
            agreed_duration_min=duracion,
            quoted_at=AHORA,
            expires_at=vence,
        )

    async def aceptar(self, booking: Booking, *, ahora: datetime = AHORA) -> Quote:
        assert booking.quote_id is not None
        return await self.cotizaciones.accept(booking.quote_id, decided_at=ahora, now=ahora)

    async def recibir(self, booking: Booking) -> Any:
        return await self.jobs.receive(
            idempotency_key=f"rec-{uuid.uuid4()}", arrived_at=T, booking_id=booking.id
        )


async def armar(session: AsyncSession) -> Negocio:
    alta = await provision_tenant(
        session,
        name=f"Lavadero sintético {uuid.uuid4().hex[:6]}",
        owner_email=f"owner-{uuid.uuid4().hex[:8]}@ejemplo.invalid",
        owner_password_hash=hash_password("clave-sintetica-123"),
    )
    tenant_id, owner_id = alta.tenant.id, alta.owner.id
    catalogo = CatalogService(session, tenant_id, owner_id)
    auto = await catalogo.create_vehicle_size("AUTO", "Auto")
    lavado = await catalogo.create_service("Lavado completo", PricingMode.PRECIO_FIJO)
    encerado = await catalogo.create_service("Encerado", PricingMode.PRECIO_FIJO)
    tapizado = await catalogo.create_service("Tapizado", PricingMode.A_COTIZAR)
    await catalogo.set_price(lavado.id, auto.id, price_cents=PRECIO_IMPAR, duration_min=60)
    await catalogo.set_price(encerado.id, auto.id, price_cents=PRECIO_IMPAR, duration_min=30)
    await catalogo.set_price(tapizado.id, auto.id, price_cents=BASE_TAPIZADO, duration_min=120)
    puesto = await catalogo.create_resource("Puesto 1")
    puesto_2 = await catalogo.create_resource("Puesto 2", sort_order=1)
    efectivo = await catalogo.create_payment_method("EFECTIVO", "Efectivo")
    cliente = await CustomerService(session, tenant_id, owner_id).resolve_by_phone(
        "11 0000-0042", name="Cliente Sintético", channel=Channel.WHATSAPP
    )
    vehiculo = await VehicleService(session, tenant_id, owner_id).resolve_by_plate(
        "ZZ042ZZ", vehicle_size_id=auto.id
    )
    return Negocio(
        session=session,
        tenant_id=tenant_id,
        owner_id=owner_id,
        auto=auto.id,
        lavado=lavado.id,
        encerado=encerado.id,
        tapizado=tapizado.id,
        puesto=puesto.id,
        puesto_2=puesto_2.id,
        efectivo=efectivo.id,
        cliente=cliente.customer.id,
        vehiculo=vehiculo.vehicle.id,
    )


@pytest_asyncio.fixture
async def neg(db_session: AsyncSession) -> Negocio:
    return await armar(db_session)


# ══ A7 · Seña global ══════════════════════════════════════════════════════════


async def test_provision_tenant_deja_la_sena_apagada_al_20_por_ciento(neg):
    """D4-4/D4-11 y defaults de §3.2."""
    fila = await neg.configurar()
    assert fila.deposit_enabled is False
    assert fila.deposit_bps == 2000
    assert (fila.hold_min, fila.min_advance_min) == (30, 5)
    assert (fila.tolerance_min, fila.late_cancel_threshold_min) == (20, 720)


async def test_sena_apagada_no_pide_sena_y_confirma(neg):
    booking = await neg.turno(servicios=[neg.lavado, neg.encerado])
    assert booking.deposit_required_cents == 0
    assert booking.status == S.CONFIRMADO
    assert booking.hold_expires_at is None
    assert booking.price_cents == 2 * PRECIO_IMPAR
    assert [i.deposit_bps for i in await neg.items(booking)] == [0, 0]


async def test_sena_apagada_con_porcentaje_guardado_sigue_sin_pedir_sena(neg):
    """§3.2: el % se conserva con la seña apagada; apagada manda."""
    await neg.configurar(deposit_enabled=False, deposit_bps=5000)
    booking = await neg.turno()
    assert booking.deposit_required_cents == 0 and booking.status == S.CONFIRMADO


async def test_sena_al_20_es_por_item_con_redondeo_por_item(neg):
    await neg.configurar(deposit_enabled=True, deposit_bps=2000)
    booking = await neg.turno(servicios=[neg.lavado, neg.encerado])
    assert booking.deposit_required_cents == 2 * SENA_IMPAR_20  # 400_002, no 400_001
    assert booking.deposit_required_cents != _sena(2 * PRECIO_IMPAR, 2000)
    assert booking.status == S.PENDIENTE_SENA
    assert [i.deposit_bps for i in await neg.items(booking)] == [2000, 2000]


async def test_el_hold_sale_de_hold_min(neg):
    await neg.configurar(deposit_enabled=True, hold_min=45)
    booking = await neg.turno()
    assert booking.status == S.PENDIENTE_SENA
    assert booking.hold_expires_at is not None
    hold = booking.hold_expires_at
    hold = hold if hold.tzinfo is not None else hold.replace(tzinfo=UTC)
    assert hold == AHORA + timedelta(minutes=45)


async def test_el_turno_ya_reservado_no_cambia_si_el_negocio_cambia_el_porcentaje(neg):
    await neg.configurar(deposit_enabled=True, deposit_bps=2000)
    booking = await neg.turno()
    await neg.configurar(deposit_bps=9000)
    await neg.session.refresh(booking)
    assert booking.deposit_required_cents == SENA_IMPAR_20
    assert [i.deposit_bps for i in await neg.items(booking)] == [2000]


async def test_item_a_cotizar_no_lleva_sena_al_reservar(neg):
    await neg.configurar(deposit_enabled=True, deposit_bps=2000)
    booking = await neg.turno(servicios=[neg.tapizado])
    assert booking.status == S.PENDIENTE_COTIZACION
    assert booking.deposit_required_cents == 0
    assert booking.hold_expires_at is None, "un turno a cotizar no retiene horario (D4-13)"
    (item,) = await neg.items(booking)
    # §3.2: el ítem guarda el precio base y el % del tenant, pero su seña es 0 hasta aceptar
    assert item.price_cents == BASE_TAPIZADO
    assert item.pricing_mode == PricingMode.A_COTIZAR
    assert item.deposit_bps == 2000
    quote = await neg.session.get(Quote, booking.quote_id)
    assert quote is not None and quote.status == QuoteStatus.PENDIENTE


async def test_al_aceptar_la_sena_es_sobre_el_acordado(neg):
    await neg.configurar(deposit_enabled=True, deposit_bps=2000)
    booking = await neg.turno(servicios=[neg.tapizado])
    await neg.cotizar(booking)
    quote = await neg.aceptar(booking)
    assert quote.status == QuoteStatus.ACEPTADO
    await neg.session.refresh(booking)
    assert booking.price_cents == ACORDADO
    assert booking.deposit_required_cents == SENA_ACORDADO_20
    assert booking.status == S.PENDIENTE_SENA
    assert booking.hold_expires_at is not None, "PENDIENTE_SEÑA con hold (R6: sale de hold_min)"


@pytest.mark.parametrize(
    "cambio",
    [{"deposit_bps": 5000}, {"deposit_enabled": False}, {"deposit_bps": 1}],
    ids=["sube-al-50", "la-apaga", "baja-al-0.01"],
)
async def test_al_aceptar_usa_el_porcentaje_guardado_al_reservar(neg, cambio):
    """§5 `_pass_to_booking`: su `deposit_bps` guardado × acordado, no el % vigente."""
    await neg.configurar(deposit_enabled=True, deposit_bps=2000)
    booking = await neg.turno(servicios=[neg.tapizado])
    await neg.configurar(**cambio)
    await neg.cotizar(booking)
    await neg.aceptar(booking)
    await neg.session.refresh(booking)
    assert booking.deposit_required_cents == SENA_ACORDADO_20
    assert booking.status == S.PENDIENTE_SENA


async def test_sena_apagada_al_reservar_no_se_pide_al_aceptar_aunque_se_prenda(neg):
    booking = await neg.turno(servicios=[neg.tapizado])
    await neg.configurar(deposit_enabled=True, deposit_bps=2000)
    await neg.cotizar(booking)
    await neg.aceptar(booking)
    await neg.session.refresh(booking)
    assert booking.deposit_required_cents == 0
    assert booking.status == S.CONFIRMADO
    assert booking.hold_expires_at is None


async def test_turno_mixto_al_aceptar_suma_sena_por_item(neg):
    """Adenda C1 + D4-7: lavado (20 % de su precio) + cotizado (20 % del acordado)."""
    await neg.configurar(deposit_enabled=True, deposit_bps=2000)
    booking = await neg.turno(servicios=[neg.lavado, neg.tapizado])
    assert booking.status == S.PENDIENTE_COTIZACION
    await neg.cotizar(booking)
    await neg.aceptar(booking)
    await neg.session.refresh(booking)
    assert booking.price_cents == PRECIO_IMPAR + ACORDADO
    assert booking.deposit_required_cents == SENA_IMPAR_20 + SENA_ACORDADO_20
    assert booking.duration_min == 60 + DURACION_ACORDADA


async def test_recibir_con_sena_impaga_funciona_y_el_saldo_es_el_precio(neg):
    """D4-5: se recibe igual; recibir sin seña no agrega cargo."""
    await neg.configurar(deposit_enabled=True, deposit_bps=2000)
    booking = await neg.turno()
    assert booking.status == S.PENDIENTE_SENA
    job = await neg.recibir(booking)
    assert booking.status == S.RECIBIDO
    assert job.base_price_cents == PRECIO_IMPAR
    assert await neg.jobs.balance(job.id) == PRECIO_IMPAR


async def test_sena_pagada_en_parte_y_saldo_es_precio_menos_pagos_netos(neg):
    await neg.configurar(deposit_enabled=True, deposit_bps=2000)
    booking = await neg.turno(servicios=[neg.lavado, neg.encerado])
    parcial = 150_000
    assert parcial < booking.deposit_required_cents
    await neg.turnos.confirm_deposit(booking.id, neg.pago(parcial), now=AHORA)
    job = await neg.recibir(booking)
    assert await neg.jobs.balance(job.id) == 2 * PRECIO_IMPAR - parcial


async def test_cotizado_recibido_el_saldo_es_el_acordado_menos_la_sena(neg):
    await neg.configurar(deposit_enabled=True, deposit_bps=2000)
    booking = await neg.turno(servicios=[neg.lavado, neg.tapizado])
    await neg.cotizar(booking)
    await neg.aceptar(booking)
    await neg.turnos.confirm_deposit(booking.id, neg.pago(SENA_IMPAR_20), now=AHORA)
    job = await neg.recibir(booking)
    assert job.base_price_cents == PRECIO_IMPAR + ACORDADO
    assert await neg.jobs.balance(job.id) == PRECIO_IMPAR + ACORDADO - SENA_IMPAR_20


# ══ A7b · Cotización ══════════════════════════════════════════════════════════

RECHAZOS = (GuardFailedError, InvalidTransition)


async def _pagos_de(neg: Negocio, booking: Booking) -> int:
    return int(
        await neg.session.scalar(
            select(func.count()).select_from(Payment).where(Payment.booking_id == booking.id)
        )
        or 0
    )


@pytest.mark.parametrize("sena", [True, False], ids=["sena-on", "sena-off"])
async def test_confirm_deposit_rechaza_un_turno_con_cotizacion_pendiente(neg, sena):
    """Aunque el ítem tenga precio base: la guarda no se apoya en `price_cents IS NULL`."""
    await neg.configurar(deposit_enabled=sena)
    booking = await neg.turno(servicios=[neg.lavado, neg.tapizado])
    with pytest.raises(RECHAZOS):
        await neg.turnos.confirm_deposit(booking.id, neg.pago(SENA_IMPAR_20), now=AHORA)
    await neg.session.refresh(booking)
    assert booking.status == S.PENDIENTE_COTIZACION
    assert await _pagos_de(neg, booking) == 0


@pytest.mark.parametrize("con_pago", [True, False], ids=["con-pago", "sin-pago"])
async def test_confirm_late_rechaza_un_turno_con_cotizacion_pendiente(neg, con_pago):
    await neg.configurar(deposit_enabled=True)
    booking = await neg.turno(servicios=[neg.tapizado])
    await neg.cotizar(booking)  # COTIZADO, todavía no ACEPTADO
    pago = neg.pago(SENA_ACORDADO_20) if con_pago else None
    with pytest.raises(RECHAZOS):
        await neg.turnos.confirm_late(booking.id, now=AHORA, payment=pago)
    await neg.session.refresh(booking)
    assert booking.status == S.PENDIENTE_COTIZACION
    assert await _pagos_de(neg, booking) == 0


@pytest.mark.parametrize("marcada", [True, False], ids=["marcada-VENCIDO", "solo-expires_at"])
async def test_con_la_cotizacion_vencida_no_se_confirma_ni_se_cobra_sena(neg, marcada):
    await neg.configurar(deposit_enabled=True)
    booking = await neg.turno(servicios=[neg.lavado, neg.tapizado])
    vence = AHORA + timedelta(hours=1)
    await neg.cotizar(booking, vence=vence)
    despues = vence + timedelta(minutes=1)
    if marcada:
        assert booking.quote_id is not None
        quote = await neg.cotizaciones.expire(booking.quote_id, now=despues)
        assert quote.status == QuoteStatus.VENCIDO
    with pytest.raises(RECHAZOS):
        await neg.turnos.confirm_deposit(booking.id, neg.pago(SENA_IMPAR_20), now=despues)
    with pytest.raises(RECHAZOS):
        await neg.turnos.confirm_late(booking.id, now=despues, payment=neg.pago(SENA_IMPAR_20))
    with pytest.raises(RECHAZOS):
        await neg.turnos.confirm_late(booking.id, now=despues)
    with pytest.raises(RECHAZOS):
        await neg.aceptar(booking, ahora=despues)
    assert await _pagos_de(neg, booking) == 0


async def test_con_la_cotizacion_aceptada_la_sena_si_se_registra(neg):
    """Contracara de los rechazos: la guarda deja pasar el turno con su cotización ACEPTADO."""
    await neg.configurar(deposit_enabled=True)
    booking = await neg.turno(servicios=[neg.tapizado])
    await neg.cotizar(booking)
    await neg.aceptar(booking)
    hecho = await neg.turnos.confirm_deposit(booking.id, neg.pago(SENA_ACORDADO_20), now=AHORA)
    assert hecho.booking.status == S.CONFIRMADO


@pytest.mark.parametrize(
    ("minimo", "faltan", "pasa"),
    [(5, 4, False), (5, 5, True), (60, 59, False), (60, 60, True), (0, 0, True)],
    ids=["default-4min", "default-5min", "60-59min", "60-60min", "cero-ya"],
)
async def test_reservar_con_menos_de_min_advance_falla(neg, minimo, faltan, pasa):
    await neg.configurar(min_advance_min=minimo)
    ahora = T - timedelta(minutes=faltan)
    if pasa:
        booking = await neg.turno(ahora=ahora)
        assert booking.status == S.CONFIRMADO
    else:
        with pytest.raises(DomainError):
            await neg.turno(ahora=ahora)
        cuantos = await neg.session.scalar(
            select(func.count()).select_from(Booking).where(Booking.tenant_id == neg.tenant_id)
        )
        assert cuantos == 0


async def test_min_advance_tambien_aplica_al_turno_a_cotizar(neg):
    await neg.configurar(min_advance_min=60)
    with pytest.raises(DomainError):
        await neg.turno(servicios=[neg.tapizado], ahora=T - timedelta(minutes=30))


# ══ A7b sobre Postgres: el EXCLUDE sin PENDIENTE_COTIZACION ══════════════════

Factory = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def fabrica(pg_admin_engine: Any, pg_clean: None) -> AsyncGenerator[Factory, None]:
    """Sesiones como `carwash_app` (RLS activo), una transacción por bloque."""
    engine = create_async_engine(
        _as_app_role(_with_driver(PG_TEST_URL, "asyncpg")), poolclass=NullPool
    )
    yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_neg(fabrica: Factory) -> Negocio:
    async with fabrica() as s, s.begin():
        return await armar(s)


async def _en_tx[R](fabrica: Factory, neg: Negocio, fn: Callable[[Negocio], Awaitable[R]]) -> R:
    async with fabrica() as s, s.begin():
        return await fn(neg.en(s))


@pytest.mark.postgres
@pytest.mark.asyncio(loop_scope="session")
async def test_pg_turno_a_cotizar_no_ocupa_el_puesto(fabrica, pg_neg):
    cotizar = await _en_tx(fabrica, pg_neg, lambda n: n.turno(servicios=[n.tapizado]))
    assert cotizar.status == S.PENDIENTE_COTIZACION

    # mismo puesto, mismo horario: entra (D4-13)
    otro = await _en_tx(fabrica, pg_neg, lambda n: n.turno(servicios=[n.lavado]))
    assert otro.status == S.CONFIRMADO

    # y un segundo a cotizar encima, también
    otro_cotizar = await _en_tx(fabrica, pg_neg, lambda n: n.turno(servicios=[n.tapizado]))
    assert otro_cotizar.status == S.PENDIENTE_COTIZACION


@pytest.mark.postgres
@pytest.mark.asyncio(loop_scope="session")
async def test_pg_aceptar_con_el_horario_tomado_choca_y_la_cotizacion_sigue_cotizado(
    fabrica, pg_neg
):
    async def preparar(n: Negocio) -> Booking:
        await n.configurar(deposit_enabled=True, deposit_bps=2000)
        booking = await n.turno(servicios=[n.tapizado])
        await n.cotizar(booking)
        return booking

    cotizar = await _en_tx(fabrica, pg_neg, preparar)
    # otro cliente toma el horario mientras tanto (el cotizado no lo retenía)
    await _en_tx(fabrica, pg_neg, lambda n: n.turno(servicios=[n.lavado]))

    async with fabrica() as s, s.begin():
        n = pg_neg.en(s)
        with pytest.raises(SlotTakenError):
            await n.aceptar(cotizar)

    async with fabrica() as s, s.begin():
        n = pg_neg.en(s)
        await set_tenant_context(s, n.tenant_id)
        quote = await s.get(Quote, cotizar.quote_id)
        booking = await s.get(Booking, cotizar.id)
        assert quote is not None and quote.status == QuoteStatus.COTIZADO
        assert booking is not None and booking.status == S.PENDIENTE_COTIZACION
        assert booking.deposit_required_cents == 0


@pytest.mark.postgres
@pytest.mark.asyncio(loop_scope="session")
async def test_pg_aceptar_con_el_horario_libre_ocupa_el_puesto(fabrica, pg_neg):
    async def preparar(n: Negocio) -> Booking:
        booking = await n.turno(servicios=[n.tapizado])
        await n.cotizar(booking)
        await n.aceptar(booking)
        return booking

    aceptado = await _en_tx(fabrica, pg_neg, preparar)
    assert aceptado.status == S.CONFIRMADO
    async with fabrica() as s, s.begin():
        with pytest.raises(SlotTakenError):
            await pg_neg.en(s).turno(servicios=[pg_neg.lavado])
    # en el otro puesto, sí
    libre = await _en_tx(
        fabrica, pg_neg, lambda n: n.turno(servicios=[n.lavado], puesto=n.puesto_2)
    )
    assert libre.status == S.CONFIRMADO
