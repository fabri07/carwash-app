"""FASE-3-CONTRATO §2.3 (cotizaciones) y §2.4 + A2 (resolución de la seña)."""

import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.application.services import DepositResolutionService, QuoteService
from app.application.services._base import as_aware
from app.application.services.errors import IdempotencyKeyReusedError, NotFoundError
from app.domain.enums import (
    BookingStatus,
    CashDirection,
    DepositStatus,
    PaymentKind,
    QuoteStatus,
    SuppliesPurchaser,
)
from app.domain.exceptions import GuardFailedError, InvalidAmountError, InvalidTransition
from app.persistence.models import CashMovement, Payment
from app.tests.application._armado import AHORA, SENA, Lavadero, T, armar

Q = QuoteStatus
HOLD = AHORA + timedelta(hours=2)


@pytest_asyncio.fixture
async def lav(db_session) -> Lavadero:
    return await armar(db_session)


async def _suelta(lav: Lavadero, **kw):
    return await lav.cotizaciones.create(
        customer_id=lav.cliente,
        service_id=lav.tapizado,
        vehicle_size_id=lav.auto,
        requested_at=AHORA,
        vehicle_id=lav.vehiculo,
        **kw,
    )


async def _cotizada(lav: Lavadero, quote_id, precio=4_000_000, minutos=180):
    return await lav.cotizaciones.quote(
        quote_id, agreed_price_cents=precio, agreed_duration_min=minutos, quoted_at=AHORA
    )


# ── Cotizaciones ─────────────────────────────────────────────────────────────


async def test_cotizar_exige_precio_y_duracion(lav):
    quote = await _suelta(lav)
    assert quote.status == Q.PENDIENTE
    with pytest.raises(GuardFailedError):  # [corregir] R-C-024: nunca 0
        await lav.cotizaciones.quote(
            quote.id, agreed_price_cents=0, agreed_duration_min=60, quoted_at=AHORA
        )
    with pytest.raises(InvalidAmountError):
        await lav.cotizaciones.quote(
            quote.id,
            agreed_price_cents=1,
            agreed_duration_min=60,
            quoted_at=AHORA,
            supplies_cost_cents=-1,
        )
    await lav.cotizaciones.quote(
        quote.id,
        agreed_price_cents=4_000_000,
        agreed_duration_min=180,
        quoted_at=AHORA,
        supplies_purchaser=SuppliesPurchaser.CLIENTE,
        supplies_cost_cents=0,
        expires_at=AHORA + timedelta(days=7),
    )
    assert quote.status == Q.COTIZADO
    assert quote.supplies_purchaser == SuppliesPurchaser.CLIENTE
    with pytest.raises(InvalidTransition):
        await _cotizada(lav, quote.id)


async def test_aceptar_sin_turno_solo_decide(lav):
    quote = await _suelta(lav)
    with pytest.raises(InvalidTransition):  # no se acepta lo no cotizado
        await lav.cotizaciones.accept(quote.id, decided_at=AHORA, now=AHORA)
    await _cotizada(lav, quote.id)
    await lav.cotizaciones.accept(quote.id, decided_at=AHORA, now=AHORA)
    assert quote.status == Q.ACEPTADO
    assert quote.decided_by_user_id == lav.owner_id


async def test_aceptar_con_turno_pasa_precio_y_duracion_y_confirma(lav):
    booking = await lav.turno(servicio=lav.tapizado, hold=HOLD)
    assert booking.quote_id is not None
    await _cotizada(lav, booking.quote_id, precio=4_500_000, minutos=150)
    quote = await lav.cotizaciones.accept(booking.quote_id, decided_at=AHORA, now=AHORA)
    assert quote.status == Q.ACEPTADO
    assert booking.status == BookingStatus.CONFIRMADO
    assert booking.price_cents == 4_500_000
    assert booking.duration_min == 150
    assert booking.end_at == T + timedelta(minutes=150)
    assert booking.deposit_required_cents == 0
    assert booking.hold_expires_at is None


async def test_aceptar_con_seña_deja_el_turno_esperando_la_sena(lav):
    booking = await lav.turno(servicio=lav.tapizado, hold=HOLD)
    booking.deposit_bps = 2000  # un A_COTIZAR con seña es incoherente en catálogo, no acá
    assert booking.quote_id is not None
    await _cotizada(lav, booking.quote_id, precio=1_000_000)
    nuevo_hold = AHORA + timedelta(minutes=30)
    await lav.cotizaciones.accept(
        booking.quote_id, decided_at=AHORA, now=AHORA, hold_expires_at=nuevo_hold
    )
    assert booking.status == BookingStatus.PENDIENTE_SENA
    assert booking.deposit_required_cents == 200_000
    assert booking.hold_expires_at == nuevo_hold


