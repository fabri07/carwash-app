"""FASE-3-CONTRATO §2.2 y §4 — `JobService` en la suite rápida (SQLite).

Incluye los casos B13 del legacy que son de aplicación: segundo cobro suma; walk-in no se
cancela por demora; cancelar por demora un `FINALIZADO` falla; finalizar sin iniciar falla;
recibir dos veces el mismo turno devuelve el mismo job. Y A1 (anular un cobro de un `COBRADO`).
Lo que SQLite no puede (locks, carrera, EXCLUDE, trigger) está en `test_servicios_pg.py`.
"""

import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.application.services import JobService, PaymentInput
from app.application.services.errors import (
    CatalogIncoherentError,
    IdempotencyKeyReusedError,
    NotFoundError,
)
from app.domain.enums import (
    BookingStatus,
    CashDirection,
    CashMovementKind,
    Channel,
    DirtLevel,
    JobEventType,
    JobStatus,
    PaymentKind,
    PricingMode,
)
from app.domain.exceptions import (
    GuardFailedError,
    InvalidAmountError,
    InvalidDatetimeError,
    InvalidTransition,
)
from app.persistence.models import Booking, CashMovement, Job, JobEvent, Payment, Service
from app.tests.application._armado import (
    AHORA,
    COMISION_CREDITO_BPS,
    PRECIO_CON_SENA,
    PRECIO_FIJO,
    SENA,
    Lavadero,
    T,
    armar,
)

E = JobEventType


@pytest_asyncio.fixture
async def lav(db_session) -> Lavadero:
    return await armar(db_session)


async def _eventos(lav: Lavadero, job_id: uuid.UUID) -> list[JobEventType]:
    return [e.event_type for e in await lav.jobs.history(job_id)]


async def _turno_con_sena_pagada(lav: Lavadero) -> Booking:
    booking = await lav.turno(servicio=lav.lavado_con_sena, hold=AHORA + timedelta(minutes=30))
    await lav.turnos.confirm_deposit(booking.id, lav.pago(SENA), now=AHORA)
    return booking


# ── Recepción ────────────────────────────────────────────────────────────────


async def test_recibir_un_turno_toma_snapshots_demora_y_pasa_el_turno_a_recibido(lav):
    booking = await lav.turno()
    job = await lav.recibido(booking, llegada=T + timedelta(minutes=7, seconds=30))

    assert job.status == JobStatus.PRESENTE
    assert job.booking_id == booking.id
    assert job.base_price_cents == PRECIO_FIJO
    assert job.service_name_snapshot == "Lavado completo"
    assert job.scheduled_at == T
    assert job.arrival_delay_min == 7
    assert job.customer_id == lav.cliente
    assert job.channel == Channel.WHATSAPP
    assert job.resource_id == lav.puesto
    assert job.responsible_user_id == lav.owner_id
    assert booking.status == BookingStatus.RECIBIDO
    assert await _eventos(lav, job.id) == [E.JOB_RECEIVED]


async def test_llegar_antes_deja_demora_negativa(lav):
    job = await lav.recibido(llegada=T - timedelta(minutes=15))
    assert job.arrival_delay_min == -15


async def test_b13_recibir_dos_veces_el_mismo_turno_devuelve_el_mismo_job(lav):
    booking = await lav.turno()
    primero = await lav.recibido(booking, clave="rec-1")
    segundo = await lav.recibido(booking, clave="rec-2")  # otra clave, otro dispositivo
    assert segundo.id == primero.id
    assert await _eventos(lav, primero.id) == [E.JOB_RECEIVED]


async def test_reenvio_con_la_misma_clave_no_duplica(lav):
    booking = await lav.turno()
    primero = await lav.recibido(booking, clave="rec-x")
    otra_vez = await lav.recibido(booking, clave="rec-x")
    assert otra_vez.id == primero.id
    walk = await lav.walk_in(clave="walk-x")
    assert (await lav.walk_in(clave="walk-x")).id == walk.id


async def test_una_clave_de_recepcion_no_sirve_para_otro_turno(lav):
    await lav.recibido(await lav.turno(), clave="rec-y")
    otro = await lav.turno(inicio=T + timedelta(hours=3))
    with pytest.raises(IdempotencyKeyReusedError):
        await lav.recibido(otro, clave="rec-y")


async def test_una_clave_de_otra_operacion_no_sirve_para_recibir(lav):
    job = await lav.walk_in()
    await lav.jobs.start(job.id, idempotency_key="k-start", occurred_at=T)
    with pytest.raises(IdempotencyKeyReusedError):
        await lav.walk_in(clave="k-start")
    with pytest.raises(IdempotencyKeyReusedError):
        await lav.recibido(clave="k-start")


async def test_recibir_vincula_las_senas_del_turno_al_job(lav):
    booking = await _turno_con_sena_pagada(lav)
    job = await lav.recibido(booking)
    pagos = (
        await lav.session.scalars(select(Payment).where(Payment.booking_id == booking.id))
    ).all()
    assert [p.job_id for p in pagos] == [job.id]
    assert job.deposit_required_cents == SENA
    assert await lav.jobs.balance(job.id) == PRECIO_CON_SENA - SENA


