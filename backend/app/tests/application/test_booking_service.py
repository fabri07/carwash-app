"""FASE-3-CONTRATO §1.3, §2.1 y §4 — `BookingService` en la suite rápida.

El `EXCLUDE` y la carrera viven en `test_servicios_pg.py`: SQLite no los tiene.
"""

import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.application.services import BookingService
from app.application.services._base import as_aware
from app.application.services.errors import (
    AlreadyExistsError,
    CatalogIncoherentError,
    IdempotencyKeyReusedError,
    NotFoundError,
)
from app.domain.enums import (
    BookingStatus,
    CancellationClassification,
    CancellationInitiator,
    DepositStatus,
    PaymentKind,
    PricingMode,
    QuoteStatus,
)
from app.domain.exceptions import GuardFailedError, InvalidTransition
from app.persistence.models import CashMovement, Quote
from app.persistence.models.cancellation import DEFAULT_REASON
from app.tests.application._armado import (
    AHORA,
    PRECIO_CON_SENA,
    PRECIO_FIJO,
    SENA,
    SENA_BPS,
    Lavadero,
    T,
    armar,
)

S = BookingStatus
HOLD = AHORA + timedelta(minutes=30)


@pytest_asyncio.fixture
async def lav(db_session) -> Lavadero:
    return await armar(db_session)


async def _con_sena(lav: Lavadero, **kw):
    return await lav.turno(servicio=lav.lavado_con_sena, hold=HOLD, **kw)


# ── Alta ─────────────────────────────────────────────────────────────────────


async def test_alta_sin_sena_nace_confirmada_con_snapshots(lav):
    booking = await lav.turno(hold=HOLD)  # un hold en un confirmado se descarta
    assert booking.status == S.CONFIRMADO
    assert booking.hold_expires_at is None
    assert booking.price_cents == PRECIO_FIJO
    assert booking.duration_min == 60
    assert booking.end_at == T + timedelta(minutes=60)
    assert booking.deposit_required_cents == 0
    assert booking.service_name_snapshot == "Lavado completo"
    assert booking.terms_accepted_at is None  # el panel no estampa aceptación (C-15)


async def test_alta_con_sena_queda_pendiente_y_exige_hold(lav):
    with pytest.raises(GuardFailedError):
        await lav.turno(servicio=lav.lavado_con_sena)
    booking = await _con_sena(lav)
    assert booking.status == S.PENDIENTE_SENA
    assert booking.deposit_bps == SENA_BPS
    assert booking.deposit_required_cents == SENA
    assert booking.hold_expires_at == HOLD


async def test_alta_a_cotizar_crea_la_cotizacion_pendiente(lav):
    booking = await lav.turno(servicio=lav.tapizado, hold=HOLD)
    assert booking.status == S.PENDIENTE_COTIZACION
    assert booking.price_cents is None
    assert booking.deposit_required_cents == 0
    quote = await lav.session.get(Quote, booking.quote_id)
    assert quote is not None
    assert (quote.status, quote.booking_id) == (QuoteStatus.PENDIENTE, booking.id)
    assert quote.expires_at is not None and as_aware(quote.expires_at) == HOLD  # SQLite: sin zona


async def test_alta_con_codigo_repetido_choca(lav):
    await lav.turno(codigo="TUR-0001")
    with pytest.raises(AlreadyExistsError):
        await lav.turno(codigo="TUR-0001", puesto=lav.puesto_2)


async def test_alta_valida_catalogo_y_referencias(lav):
    servicio = await lav.catalogo.create_service("Solo SUV", PricingMode.PRECIO_FIJO)
    with pytest.raises(CatalogIncoherentError):
        await lav.turno(servicio=servicio.id)
    with pytest.raises(NotFoundError):
        await lav.turno(puesto=uuid.uuid4())
    with pytest.raises(NotFoundError):
        await lav.turno(vehiculo=uuid.uuid4())


async def test_expire_holds_vence_solo_los_vencidos_del_puesto(lav):
    vencido = await _con_sena(lav)
    vigente = await lav.turno(
        servicio=lav.lavado_con_sena, hold=HOLD + timedelta(hours=1), inicio=T + timedelta(hours=3)
    )
    otro_puesto = await _con_sena(lav, puesto=lav.puesto_2)
    vencidos = await lav.turnos.expire_holds(lav.puesto, HOLD)  # `<=`: justo en el borde vence
    assert vencidos == [vencido]
    assert vencido.status == S.VENCIDO
    assert vigente.status == S.PENDIENTE_SENA
    assert otro_puesto.status == S.PENDIENTE_SENA
    assert await lav.turnos.expire_holds(lav.puesto, HOLD) == []