async def test_aceptar_con_el_hold_del_turno_vencido_falla(lav):
    booking = await lav.turno(servicio=lav.tapizado, hold=HOLD)
    assert booking.quote_id is not None
    # La cotización sigue vigente (F6 la rechazaría antes): lo que venció es el turno.
    await lav.cotizaciones.quote(
        booking.quote_id,
        agreed_price_cents=4_000_000,
        agreed_duration_min=180,
        quoted_at=AHORA,
        expires_at=HOLD + timedelta(days=1),
    )
    with pytest.raises(InvalidTransition):
        await lav.cotizaciones.accept(booking.quote_id, decided_at=HOLD, now=HOLD)


async def test_rechazar_cancelar_y_vencer(lav):
    rechazada = await _suelta(lav)
    await _cotizada(lav, rechazada.id)
    await lav.cotizaciones.reject(rechazada.id, decided_at=AHORA)
    assert rechazada.status == Q.RECHAZADO
    cancelada = await _suelta(lav)
    await lav.cotizaciones.cancel(cancelada.id, decided_at=AHORA)
    assert cancelada.status == Q.CANCELADO
    with pytest.raises(InvalidTransition):
        await lav.cotizaciones.reject(cancelada.id, decided_at=AHORA)

    sin_ttl = await _suelta(lav)
    with pytest.raises(GuardFailedError):
        await lav.cotizaciones.expire(sin_ttl.id, now=AHORA)
    con_ttl = await _suelta(lav, expires_at=AHORA + timedelta(days=1))
    with pytest.raises(GuardFailedError):
        await lav.cotizaciones.expire(con_ttl.id, now=AHORA)
    await lav.cotizaciones.expire(con_ttl.id, now=AHORA + timedelta(days=1))
    assert con_ttl.status == Q.VENCIDO


async def test_cotizacion_con_referencias_ajenas_no_existe(lav):
    with pytest.raises(NotFoundError):
        await lav.cotizaciones.create(
            customer_id=uuid.uuid4(),
            service_id=lav.tapizado,
            vehicle_size_id=lav.auto,
            requested_at=AHORA,
        )
    with pytest.raises(NotFoundError):
        await lav.cotizaciones.create(
            customer_id=lav.cliente,
            service_id=lav.tapizado,
            vehicle_size_id=lav.auto,
            requested_at=AHORA,
            vehicle_id=uuid.uuid4(),
        )
    with pytest.raises(GuardFailedError):
        await QuoteService(lav.session, lav.tenant_id, None).cancel(
            (await _suelta(lav)).id, decided_at=AHORA
        )


# ── Resolución de la seña (§2.4, A2) ─────────────────────────────────────────


async def _cancelada(lav: Lavadero, *, cuando, con_sena=True, operativa=False):
    booking = await lav.turno(servicio=lav.lavado_con_sena, hold=AHORA + timedelta(minutes=30))
    if con_sena:
        await lav.turnos.confirm_deposit(booking.id, lav.pago(SENA), now=AHORA)
    if operativa:
        return await lav.turnos.cancel_operational(booking.id, requested_at=cuando, reason="Lluvia")
    return await lav.turnos.cancel_by_client(booking.id, now=cuando)


async def test_devuelta_registra_la_devolucion_y_cierra(lav):
    c = await _cancelada(lav, cuando=AHORA)
    assert c.deposit_status == DepositStatus.DEVOLUCION_PENDIENTE
    with pytest.raises(GuardFailedError):  # DEVUELTA sin pago de devolución
        await lav.senas.resolve(
            c.id, status=DepositStatus.DEVUELTA, reason="Transferido", resolved_at=T
        )
    with pytest.raises(InvalidAmountError):
        await lav.senas.resolve(
            c.id,
            status=DepositStatus.DEVUELTA,
            reason="Transferido",
            resolved_at=T,
            refund=lav.pago(SENA + 1),
        )
    hecho = await lav.senas.resolve(
        c.id,
        status=DepositStatus.DEVUELTA,
        reason="Transferido",
        resolved_at=T,
        refund=lav.pago(SENA, "dev-1"),
    )
    assert hecho.deposit_status == DepositStatus.DEVUELTA
    assert hecho.resolved_by_user_id == lav.owner_id
    assert hecho.resolution_reason == "Transferido"
    devolucion = await lav.session.get(Payment, hecho.refund_payment_id)
    assert devolucion is not None
    assert (devolucion.kind, devolucion.booking_id) == (PaymentKind.DEVOLUCION, c.booking_id)
    caja = await lav.session.scalar(
        select(CashMovement).where(CashMovement.payment_id == devolucion.id)
    )
    assert caja is not None and caja.direction == CashDirection.SALIDA
    # reenvío con la misma clave: sin efectos
    otra = await lav.senas.resolve(
        c.id,
        status=DepositStatus.DEVUELTA,
        reason="Transferido",
        resolved_at=T,
        refund=lav.pago(SENA, "dev-1"),
    )
    assert otra.refund_payment_id == hecho.refund_payment_id
    with pytest.raises(InvalidTransition):  # un cierre no se reabre
        await lav.senas.resolve(c.id, status=DepositStatus.REPROGRAMADA, reason="x", resolved_at=T)