async def test_recibir_con_sena_sin_pagar_esta_permitido_por_ahora(lav):
    # [abierto] pregunta 3: la tabla lo permite, la guarda es configurable en F6.
    booking = await lav.turno(servicio=lav.lavado_con_sena, hold=AHORA + timedelta(minutes=30))
    job = await lav.recibido(booking)
    assert job.status == JobStatus.PRESENTE


async def test_turno_sin_vehiculo_exige_uno_al_recibir(lav):
    booking = await lav.turno(vehiculo=None)
    with pytest.raises(GuardFailedError):
        await lav.recibido(booking)
    job = await lav.jobs.receive(
        idempotency_key="rec-v", arrived_at=T, booking_id=booking.id, vehicle_id=lav.vehiculo
    )
    assert job.vehicle_id == lav.vehiculo
    assert booking.vehicle_id == lav.vehiculo


async def test_no_se_recibe_un_turno_cancelado(lav):
    booking = await lav.turno()
    await lav.turnos.cancel_operational(booking.id, requested_at=AHORA, reason="Lluvia")
    with pytest.raises(InvalidTransition):
        await lav.recibido(booking)


async def test_a_cotizar_no_se_recibe_sin_cotizacion_aceptada(lav):
    booking = await lav.turno(servicio=lav.tapizado, hold=AHORA + timedelta(hours=2))
    with pytest.raises(InvalidTransition):  # PENDIENTE_COTIZACION no es recibible
        await lav.recibido(booking)
    assert booking.quote_id is not None
    await lav.cotizaciones.quote(
        booking.quote_id, agreed_price_cents=4_500_000, agreed_duration_min=150, quoted_at=AHORA
    )
    await lav.cotizaciones.accept(booking.quote_id, decided_at=AHORA, now=AHORA)
    job = await lav.recibido(booking)
    assert job.base_price_cents == 4_500_000  # hereda el precio acordado (R-C-029)
    assert job.quote_id == booking.quote_id


async def test_walk_in_toma_el_precio_del_catalogo_y_no_tiene_demora(lav):
    job = await lav.walk_in()
    assert job.booking_id is None
    assert job.scheduled_at is None
    assert job.arrival_delay_min is None
    assert job.base_price_cents == PRECIO_FIJO
    assert job.channel == Channel.CALLE
    assert job.customer_id is None  # [abierto] pregunta 1


async def test_walk_in_exige_vehiculo_tamano_servicio_y_canal(lav):
    with pytest.raises(GuardFailedError):
        await lav.jobs.receive(idempotency_key="w", arrived_at=T, vehicle_id=lav.vehiculo)


async def test_walk_in_a_cotizar_exige_cotizacion_aceptada_del_mismo_servicio(lav):
    base = {
        "arrived_at": T,
        "vehicle_id": lav.vehiculo,
        "vehicle_size_id": lav.auto,
        "service_id": lav.tapizado,
        "channel": Channel.CALLE,
        "customer_id": lav.cliente,
    }
    with pytest.raises(GuardFailedError):
        await lav.jobs.receive(idempotency_key="w1", **base)
    quote = await lav.cotizaciones.create(
        customer_id=lav.cliente,
        service_id=lav.tapizado,
        vehicle_size_id=lav.auto,
        requested_at=AHORA,
    )
    with pytest.raises(GuardFailedError):  # PENDIENTE, no ACEPTADO
        await lav.jobs.receive(idempotency_key="w2", quote_id=quote.id, **base)
    await lav.cotizaciones.quote(
        quote.id, agreed_price_cents=5_000_000, agreed_duration_min=180, quoted_at=AHORA
    )
    await lav.cotizaciones.accept(quote.id, decided_at=AHORA, now=AHORA)
    with pytest.raises(GuardFailedError):  # otro tamaño
        await lav.jobs.receive(
            idempotency_key="w3", quote_id=quote.id, **(base | {"vehicle_size_id": lav.suv})
        )
    job = await lav.jobs.receive(idempotency_key="w4", quote_id=quote.id, **base)
    assert job.base_price_cents == 5_000_000
    assert job.quote_id == quote.id


async def test_walk_in_sin_precio_de_catalogo_es_incoherencia(lav):
    servicio = await lav.catalogo.create_service("Solo SUV", "PRECIO_FIJO")
    await lav.catalogo.set_price(servicio.id, lav.suv, price_cents=100_000, duration_min=30)
    with pytest.raises(CatalogIncoherentError):
        await lav.jobs.receive(
            idempotency_key="w",
            arrived_at=T,
            vehicle_id=lav.vehiculo,
            vehicle_size_id=lav.auto,
            service_id=servicio.id,
            channel=Channel.CALLE,
        )


async def test_recibir_valida_referencias_del_tenant(lav):
    with pytest.raises(NotFoundError):
        await lav.jobs.receive(idempotency_key="r", arrived_at=T, booking_id=uuid.uuid4())
    with pytest.raises(NotFoundError):
        await lav.jobs.receive(
            idempotency_key="r", arrived_at=T, booking_id=uuid.uuid4(), resource_id=uuid.uuid4()
        )
    with pytest.raises(NotFoundError):
        await lav.jobs.receive(idempotency_key="r", arrived_at=T, responsible_user_id=uuid.uuid4())


async def test_hora_sin_zona_y_clave_vacia_se_rechazan(lav):
    with pytest.raises(InvalidDatetimeError):
        await lav.jobs.receive(idempotency_key="r", arrived_at=T.replace(tzinfo=None))
    with pytest.raises(GuardFailedError):
        await lav.jobs.receive(idempotency_key="   ", arrived_at=T)


