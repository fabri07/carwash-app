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
from app.persistence.models import CashMovement, Payment, Quote
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


async def test_confirmacion_tardia_solo_desde_vencido(lav):
    booking = await lav.turno()
    with pytest.raises(InvalidTransition):
        await lav.turnos.confirm_late(booking.id, now=AHORA)
    pendiente = await _con_sena(lav, inicio=T + timedelta(hours=3))
    await lav.turnos.expire_holds(lav.puesto, HOLD)
    confirmado = await lav.turnos.confirm_late(pendiente.id, now=HOLD, payment=lav.pago(SENA))
    assert confirmado.status == S.CONFIRMADO


# ── Cancelaciones ────────────────────────────────────────────────────────────


async def test_cancelacion_normal_con_sena_abre_devolucion_pendiente(lav):
    booking = await _con_sena(lav)
    await lav.turnos.confirm_deposit(booking.id, lav.pago(SENA), now=AHORA)
    c = await lav.turnos.cancel_by_client(booking.id, now=T - timedelta(hours=13))
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
    c = await web.cancel_by_client(booking.id, now=T - timedelta(hours=2), reason="  Viaje  ")
    assert c.classification == CancellationClassification.TARDIA
    assert c.deposit_status == DepositStatus.SIN_PAGO
    assert c.actor_user_id is None
    assert c.reason == "Viaje"


async def test_cancelar_despues_de_la_hora_es_ausente_con_aviso(lav):
    booking = await _con_sena(lav)
    await lav.turnos.confirm_deposit(booking.id, lav.pago(SENA), now=AHORA)
    c = await lav.turnos.cancel_by_client(booking.id, now=T + timedelta(minutes=10))
    assert booking.status == S.AUSENTE_CON_AVISO_POSTERIOR
    assert c.classification == CancellationClassification.POSTERIOR_AL_TURNO
    assert c.anticipation_min == -10
    assert c.deposit_status == DepositStatus.EN_REVISION


async def test_una_cancelacion_por_turno_la_segunda_devuelve_la_existente(lav):
    booking = await lav.turno()
    uno = await lav.turnos.cancel_by_client(booking.id, now=AHORA)
    dos = await lav.turnos.cancel_by_client(booking.id, now=AHORA)
    assert dos.id == uno.id
    tres = await lav.turnos.cancel_operational(booking.id, requested_at=AHORA, reason="x")
    assert tres.id == uno.id


async def test_no_se_cancela_desde_vencido_ni_con_motivo_largo(lav):
    booking = await _con_sena(lav)
    await lav.turnos.expire_holds(lav.puesto, HOLD)
    with pytest.raises(InvalidTransition):  # [corregir] R-T-036
        await lav.turnos.cancel_by_client(booking.id, now=AHORA)
    otro = await lav.turno(inicio=T + timedelta(hours=3))
    with pytest.raises(GuardFailedError):
        await lav.turnos.cancel_by_client(otro.id, now=AHORA, reason="x" * 161)


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
        await otro.turnos.cancel_by_client(booking.id, now=AHORA)


async def test_precio_del_turno_es_snapshot(lav):
    booking = await _con_sena(lav)
    await lav.catalogo.set_price(
        lav.lavado_con_sena, lav.auto, price_cents=9_999_900, duration_min=30, deposit_bps=0
    )
    assert booking.price_cents == PRECIO_CON_SENA
    assert booking.duration_min == 90


# ── Arreglos de T3 (revisor adversarial y /code-review) ─────────────────────


async def test_f4_un_turno_a_cotizar_vencido_no_se_confirma_tarde(lav):
    booking = await lav.turno(servicio=lav.tapizado, hold=HOLD)
    await lav.turnos.expire_holds(lav.puesto, HOLD)
    assert booking.status == S.VENCIDO
    with pytest.raises(GuardFailedError, match="price"):
        await lav.turnos.confirm_late(booking.id, now=HOLD)
    with pytest.raises(GuardFailedError, match="price"):
        await lav.turnos.confirm_late(booking.id, now=HOLD, payment=lav.pago(SENA))
    assert booking.status == S.VENCIDO
    assert booking.price_cents is None


async def test_f4_la_confirmacion_tardia_exige_la_sena_si_el_turno_la_pide(lav):
    booking = await _con_sena(lav)
    await lav.turnos.expire_holds(lav.puesto, HOLD)
    with pytest.raises(GuardFailedError, match="deposit"):
        await lav.turnos.confirm_late(booking.id, now=HOLD)
    assert booking.status == S.VENCIDO
    await lav.turnos.confirm_late(booking.id, now=HOLD, payment=lav.pago(SENA))
    assert booking.status == S.CONFIRMADO


async def test_f4_sin_sena_requerida_la_confirmacion_tardia_no_acepta_un_pago(lav):
    booking = await _con_sena(lav)
    await lav.turnos.expire_holds(lav.puesto, HOLD)
    booking.deposit_required_cents = 0  # defensa: hoy un PENDIENTE_SEÑA siempre pide seña
    with pytest.raises(GuardFailedError, match="deposit"):
        await lav.turnos.confirm_late(booking.id, now=HOLD, payment=lav.pago(SENA))
    assert booking.status == S.VENCIDO
    assert (await lav.turnos.confirm_late(booking.id, now=HOLD)).status == S.CONFIRMADO