async def test_a2_retenida_solo_desde_en_revision(lav):
    a_tiempo = await _cancelada(lav, cuando=AHORA)
    with pytest.raises(InvalidTransition):
        await lav.senas.resolve(
            a_tiempo.id, status=DepositStatus.RETENIDA, reason="x", resolved_at=T
        )
    operativa = await _cancelada(lav, cuando=AHORA, operativa=True)
    with pytest.raises(InvalidTransition):
        await lav.senas.resolve(
            operativa.id, status=DepositStatus.RETENIDA, reason="x", resolved_at=T
        )
    posterior = await _cancelada(lav, cuando=T + timedelta(minutes=5))
    assert posterior.deposit_status == DepositStatus.EN_REVISION
    with pytest.raises(GuardFailedError):  # motivo obligatorio
        await lav.senas.resolve(
            posterior.id, status=DepositStatus.RETENIDA, reason=" ", resolved_at=T
        )
    with pytest.raises(GuardFailedError):  # la devolución solo acompaña a DEVUELTA
        await lav.senas.resolve(
            posterior.id,
            status=DepositStatus.RETENIDA,
            reason="Avisó tarde",
            resolved_at=T,
            refund=lav.pago(SENA),
        )
    hecho = await lav.senas.resolve(
        posterior.id, status=DepositStatus.RETENIDA, reason="Avisó tarde", resolved_at=T
    )
    assert hecho.deposit_status == DepositStatus.RETENIDA


async def test_reprogramada_y_sin_pago(lav):
    operativa = await _cancelada(lav, cuando=AHORA, operativa=True)
    hecho = await lav.senas.resolve(
        operativa.id, status=DepositStatus.REPROGRAMADA, reason="Pasa al jueves", resolved_at=T
    )
    assert hecho.deposit_status == DepositStatus.REPROGRAMADA
    sin_pago = await _cancelada(lav, cuando=AHORA, con_sena=False)
    assert sin_pago.deposit_status == DepositStatus.SIN_PAGO
    with pytest.raises(InvalidTransition):
        await lav.senas.resolve(
            sin_pago.id, status=DepositStatus.DEVUELTA, reason="x", resolved_at=T
        )


async def test_resolver_exige_actor_y_clave_propia(lav):
    c = await _cancelada(lav, cuando=AHORA)
    with pytest.raises(GuardFailedError):
        await DepositResolutionService(lav.session, lav.tenant_id, None).resolve(
            c.id, status=DepositStatus.REPROGRAMADA, reason="x", resolved_at=T
        )
    otra = await _cancelada(lav, cuando=AHORA)
    await lav.senas.resolve(
        c.id,
        status=DepositStatus.DEVUELTA,
        reason="x",
        resolved_at=T,
        refund=lav.pago(SENA, "dev"),
    )
    with pytest.raises(IdempotencyKeyReusedError):
        await lav.senas.resolve(
            otra.id,
            status=DepositStatus.DEVUELTA,
            reason="x",
            resolved_at=T,
            refund=lav.pago(SENA, "dev"),
        )
    with pytest.raises(NotFoundError):
        await lav.senas.resolve(
            uuid.uuid4(), status=DepositStatus.DEVUELTA, reason="x", resolved_at=T
        )


# ── Arreglos de T3 (revisor adversarial y /code-review) ─────────────────────


async def test_f6_una_cotizacion_vencida_no_se_acepta(lav):
    vence = AHORA + timedelta(hours=1)
    quote = await _suelta(lav, expires_at=vence)
    await _cotizada(lav, quote.id)
    with pytest.raises(GuardFailedError, match="expired"):  # mismo comparador `<=` del hold
        await lav.cotizaciones.accept(quote.id, decided_at=vence, now=vence)
    assert quote.status == Q.COTIZADO
    antes = vence - timedelta(seconds=1)
    await lav.cotizaciones.accept(quote.id, decided_at=antes, now=antes)
    assert quote.status == Q.ACEPTADO


async def test_f9_reenviar_el_mismo_cierre_devuelve_el_caso_sin_efectos(lav):
    c = await _cancelada(lav, cuando=T + timedelta(minutes=5))
    assert c.deposit_status == DepositStatus.EN_REVISION
    hecho = await lav.senas.resolve(
        c.id, status=DepositStatus.RETENIDA, reason="No vino", resolved_at=T
    )
    otra = await lav.senas.resolve(
        c.id,
        status=DepositStatus.RETENIDA,
        reason="Otro motivo",
        resolved_at=T + timedelta(hours=1),
    )
    assert otra.id == hecho.id
    assert otra.resolution_reason == "No vino"  # sin efectos
    assert as_aware(otra.resolved_at) == T
    with pytest.raises(InvalidTransition):  # un cierre distinto sigue fallando
        await lav.senas.resolve(c.id, status=DepositStatus.REPROGRAMADA, reason="x", resolved_at=T)