async def test_sin_actor_no_se_opera_un_job(lav):
    anonimo = JobService(lav.session, lav.tenant_id, None)
    with pytest.raises(GuardFailedError):
        await anonimo.receive(idempotency_key="r", arrived_at=T)


def test_el_tenant_tiene_que_ser_un_uuid(lav):
    with pytest.raises(TypeError):
        JobService(lav.session, str(lav.tenant_id), lav.owner_id)


# ── Transiciones ─────────────────────────────────────────────────────────────


async def test_ciclo_completo_con_un_solo_cobro(lav):
    booking = await lav.turno()
    job = await lav.recibido(booking)
    await lav.jobs.start(job.id, idempotency_key="s", occurred_at=T + timedelta(minutes=5))
    assert job.status == JobStatus.EN_PROCESO
    assert job.started_at == T + timedelta(minutes=5)
    await lav.jobs.finish(job.id, idempotency_key="f", occurred_at=T + timedelta(minutes=50))
    assert job.status == JobStatus.FINALIZADO
    assert booking.status == BookingStatus.ATENDIDO
    await lav.jobs.record_payment(job.id, lav.pago(PRECIO_FIJO, "p"))
    assert job.status == JobStatus.COBRADO
    assert job.settled_at == T
    await lav.jobs.pick_up(job.id, idempotency_key="r", occurred_at=T + timedelta(hours=1))
    assert job.status == JobStatus.RETIRADO
    assert await _eventos(lav, job.id) == [
        E.JOB_RECEIVED,
        E.JOB_STARTED,
        E.JOB_FINISHED,
        E.PAYMENT_RECORDED,
        E.JOB_SETTLED,
        E.JOB_PICKED_UP,
    ]
    eventos = await lav.jobs.history(job.id)
    assert {e.idempotency_key for e in eventos} >= {"p", "p:settled"}
    assert all(e.actor_user_id == lav.owner_id for e in eventos)
    assert eventos[1].from_status == JobStatus.PRESENTE
    assert eventos[1].to_status == JobStatus.EN_PROCESO
    assert eventos[3].from_status is None and eventos[3].to_status is None


async def test_b13_finalizar_sin_iniciar_falla(lav):
    job = await lav.walk_in()
    with pytest.raises(InvalidTransition):
        await lav.jobs.finish(job.id, idempotency_key="f", occurred_at=T)


async def test_reenviar_una_transicion_no_la_repite(lav):
    job = await lav.walk_in()
    await lav.jobs.start(job.id, idempotency_key="s", occurred_at=T)
    await lav.jobs.start(job.id, idempotency_key="s", occurred_at=T)  # reenvío de la cola
    with pytest.raises(InvalidTransition):
        await lav.jobs.start(job.id, idempotency_key="s2", occurred_at=T)
    assert await _eventos(lav, job.id) == [E.JOB_RECEIVED, E.JOB_STARTED]


async def test_una_clave_usada_en_otro_job_no_se_acepta(lav):
    uno = await lav.walk_in()
    dos = await lav.walk_in()
    await lav.jobs.start(uno.id, idempotency_key="s", occurred_at=T)
    with pytest.raises(IdempotencyKeyReusedError):
        await lav.jobs.start(dos.id, idempotency_key="s", occurred_at=T)


async def test_finalizar_con_saldo_cero_encadena_el_cobro(lav):
    job = await lav.walk_in()
    await lav.jobs.record_payment(job.id, lav.pago(PRECIO_FIJO))  # pagó por adelantado
    assert job.status == JobStatus.PRESENTE  # cobrar no finaliza
    await lav.finalizado(job)
    assert job.status == JobStatus.COBRADO
    assert (await _eventos(lav, job.id))[-2:] == [E.JOB_FINISHED, E.JOB_SETTLED]


async def test_no_se_retira_sin_cobrar(lav):
    job = await lav.finalizado()
    with pytest.raises(InvalidTransition):
        await lav.jobs.pick_up(job.id, idempotency_key="r", occurred_at=T)


async def test_job_ajeno_o_inexistente_no_existe(lav, db_session):
    otro = await armar(db_session, "Otro lavadero")
    job = await otro.walk_in()
    with pytest.raises(NotFoundError):
        await lav.jobs.start(job.id, idempotency_key="s", occurred_at=T)
    with pytest.raises(NotFoundError):
        await lav.jobs.get(job.id)


# ── Cobros ───────────────────────────────────────────────────────────────────


async def test_b13_segundo_cobro_suma_no_pisa(lav):
    job = await lav.finalizado()
    await lav.jobs.record_payment(job.id, lav.pago(1_200_000, "p1"))
    assert job.status == JobStatus.FINALIZADO
    assert await lav.jobs.balance(job.id) == 800_000
    await lav.jobs.record_payment(job.id, lav.pago(800_000, "p2", medio=lav.credito))
    assert await lav.jobs.balance(job.id) == 0
    assert job.status == JobStatus.COBRADO
    pagos = (await lav.session.scalars(select(Payment).where(Payment.job_id == job.id))).all()
    assert sorted(p.amount_cents for p in pagos) == [800_000, 1_200_000]