async def _horario_perdido(lav: Lavadero):
    """Turno con seña cuyo hold venció y otro cliente tomó el horario."""
    perdido = await _con_sena(lav)
    otro = await lav.turno(ahora=HOLD + timedelta(minutes=1))
    assert perdido.status == S.VENCIDO and otro.status == S.CONFIRMADO
    return perdido


async def test_f5_la_sena_que_llega_despues_de_perder_el_horario_queda_registrada(lav):
    perdido = await _horario_perdido(lav)
    tarde = HOLD + timedelta(minutes=2)
    c = await lav.turnos.record_deposit_after_slot_lost(
        perdido.id, lav.pago(SENA, "sena-tarde"), now=tarde
    )
    assert perdido.status == S.VENCIDO  # el turno no cambia
    assert c.booking_id == perdido.id
    assert c.initiator == CancellationInitiator.NEGOCIO
    assert c.classification == CancellationClassification.OPERATIVA
    assert c.deposit_status == DepositStatus.REPROGRAMACION_O_DEVOLUCION_PENDIENTE
    assert (c.previous_status, c.resulting_status) == (S.VENCIDO, S.VENCIDO)
    assert c.deposit_paid_cents == SENA
    assert c.requested_at == tarde
    assert c.actor_user_id == lav.owner_id
    assert "slot" in c.reason.lower() or "horario" in c.reason.lower()
    pagos = (
        await lav.session.scalars(select(Payment).where(Payment.booking_id == perdido.id))
    ).all()
    assert [(p.kind, p.amount_cents) for p in pagos] == [(PaymentKind.SENA, SENA)]
    # reenvío de la cola offline: sin efectos
    otra = await lav.turnos.record_deposit_after_slot_lost(
        perdido.id, lav.pago(SENA, "sena-tarde"), now=tarde
    )
    assert otra.id == c.id
    assert (
        len(
            (
                await lav.session.scalars(select(Payment).where(Payment.booking_id == perdido.id))
            ).all()
        )
        == 1
    )
    # el caso se resuelve como cualquier otro
    hecho = await lav.senas.resolve(
        c.id,
        status=DepositStatus.DEVUELTA,
        reason="Transferido",
        resolved_at=tarde,
        refund=lav.pago(SENA, "dev-tarde"),
    )
    assert hecho.deposit_status == DepositStatus.DEVUELTA


async def test_f5_solo_para_un_vencido_cuyo_horario_esta_tomado(lav):
    confirmado = await lav.turno(inicio=T + timedelta(hours=5))
    with pytest.raises(InvalidTransition):
        await lav.turnos.record_deposit_after_slot_lost(confirmado.id, lav.pago(SENA), now=HOLD)
    libre = await _con_sena(lav)
    await lav.turnos.expire_holds(lav.puesto, HOLD)
    with pytest.raises(GuardFailedError, match="confirm_late"):  # el horario sigue libre
        await lav.turnos.record_deposit_after_slot_lost(libre.id, lav.pago(SENA), now=HOLD)
    pagos = (await lav.session.scalars(select(Payment).where(Payment.booking_id == libre.id))).all()
    assert pagos == []


async def test_f5_la_clave_es_de_su_turno_y_un_segundo_pago_no_se_suma_en_silencio(lav):
    perdido = await _horario_perdido(lav)
    tarde = HOLD + timedelta(minutes=2)
    await lav.turnos.record_deposit_after_slot_lost(perdido.id, lav.pago(SENA, "s1"), now=tarde)
    with pytest.raises(GuardFailedError):  # ya hay un caso abierto: lo resuelve una persona
        await lav.turnos.record_deposit_after_slot_lost(perdido.id, lav.pago(SENA, "s2"), now=tarde)
    otro = await lav.turno(inicio=T + timedelta(hours=5))
    with pytest.raises(IdempotencyKeyReusedError):
        await lav.turnos.confirm_late(otro.id, now=tarde, payment=lav.pago(SENA, "s1"))
    # una clave de un pago de otra operación no sirve para registrar la seña tardía
    job = await lav.walk_in()
    await lav.jobs.record_payment(job.id, lav.pago(1_000, "cobro"))
    with pytest.raises(IdempotencyKeyReusedError):
        await lav.turnos.record_deposit_after_slot_lost(
            perdido.id, lav.pago(SENA, "cobro"), now=tarde
        )


async def test_f7_cancel_by_client_toma_la_hora_del_server(lav):
    booking = await _con_sena(lav)
    await lav.turnos.confirm_deposit(booking.id, lav.pago(SENA), now=AHORA)
    c = await lav.turnos.cancel_by_client(booking.id, now=T + timedelta(hours=2))
    assert c.requested_at == T + timedelta(hours=2)
    assert c.classification == CancellationClassification.POSTERIOR_AL_TURNO