async def test_bloqueo_de_agenda(lav):
    bloqueo = await lav.turnos.add_block(
        starts_at=T, ends_at=T + timedelta(hours=2), resource_id=lav.puesto, reason=" Feriado "
    )
    assert bloqueo.reason == "Feriado"
    assert bloqueo.created_by_user_id == lav.owner_id
    todo = await lav.turnos.add_block(starts_at=T, ends_at=T + timedelta(days=1))
    assert todo.resource_id is None
    assert todo.reason == "Bloqueo operativo"
    with pytest.raises(GuardFailedError):
        await lav.turnos.add_block(starts_at=T, ends_at=T)


# ── Seña ─────────────────────────────────────────────────────────────────────


async def test_confirmar_la_sena_registra_el_pago_y_confirma(lav):
    booking = await _con_sena(lav)
    hecho = await lav.turnos.confirm_deposit(booking.id, lav.pago(SENA, "s1"), now=AHORA)
    assert hecho.booking.status == S.CONFIRMADO
    assert booking.hold_expires_at is None
    assert hecho.payment.kind == PaymentKind.SENA
    assert hecho.payment.booking_id == booking.id
    assert hecho.payment.job_id is None
    caja = await lav.session.scalar(
        select(CashMovement).where(CashMovement.payment_id == hecho.payment.id)
    )
    assert caja is not None
    # reenvío: mismo pago, sin efectos
    otra_vez = await lav.turnos.confirm_deposit(booking.id, lav.pago(SENA, "s1"), now=AHORA)
    assert otra_vez.payment.id == hecho.payment.id


async def test_la_clave_de_una_sena_es_de_su_turno(lav):
    uno = await _con_sena(lav)
    dos = await _con_sena(lav, inicio=T + timedelta(hours=3))
    await lav.turnos.confirm_deposit(uno.id, lav.pago(SENA, "s1"), now=AHORA)
    with pytest.raises(IdempotencyKeyReusedError):
        await lav.turnos.confirm_deposit(dos.id, lav.pago(SENA, "s1"), now=AHORA)


async def test_sena_con_el_hold_vencido_no_confirma_y_va_por_confirmacion_tardia(lav):
    booking = await _con_sena(lav)
    tarde = HOLD + timedelta(minutes=1)
    with pytest.raises(InvalidTransition):
        await lav.turnos.confirm_deposit(booking.id, lav.pago(SENA), now=tarde)
    assert booking.status == S.VENCIDO  # el vencimiento perezoso sí quedó
    await lav.turnos.confirm_late(booking.id, now=tarde, payment=lav.pago(SENA, "tarde"))
    assert booking.status == S.CONFIRMADO
    assert booking.hold_expires_at is None
    # reenvío de la confirmación tardía con la misma seña: sin efectos
    assert (
        await lav.turnos.confirm_late(booking.id, now=tarde, payment=lav.pago(SENA, "tarde"))
    ).status == S.CONFIRMADO


async def test_confirmacion_tardia_sin_pago_y_solo_desde_vencido(lav):
    booking = await lav.turno()
    with pytest.raises(InvalidTransition):
        await lav.turnos.confirm_late(booking.id, now=AHORA)
    pendiente = await _con_sena(lav, inicio=T + timedelta(hours=3))
    await lav.turnos.expire_holds(lav.puesto, HOLD)
    assert (await lav.turnos.confirm_late(pendiente.id, now=HOLD)).status == S.CONFIRMADO


# ── Cancelaciones ────────────────────────────────────────────────────────────


async def test_cancelacion_normal_con_sena_abre_devolucion_pendiente(lav):
    booking = await _con_sena(lav)
    await lav.turnos.confirm_deposit(booking.id, lav.pago(SENA), now=AHORA)
    c = await lav.turnos.cancel_by_client(booking.id, requested_at=T - timedelta(hours=13))
    assert booking.status == S.CANCELADO_CLIENTE
    assert c.classification == CancellationClassification.NORMAL
    assert c.anticipation_min == 13 * 60
    assert c.deposit_paid_cents == SENA
    assert c.deposit_status == DepositStatus.DEVOLUCION_PENDIENTE
    assert c.previous_status == S.CONFIRMADO
    assert c.resulting_status == S.CANCELADO_CLIENTE
    assert c.initiator == CancellationInitiator.CLIENTE
    assert c.reason == DEFAULT_REASON
    assert c.actor_user_id == lav.owner_id