async def test_la_comision_se_calcula_con_la_tasa_snapshot_y_hay_movimiento_de_caja(lav):
    job = await lav.walk_in()
    pago = await lav.jobs.record_payment(job.id, lav.pago(1_000_000, medio=lav.credito))
    assert pago.commission_bps == COMISION_CREDITO_BPS
    assert pago.commission_cents == 50_000
    assert pago.kind == PaymentKind.SALDO
    caja = await lav.session.scalar(select(CashMovement).where(CashMovement.payment_id == pago.id))
    assert caja is not None
    assert (caja.direction, caja.kind, caja.amount_cents) == (
        CashDirection.ENTRADA,
        CashMovementKind.COBRO,
        1_000_000,
    )


async def test_reenviar_un_cobro_devuelve_el_mismo_pago(lav):
    job = await lav.walk_in()
    uno = await lav.jobs.record_payment(job.id, lav.pago(500_000, "p"))
    dos = await lav.jobs.record_payment(job.id, lav.pago(500_000, "p"))
    assert uno.id == dos.id
    assert await lav.jobs.balance(job.id) == PRECIO_FIJO - 500_000


async def test_la_clave_de_una_sena_no_sirve_para_un_cobro(lav):
    booking = await lav.turno(servicio=lav.lavado_con_sena, hold=AHORA + timedelta(minutes=30))
    await lav.turnos.confirm_deposit(booking.id, lav.pago(SENA, "sena-1"), now=AHORA)
    job = await lav.walk_in()
    with pytest.raises(IdempotencyKeyReusedError):
        await lav.jobs.record_payment(job.id, lav.pago(1_000, "sena-1"))


async def test_devolucion_resta_y_sale_de_caja(lav):
    # Un saldo a favor ya no nace de cobrar de más (F8): nace de un descuento posterior.
    job = await lav.walk_in()
    await lav.jobs.record_payment(job.id, lav.pago(PRECIO_FIJO))
    await lav.jobs.adjust_price(
        job.id,
        idempotency_key="desc",
        occurred_at=T,
        surcharge_cents=0,
        discount_cents=500_000,
        reason="Promo",
    )
    job = await lav.finalizado(job)
    assert job.status == JobStatus.FINALIZADO  # saldo -500.000: a favor del cliente
    devolucion = await lav.jobs.record_payment(
        job.id, lav.pago(500_000), kind=PaymentKind.DEVOLUCION
    )
    assert job.status == JobStatus.COBRADO
    caja = await lav.session.scalar(
        select(CashMovement).where(CashMovement.payment_id == devolucion.id)
    )
    assert caja is not None and caja.direction == CashDirection.SALIDA


async def test_un_cobro_no_puede_ser_sena_ni_de_importe_no_positivo(lav):
    job = await lav.walk_in()
    with pytest.raises(GuardFailedError):
        await lav.jobs.record_payment(job.id, lav.pago(100), kind=PaymentKind.SENA)
    with pytest.raises(InvalidAmountError):
        await lav.jobs.record_payment(job.id, lav.pago(0))


async def test_medio_solo_de_egresos_no_cobra(lav):
    medio = await lav.catalogo.create_payment_method(
        "CAJA_CHICA", "Caja chica", for_income=False, for_expense=True
    )
    job = await lav.walk_in()
    with pytest.raises(GuardFailedError):
        await lav.jobs.record_payment(job.id, lav.pago(100, medio=medio.id))
    with pytest.raises(NotFoundError):
        await lav.jobs.record_payment(job.id, lav.pago(100, medio=uuid.uuid4()))


# ── Anular cobros (A1) ───────────────────────────────────────────────────────


async def test_a1_anular_un_cobro_de_un_cobrado_que_deja_saldo_falla(lav):
    job = await lav.finalizado()
    pago = await lav.jobs.record_payment(job.id, lav.pago(PRECIO_FIJO))
    assert job.status == JobStatus.COBRADO
    with pytest.raises(GuardFailedError):
        await lav.jobs.void_payment(pago.id, idempotency_key="v", occurred_at=T, reason="Error")
    assert pago.voided_at is None


async def test_a1_anular_en_un_cobrado_lo_que_deja_saldo_cero_si_se_puede(lav):
    # Un cobro duplicado ya no entra (F8). El caso que A1 deja pasar: la devolución y el
    # re-cobro de un COBRADO se cargaron los dos por error; anulados, el saldo vuelve a 0.
    job = await lav.walk_in()
    await lav.jobs.start(job.id, idempotency_key="ini", occurred_at=T)
    await lav.jobs.record_payment(job.id, lav.pago(PRECIO_FIJO))
    devolucion = await lav.jobs.record_payment(
        job.id, lav.pago(500_000), kind=PaymentKind.DEVOLUCION
    )
    recobro = await lav.jobs.record_payment(job.id, lav.pago(500_000))
    await lav.jobs.finish(job.id, idempotency_key="fin", occurred_at=T)
    assert job.status == JobStatus.COBRADO
    await lav.jobs.void_payment(
        devolucion.id, idempotency_key="v1", occurred_at=T, reason="No hubo devolución"
    )
    anulado = await lav.jobs.void_payment(
        recobro.id, idempotency_key="v", occurred_at=T, reason="Cargado dos veces"
    )
    assert anulado.voided_at is not None
    assert await lav.jobs.balance(job.id) == 0
    caja = await lav.session.scalar(
        select(CashMovement).where(CashMovement.payment_id == recobro.id)
    )
    assert caja is not None and caja.voided_at is not None
    evento = (await lav.jobs.history(job.id))[-1]
    assert evento.event_type == E.PAYMENT_VOIDED
    assert evento.event_metadata["reason"] == "Cargado dos veces"
    # reenvío: sin efectos
    otra_vez = await lav.jobs.void_payment(
        recobro.id, idempotency_key="v", occurred_at=T, reason="Cargado dos veces"
    )
    assert otra_vez.id == recobro.id
    with pytest.raises(GuardFailedError):  # ya anulado, con otra clave
        await lav.jobs.void_payment(recobro.id, idempotency_key="v2", occurred_at=T, reason="x")


async def test_anular_en_curso_reabre_el_saldo(lav):
    job = await lav.walk_in()
    pago = await lav.jobs.record_payment(job.id, lav.pago(PRECIO_FIJO))
    await lav.jobs.void_payment(pago.id, idempotency_key="v", occurred_at=T, reason="Error")
    assert await lav.jobs.balance(job.id) == PRECIO_FIJO


async def test_anular_una_devolucion_en_finalizado_puede_cerrar_el_cobro(lav):
    job = await lav.finalizado()
    await lav.jobs.record_payment(job.id, lav.pago(PRECIO_FIJO - 1_000))
    devolucion = await lav.jobs.record_payment(job.id, lav.pago(1_000), kind=PaymentKind.DEVOLUCION)
    await lav.jobs.record_payment(job.id, lav.pago(2_000))  # saldo 0 → COBRADO
    assert job.status == JobStatus.COBRADO
    # anular la devolución deja saldo -1.000 (a favor): no viola A1
    await lav.jobs.void_payment(devolucion.id, idempotency_key="v", occurred_at=T, reason="Error")
    assert await lav.jobs.balance(job.id) == -1_000


async def test_anular_exige_motivo_y_pago_de_un_job(lav):
    job = await lav.walk_in()
    pago = await lav.jobs.record_payment(job.id, lav.pago(1_000))
    with pytest.raises(GuardFailedError):
        await lav.jobs.void_payment(pago.id, idempotency_key="v", occurred_at=T, reason="  ")
    booking = await lav.turno(servicio=lav.lavado_con_sena, hold=AHORA + timedelta(minutes=30))
    sena = await lav.turnos.confirm_deposit(booking.id, lav.pago(SENA), now=AHORA)
    with pytest.raises(GuardFailedError):
        await lav.jobs.void_payment(
            sena.payment.id, idempotency_key="v", occurred_at=T, reason="Error"
        )
    with pytest.raises(NotFoundError):
        await lav.jobs.void_payment(uuid.uuid4(), idempotency_key="v", occurred_at=T, reason="x")


# ── Cancelación por demora y retención ───────────────────────────────────────


async def test_b13_walk_in_no_se_cancela_por_demora(lav):
    job = await lav.walk_in()
    with pytest.raises(GuardFailedError):
        await lav.jobs.cancel_for_delay(
            job.id, idempotency_key="c", occurred_at=T + timedelta(hours=1), tolerance_min=20
        )


async def test_b13_cancelar_por_demora_un_finalizado_falla(lav):
    job = await lav.finalizado(await lav.recibido())
    with pytest.raises(InvalidTransition):
        await lav.jobs.cancel_for_delay(
            job.id, idempotency_key="c", occurred_at=T + timedelta(hours=1), tolerance_min=20
        )


async def test_la_demora_se_recalcula_con_el_momento_del_evento(lav):
    job = await lav.recibido(llegada=T + timedelta(minutes=25))  # llegó 25 tarde
    with pytest.raises(GuardFailedError):  # pero el toque dice 20: igual no alcanza
        await lav.jobs.cancel_for_delay(
            job.id, idempotency_key="c", occurred_at=T + timedelta(minutes=20), tolerance_min=20
        )


async def test_cancelar_por_demora_retiene_la_sena_y_cancela_el_turno(lav):
    booking = await _turno_con_sena_pagada(lav)
    job = await lav.recibido(booking, llegada=T + timedelta(minutes=30))
    await lav.jobs.cancel_for_delay(
        job.id,
        idempotency_key="c",
        occurred_at=T + timedelta(minutes=31),
        tolerance_min=20,
        reason="Llegó tarde",
    )
    assert job.status == JobStatus.CANCELADO_DEMORA
    assert booking.status == BookingStatus.CANCELADO_DEMORA
    eventos = await lav.jobs.history(job.id)
    cancelado, retenido = eventos[-2:]
    assert cancelado.event_type == E.JOB_CANCELLED_DELAY
    assert cancelado.event_metadata == {
        "delay_min": 31,
        "tolerance_min": 20,
        "reason": "Llegó tarde",
    }
    assert retenido.event_type == E.DEPOSIT_RETAINED
    assert retenido.idempotency_key == "c:retained"
    assert retenido.event_metadata == {"amount_cents": SENA}
    # un job cancelado por demora no cobra
    with pytest.raises(InvalidTransition):
        await lav.jobs.record_payment(job.id, lav.pago(1_000))
    # reenvío sin efectos
    await lav.jobs.cancel_for_delay(
        job.id, idempotency_key="c", occurred_at=T + timedelta(minutes=31), tolerance_min=20
    )
    assert len(await lav.jobs.history(job.id)) == len(eventos)