async def test_cancelacion_tardia_sin_sena_queda_sin_pago(lav):
    booking = await lav.turno()
    web = BookingService(lav.session, lav.tenant_id, None)  # desde la web: sin actor
    c = await web.cancel_by_client(
        booking.id, requested_at=T - timedelta(hours=2), reason="  Viaje  "
    )
    assert c.classification == CancellationClassification.TARDIA
    assert c.deposit_status == DepositStatus.SIN_PAGO
    assert c.actor_user_id is None
    assert c.reason == "Viaje"


async def test_cancelar_despues_de_la_hora_es_ausente_con_aviso(lav):
    booking = await _con_sena(lav)
    await lav.turnos.confirm_deposit(booking.id, lav.pago(SENA), now=AHORA)
    c = await lav.turnos.cancel_by_client(booking.id, requested_at=T + timedelta(minutes=10))
    assert booking.status == S.AUSENTE_CON_AVISO_POSTERIOR
    assert c.classification == CancellationClassification.POSTERIOR_AL_TURNO
    assert c.anticipation_min == -10
    assert c.deposit_status == DepositStatus.EN_REVISION


async def test_una_cancelacion_por_turno_la_segunda_devuelve_la_existente(lav):
    booking = await lav.turno()
    uno = await lav.turnos.cancel_by_client(booking.id, requested_at=AHORA)
    dos = await lav.turnos.cancel_by_client(booking.id, requested_at=AHORA)
    assert dos.id == uno.id
    tres = await lav.turnos.cancel_operational(booking.id, requested_at=AHORA, reason="x")
    assert tres.id == uno.id


async def test_no_se_cancela_desde_vencido_ni_con_motivo_largo(lav):
    booking = await _con_sena(lav)
    await lav.turnos.expire_holds(lav.puesto, HOLD)
    with pytest.raises(InvalidTransition):  # [corregir] R-T-036
        await lav.turnos.cancel_by_client(booking.id, requested_at=AHORA)
    otro = await lav.turno(inicio=T + timedelta(hours=3))
    with pytest.raises(GuardFailedError):
        await lav.turnos.cancel_by_client(otro.id, requested_at=AHORA, reason="x" * 161)


async def test_cancelacion_operativa_exige_motivo_y_actor(lav):
    booking = await _con_sena(lav)
    await lav.turnos.confirm_deposit(booking.id, lav.pago(SENA), now=AHORA)
    with pytest.raises(GuardFailedError):
        await lav.turnos.cancel_operational(booking.id, requested_at=AHORA, reason=" ")
    with pytest.raises(GuardFailedError):
        await BookingService(lav.session, lav.tenant_id, None).cancel_operational(
            booking.id, requested_at=AHORA, reason="Corte de agua"
        )
    c = await lav.turnos.cancel_operational(booking.id, requested_at=AHORA, reason="Corte de agua")
    assert booking.status == S.CANCELADO_OPERATIVO
    assert c.classification == CancellationClassification.OPERATIVA
    assert c.initiator == CancellationInitiator.NEGOCIO
    assert c.deposit_status == DepositStatus.REPROGRAMACION_O_DEVOLUCION_PENDIENTE


async def test_no_se_cancela_un_turno_recibido(lav):
    booking = await lav.turno()
    await lav.recibido(booking)
    with pytest.raises(InvalidTransition):
        await lav.turnos.cancel_operational(booking.id, requested_at=AHORA, reason="x")


async def test_inasistencia_solo_desde_confirmado(lav):
    booking = await lav.turno()
    assert (await lav.turnos.mark_no_show(booking.id)).status == S.NO_ASISTIO
    pendiente = await _con_sena(lav, inicio=T + timedelta(hours=3))
    with pytest.raises(InvalidTransition):
        await lav.turnos.mark_no_show(pendiente.id)


async def test_get_y_turno_ajeno(lav, db_session):
    booking = await lav.turno()
    assert (await lav.turnos.get(booking.id)).id == booking.id
    otro = await armar(db_session, "Otro")
    with pytest.raises(NotFoundError):
        await otro.turnos.get(booking.id)
    with pytest.raises(NotFoundError):
        await otro.turnos.cancel_by_client(booking.id, requested_at=AHORA)


async def test_precio_del_turno_es_snapshot(lav):
    booking = await _con_sena(lav)
    await lav.catalogo.set_price(
        lav.lavado_con_sena, lav.auto, price_cents=9_999_900, duration_min=30, deposit_bps=0
    )
    assert booking.price_cents == PRECIO_CON_SENA
    assert booking.duration_min == 90