async def test_revertir_la_retencion_exige_motivo_y_una_retencion_vigente(lav):
    job = await lav.recibido(llegada=T + timedelta(minutes=30))
    with pytest.raises(InvalidTransition):  # todavía no está cancelado
        await lav.jobs.reverse_retention(job.id, idempotency_key="r", occurred_at=T, reason="x")
    await lav.jobs.cancel_for_delay(
        job.id, idempotency_key="c", occurred_at=T + timedelta(minutes=40), tolerance_min=20
    )
    with pytest.raises(GuardFailedError):
        await lav.jobs.reverse_retention(job.id, idempotency_key="r", occurred_at=T, reason=" ")
    await lav.jobs.reverse_retention(
        job.id, idempotency_key="r", occurred_at=T, reason="Avisó por WhatsApp"
    )
    ultimo = (await lav.jobs.history(job.id))[-1]
    assert ultimo.event_type == E.DEPOSIT_RETENTION_REVERSED
    assert ultimo.event_metadata["reason"] == "Avisó por WhatsApp"
    assert ultimo.event_metadata["amount_cents"] == 0
    await lav.jobs.reverse_retention(job.id, idempotency_key="r", occurred_at=T, reason="x")
    with pytest.raises(GuardFailedError):  # ya no hay retención vigente
        await lav.jobs.reverse_retention(job.id, idempotency_key="r2", occurred_at=T, reason="x")


# ── Inspección y ajuste ──────────────────────────────────────────────────────


async def test_la_inspeccion_es_opcional_y_la_segunda_actualiza(lav):
    job = await lav.walk_in()
    primera = await lav.jobs.record_inspection(
        job.id,
        idempotency_key="i1",
        occurred_at=T,
        dirt_level=DirtLevel.BARRO,
        checklist={"llantas": True},
    )
    segunda = await lav.jobs.record_inspection(
        job.id, idempotency_key="i2", occurred_at=T, pre_existing_damage="Rayón puerta"
    )
    assert segunda.id == primera.id
    assert segunda.dirt_level is None  # reemplaza el formulario entero
    assert segunda.pre_existing_damage == "Rayón puerta"
    assert segunda.inspected_by_user_id == lav.owner_id
    tercera = await lav.jobs.record_inspection(job.id, idempotency_key="i2", occurred_at=T)
    assert tercera.id == primera.id
    eventos = [e for e in await lav.jobs.history(job.id) if e.event_type == E.INSPECTION_RECORDED]
    assert [e.event_metadata["updated"] for e in eventos] == [False, True]


async def test_ajustar_precio_exige_motivo_y_estado_previo_al_cobro(lav):
    job = await lav.walk_in()
    with pytest.raises(GuardFailedError):
        await lav.jobs.adjust_price(
            job.id,
            idempotency_key="a",
            occurred_at=T,
            surcharge_cents=0,
            discount_cents=1,
            reason="",
        )
    with pytest.raises(InvalidAmountError):
        await lav.jobs.adjust_price(
            job.id,
            idempotency_key="a",
            occurred_at=T,
            surcharge_cents=-1,
            discount_cents=0,
            reason="x",
        )
    with pytest.raises(InvalidAmountError):  # total <= 0 (R-O-003)
        await lav.jobs.adjust_price(
            job.id,
            idempotency_key="a",
            occurred_at=T,
            surcharge_cents=0,
            discount_cents=PRECIO_FIJO,
            reason="x",
        )
    await lav.jobs.adjust_price(
        job.id,
        idempotency_key="a",
        occurred_at=T,
        surcharge_cents=300_000,
        discount_cents=100_000,
        reason="Barro extra, cliente frecuente",
    )
    assert (job.surcharge_cents, job.discount_cents) == (300_000, 100_000)
    assert job.discount_reason == "Barro extra, cliente frecuente"
    assert await lav.jobs.balance(job.id) == PRECIO_FIJO + 200_000
    await lav.jobs.adjust_price(
        job.id, idempotency_key="a", occurred_at=T, surcharge_cents=0, discount_cents=0, reason="x"
    )
    assert job.surcharge_cents == 300_000  # reenvío sin efectos


async def test_un_descuento_que_salda_un_finalizado_lo_cobra(lav):
    job = await lav.finalizado()
    await lav.jobs.record_payment(job.id, lav.pago(PRECIO_FIJO - 100_000))
    await lav.jobs.adjust_price(
        job.id,
        idempotency_key="a",
        occurred_at=T,
        surcharge_cents=0,
        discount_cents=100_000,
        reason="Redondeo",
    )
    assert job.status == JobStatus.COBRADO
    assert job.discount_reason == "Redondeo"
    with pytest.raises(InvalidTransition):  # A3: después de cobrar no se ajusta
        await lav.jobs.adjust_price(
            job.id,
            idempotency_key="a2",
            occurred_at=T,
            surcharge_cents=1,
            discount_cents=0,
            reason="x",
        )


async def test_la_historia_es_del_job_y_del_tenant(lav):
    job = await lav.walk_in()
    eventos = await lav.jobs.history(job.id)
    assert len(eventos) == 1
    assert isinstance(eventos[0], JobEvent)
    with pytest.raises(NotFoundError):
        await lav.jobs.history(uuid.uuid4())


async def test_payment_input_es_inmutable(lav):
    pago = PaymentInput(1, lav.efectivo, "k", T)
    with pytest.raises(AttributeError):
        pago.amount_cents = 2


# ── Arreglos de T3 (revisor adversarial y /code-review) ─────────────────────


async def test_f1_una_devolucion_no_supera_lo_pagado_neto_vivo(lav):
    job = await lav.walk_in()
    with pytest.raises(InvalidAmountError):  # no se pagó nada: no hay qué devolver
        await lav.jobs.record_payment(job.id, lav.pago(50_000_000), kind=PaymentKind.DEVOLUCION)
    pago = await lav.jobs.record_payment(job.id, lav.pago(500_000))
    with pytest.raises(InvalidAmountError):
        await lav.jobs.record_payment(job.id, lav.pago(500_001), kind=PaymentKind.DEVOLUCION)
    await lav.jobs.record_payment(job.id, lav.pago(200_000), kind=PaymentKind.DEVOLUCION)
    with pytest.raises(InvalidAmountError):  # neto vivo: 500.000 − 200.000
        await lav.jobs.record_payment(job.id, lav.pago(300_001), kind=PaymentKind.DEVOLUCION)
    await lav.jobs.void_payment(pago.id, idempotency_key="v", occurred_at=T, reason="Error")
    with pytest.raises(InvalidAmountError):  # el anulado no cuenta
        await lav.jobs.record_payment(job.id, lav.pago(1), kind=PaymentKind.DEVOLUCION)
    assert await lav.jobs.balance(job.id) == PRECIO_FIJO + 200_000


async def test_f1_una_devolucion_que_deja_deuda_en_un_cobrado_se_rechaza(lav):
    job = await lav.finalizado()
    await lav.jobs.record_payment(job.id, lav.pago(PRECIO_FIJO))
    assert job.status == JobStatus.COBRADO
    with pytest.raises(GuardFailedError, match="A1"):
        await lav.jobs.record_payment(job.id, lav.pago(1_500_000), kind=PaymentKind.DEVOLUCION)
    assert await lav.jobs.balance(job.id) == 0
    pagos = (await lav.session.scalars(select(Payment).where(Payment.job_id == job.id))).all()
    assert len(pagos) == 1
    await lav.jobs.pick_up(job.id, idempotency_key="ret", occurred_at=T)
    with pytest.raises(GuardFailedError, match="A1"):  # tampoco en RETIRADO
        await lav.jobs.record_payment(job.id, lav.pago(1), kind=PaymentKind.DEVOLUCION)


async def test_f1_en_un_cobrado_la_devolucion_de_un_saldo_a_favor_si_entra(lav):
    job = await lav.finalizado()
    await lav.jobs.record_payment(job.id, lav.pago(PRECIO_FIJO - 1_000))
    devolucion = await lav.jobs.record_payment(job.id, lav.pago(1_000), kind=PaymentKind.DEVOLUCION)
    await lav.jobs.record_payment(job.id, lav.pago(2_000))
    assert job.status == JobStatus.COBRADO
    await lav.jobs.void_payment(devolucion.id, idempotency_key="v", occurred_at=T, reason="Error")
    assert await lav.jobs.balance(job.id) == -1_000  # a favor del cliente
    await lav.jobs.record_payment(job.id, lav.pago(1_000), kind=PaymentKind.DEVOLUCION)
    assert await lav.jobs.balance(job.id) == 0


async def test_f2_no_se_cancela_por_demora_con_cobros_vivos(lav):
    booking = await lav.turno()
    job = await lav.recibido(booking, llegada=T + timedelta(minutes=40))
    pago = await lav.jobs.record_payment(job.id, lav.pago(PRECIO_FIJO))
    with pytest.raises(GuardFailedError, match="payment"):
        await lav.jobs.cancel_for_delay(
            job.id, idempotency_key="c", occurred_at=T + timedelta(minutes=41), tolerance_min=20
        )
    assert job.status == JobStatus.PRESENTE
    assert booking.status == BookingStatus.RECIBIDO
    # anulado el cobro, se cancela
    await lav.jobs.void_payment(pago.id, idempotency_key="v", occurred_at=T, reason="Error")
    await lav.jobs.cancel_for_delay(
        job.id, idempotency_key="c", occurred_at=T + timedelta(minutes=41), tolerance_min=20
    )
    assert job.status == JobStatus.CANCELADO_DEMORA


async def test_f2_un_cobro_devuelto_no_impide_cancelar_por_demora(lav):
    booking = await _turno_con_sena_pagada(lav)
    job = await lav.recibido(booking, llegada=T + timedelta(minutes=40))
    await lav.jobs.record_payment(job.id, lav.pago(1_000_000))
    await lav.jobs.record_payment(job.id, lav.pago(1_000_000), kind=PaymentKind.DEVOLUCION)
    await lav.jobs.cancel_for_delay(
        job.id, idempotency_key="c", occurred_at=T + timedelta(minutes=41), tolerance_min=20
    )
    assert job.status == JobStatus.CANCELADO_DEMORA
    retenido = (await lav.jobs.history(job.id))[-1]
    assert retenido.event_metadata == {"amount_cents": SENA}  # la seña, no el saldo


async def _cotizacion_aceptada(lav: Lavadero, precio: int = 5_000_000):
    quote = await lav.cotizaciones.create(
        customer_id=lav.cliente,
        service_id=lav.tapizado,
        vehicle_size_id=lav.auto,
        requested_at=AHORA,
    )
    await lav.cotizaciones.quote(
        quote.id, agreed_price_cents=precio, agreed_duration_min=120, quoted_at=AHORA
    )
    await lav.cotizaciones.accept(quote.id, decided_at=AHORA, now=AHORA)
    return quote


def _walk_in_cotizado(lav: Lavadero, clave: str, quote_id: uuid.UUID):
    return lav.jobs.receive(
        idempotency_key=clave,
        arrived_at=T,
        vehicle_id=lav.vehiculo,
        vehicle_size_id=lav.auto,
        service_id=lav.tapizado,
        channel=Channel.CALLE,
        quote_id=quote_id,
    )


async def test_f3_una_cotizacion_se_usa_en_un_solo_job(lav):
    quote = await _cotizacion_aceptada(lav)
    primero = await _walk_in_cotizado(lav, "w1", quote.id)
    with pytest.raises(GuardFailedError, match="quote"):
        await _walk_in_cotizado(lav, "w2", quote.id)
    assert (await _walk_in_cotizado(lav, "w1", quote.id)).id == primero.id  # reenvío
    jobs = (await lav.session.scalars(select(Job).where(Job.quote_id == quote.id))).all()
    assert [j.id for j in jobs] == [primero.id]


async def test_f3_la_cotizacion_de_un_turno_no_sirve_para_un_walk_in(lav):
    booking = await lav.turno(servicio=lav.tapizado, hold=AHORA + timedelta(hours=2))
    assert booking.quote_id is not None
    await lav.cotizaciones.quote(
        booking.quote_id, agreed_price_cents=100_000, agreed_duration_min=60, quoted_at=AHORA
    )
    await lav.cotizaciones.accept(booking.quote_id, decided_at=AHORA, now=AHORA)
    with pytest.raises(GuardFailedError, match="booking"):
        await _walk_in_cotizado(lav, "w", booking.quote_id)
    job = await lav.recibido(booking)
    assert job.quote_id == booking.quote_id


async def test_f3_recibir_un_turno_exige_la_cotizacion_del_turno(lav):
    booking = await lav.turno(servicio=lav.tapizado, hold=AHORA + timedelta(hours=2))
    assert booking.quote_id is not None
    await lav.cotizaciones.quote(
        booking.quote_id, agreed_price_cents=100_000, agreed_duration_min=60, quoted_at=AHORA
    )
    await lav.cotizaciones.accept(booking.quote_id, decided_at=AHORA, now=AHORA)
    suelta = await _cotizacion_aceptada(lav)
    with pytest.raises(GuardFailedError, match="quote"):
        await lav.jobs.receive(
            idempotency_key="r", arrived_at=T, booking_id=booking.id, quote_id=suelta.id
        )
    job = await lav.jobs.receive(
        idempotency_key="r", arrived_at=T, booking_id=booking.id, quote_id=booking.quote_id
    )
    assert (job.quote_id, job.base_price_cents) == (booking.quote_id, 100_000)


async def test_f3_la_cotizacion_del_turno_ya_usada_por_otro_job_es_error_de_dominio(lav):
    """La red final es el único parcial `(tenant_id, quote_id)`: si otro job ya tiene la
    cotización (carrera, dato migrado), recibir el turno falla con error de dominio, no 500."""
    booking = await lav.turno(servicio=lav.tapizado, hold=AHORA + timedelta(hours=2))
    assert booking.quote_id is not None
    await lav.cotizaciones.quote(
        booking.quote_id, agreed_price_cents=100_000, agreed_duration_min=60, quoted_at=AHORA
    )
    await lav.cotizaciones.accept(booking.quote_id, decided_at=AHORA, now=AHORA)
    intruso = await lav.walk_in()
    intruso.quote_id = booking.quote_id  # lo que dejaría una carrera o una migración
    await lav.session.flush()
    with pytest.raises(GuardFailedError, match="quote"):
        await lav.recibido(booking)
    assert booking.status == BookingStatus.CONFIRMADO


async def test_f8_un_cobro_mayor_al_saldo_pendiente_se_rechaza(lav):
    job = await lav.finalizado()
    with pytest.raises(InvalidAmountError):
        await lav.jobs.record_payment(job.id, lav.pago(PRECIO_FIJO + 100))
    assert job.status == JobStatus.FINALIZADO
    await lav.jobs.record_payment(job.id, lav.pago(PRECIO_FIJO - 100))
    with pytest.raises(InvalidAmountError):
        await lav.jobs.record_payment(job.id, lav.pago(101))
    await lav.jobs.record_payment(job.id, lav.pago(100))
    assert job.status == JobStatus.COBRADO
    with pytest.raises(InvalidAmountError):  # el cobro duplicado ya no entra
        await lav.jobs.record_payment(job.id, lav.pago(PRECIO_FIJO))


async def test_f13_recibir_decide_por_el_snapshot_del_turno_no_por_el_catalogo(lav):
    booking = await lav.turno()  # precio fijo, reservado con precio
    servicio = await lav.session.get(Service, lav.lavado)
    assert servicio is not None
    servicio.pricing_mode = PricingMode.A_COTIZAR  # el catálogo cambió después de reservar
    await lav.session.flush()
    job = await lav.recibido(booking)
    assert (job.base_price_cents, job.quote_id) == (PRECIO_FIJO, None)
