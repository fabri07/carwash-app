"""FASE-6-CONTRATO §3 y §5 — `JobService.record_retrospective` y `OperationService` en la suite
rápida (SQLite), con reloj fijo (`T` = lunes 21/9/2026 10:00 UTC, `AHORA` el día anterior).

Lo que SQLite no puede (locks, carrera, `EXCLUDE`) está en `test_operacion_pg.py`.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.application.services import ManualDeposit, RetroPayment
from app.application.services._base import as_aware
from app.application.services.errors import NotFoundError
from app.application.services.operation_service import DAY_CAP, cancel_delay_from
from app.domain.enums import (
    BookingSource,
    BookingStatus,
    Channel,
    JobEntryMode,
    JobEventType,
    JobStatus,
)
from app.domain.exceptions import (
    GuardFailedError,
    InvalidAmountError,
    InvalidDatetimeError,
    InvalidTransition,
)
from app.persistence.models import Customer, Job, JobEvent, Payment, Vehicle
from app.tests.application._armado import AHORA, PRECIO_FIJO, Lavadero, T, armar

E = JobEventType
#: Una carga hecha "mañana" del turno de referencia: todo lo de T es pasado.
AHORA_RETRO = T + timedelta(days=1)


@pytest_asyncio.fixture
async def lav(db_session) -> Lavadero:
    return await armar(db_session)


def _k() -> str:
    return f"k-{uuid.uuid4()}"


async def _retro(lav: Lavadero, **pisar):
    datos = {
        "idempotency_key": _k(),
        "started_at": T,
        "finished_at": T + timedelta(hours=1),
        "now": AHORA_RETRO,
        "vehicle_id": lav.vehiculo,
        "vehicle_size_id": lav.auto,
        "service_ids": [lav.lavado],
        **pisar,
    }
    return await lav.jobs.record_retrospective(**datos)


async def _eventos(lav: Lavadero, job_id: uuid.UUID) -> list[JobEvent]:
    return await lav.jobs.history(job_id)


# ── JobService.record_retrospective ──────────────────────────────────────────


async def test_la_carga_retrospectiva_crea_el_job_y_sus_eventos_con_la_hora_real(lav):
    job = await _retro(lav, customer_id=lav.cliente, resource_id=lav.puesto)
    assert job.entry_mode == JobEntryMode.RETROSPECTIVA
    assert job.status == JobStatus.FINALIZADO  # cobro pendiente: "debe"
    assert [as_aware(x) for x in (job.arrived_at, job.started_at, job.finished_at)] == [
        T,
        T,
        T + timedelta(hours=1),
    ]
    assert job.resource_id == lav.puesto and job.customer_id == lav.cliente
    assert job.channel == Channel.CARGA_MANUAL and job.scheduled_at is None
    eventos = await _eventos(lav, job.id)
    assert [e.event_type for e in eventos] == [E.JOB_RECEIVED, E.JOB_STARTED, E.JOB_FINISHED]
    assert [as_aware(e.occurred_at) for e in eventos] == [T, T, T + timedelta(hours=1)]
    assert all(e.event_metadata["manual_entry"] is True for e in eventos)
    assert eventos[0].event_metadata["source"] == "WALK_IN"
    assert await lav.jobs.balance(job.id) == PRECIO_FIJO


async def test_con_el_cobro_total_queda_cobrado_y_el_cobro_lleva_la_hora_de_fin(lav):
    job = await _retro(lav, payment=RetroPayment(payment_method_id=lav.efectivo))
    assert job.status == JobStatus.COBRADO
    assert job.settled_at is not None and as_aware(job.settled_at) == T + timedelta(hours=1)
    eventos = await _eventos(lav, job.id)
    assert [e.event_type for e in eventos][-2:] == [E.PAYMENT_RECORDED, E.JOB_SETTLED]
    assert all(e.event_metadata["manual_entry"] is True for e in eventos)  # también el encadenado
    assert await lav.jobs.balance(job.id) == 0


async def test_con_un_cobro_parcial_queda_finalizado_con_el_resto_pendiente(lav):
    job = await _retro(
        lav, payment=RetroPayment(payment_method_id=lav.efectivo, amount_cents=500_000)
    )
    assert job.status == JobStatus.FINALIZADO
    assert await lav.jobs.balance(job.id) == PRECIO_FIJO - 500_000


async def test_un_cobro_de_mas_en_la_carga_retrospectiva_no_deja_nada(lav):
    with pytest.raises(InvalidAmountError):
        await _retro(
            lav,
            payment=RetroPayment(payment_method_id=lav.efectivo, amount_cents=PRECIO_FIJO + 1),
        )


async def test_la_marca_manual_no_se_filtra_a_las_acciones_en_vivo_que_siguen(lav):
    retro = await _retro(lav, payment=RetroPayment(payment_method_id=lav.efectivo))
    en_vivo = await lav.walk_in(llegada=AHORA_RETRO)
    await lav.jobs.start(en_vivo.id, idempotency_key=_k(), occurred_at=AHORA_RETRO)
    assert en_vivo.entry_mode == JobEntryMode.EN_VIVO
    for e in await _eventos(lav, en_vivo.id):
        assert "manual_entry" not in e.event_metadata
    # Y un toque en vivo sobre la carga (retirarla al otro día) tampoco es carga manual.
    await lav.jobs.pick_up(retro.id, idempotency_key=_k(), occurred_at=AHORA_RETRO)
    ultimo = (await _eventos(lav, retro.id))[-1]
    assert ultimo.event_type == E.JOB_PICKED_UP and "manual_entry" not in ultimo.event_metadata


async def test_la_marca_manual_se_apaga_aunque_la_carga_falle(lav):
    with pytest.raises(GuardFailedError):
        await _retro(lav, finished_at=T - timedelta(minutes=1))
    assert lav.jobs._manual is False
    with pytest.raises(NotFoundError):
        await _retro(lav, vehicle_id=uuid.uuid4())
    assert lav.jobs._manual is False


async def test_la_carga_retrospectiva_exige_horas_coherentes_y_pasadas(lav):
    with pytest.raises(GuardFailedError, match="before"):
        await _retro(lav, finished_at=T - timedelta(seconds=1))
    with pytest.raises(GuardFailedError, match="past"):
        await _retro(lav, finished_at=AHORA_RETRO + timedelta(minutes=6))
    with pytest.raises(GuardFailedError, match="past"):
        await _retro(
            lav,
            started_at=AHORA_RETRO + timedelta(hours=1),
            finished_at=AHORA_RETRO + timedelta(hours=2),
        )
    ok = await _retro(lav, started_at=T, finished_at=T)  # inicio == fin se acepta
    assert ok.finished_at == T
    ahora_exacto = await _retro(lav, finished_at=AHORA_RETRO)  # "ahora" también
    assert ahora_exacto.finished_at == AHORA_RETRO
    # Un reloj de celular apenas adelantado (< 5 min) se acota a "ahora": no se pierde la carga.
    adelantado = await _retro(lav, finished_at=AHORA_RETRO + timedelta(seconds=30))
    assert adelantado.finished_at == AHORA_RETRO
    assert await _count_jobs(lav) == 3  # lo rechazado no dejó filas


async def _count_jobs(lav: Lavadero) -> int:
    rows = await lav.session.scalars(select(Job).where(Job.tenant_id == lav.tenant_id))
    return len(list(rows.all()))


async def test_las_horas_sin_zona_se_rechazan(lav):
    with pytest.raises(InvalidDatetimeError):
        await _retro(lav, started_at=datetime(2026, 9, 21, 10))


async def test_sin_vehiculo_la_carga_retrospectiva_falla_como_un_walk_in(lav):
    with pytest.raises(GuardFailedError, match="walk-in"):
        await _retro(lav, vehicle_id=None)


async def test_un_servicio_a_cotizar_no_se_carga_retrospectivamente(lav):
    with pytest.raises(GuardFailedError, match="quote"):
        await _retro(lav, service_ids=[lav.tapizado])


async def test_la_carga_retrospectiva_es_idempotente_por_clave(lav):
    clave = _k()
    uno = await _retro(
        lav, idempotency_key=clave, payment=RetroPayment(payment_method_id=lav.efectivo)
    )
    dos = await _retro(
        lav, idempotency_key=clave, payment=RetroPayment(payment_method_id=lav.efectivo)
    )
    assert uno.id == dos.id
    assert await _count_jobs(lav) == 1
    assert len(await _eventos(lav, uno.id)) == 5
    # El reenvío no necesita ni validar de nuevo: aun con el reloj atrasado devuelve lo cargado.
    tres = await _retro(lav, idempotency_key=clave, now=T - timedelta(days=30), vehicle_id=None)
    assert tres.id == uno.id


async def test_la_carga_retrospectiva_no_valida_el_puesto_contra_la_ocupacion(lav):
    await lav.turno(inicio=T)  # el puesto está ocupado a esa hora
    uno = await _retro(lav, resource_id=lav.puesto)
    dos = await _retro(lav, resource_id=lav.puesto)
    assert uno.id != dos.id


# ── void_payment con job_id ──────────────────────────────────────────────────


async def test_anular_un_cobro_con_el_job_equivocado_es_not_found(lav):
    uno = await lav.finalizado()
    pago = await lav.jobs.record_payment(uno.id, lav.pago(1_000))
    otro = await lav.finalizado()
    with pytest.raises(NotFoundError):
        await lav.jobs.void_payment(
            pago.id, idempotency_key=_k(), occurred_at=T, reason="Error", job_id=otro.id
        )
    anulado = await lav.jobs.void_payment(
        pago.id, idempotency_key=_k(), occurred_at=T, reason="Error", job_id=uno.id
    )
    assert anulado.voided_at is not None


# ── cancel_delay_from ────────────────────────────────────────────────────────


async def test_cancelar_por_demora_se_ofrece_recien_pasada_la_tolerancia(lav):
    """La demora (floor de minutos) tiene que **superar** la tolerancia: `tolerance + 1` min."""
    job = await lav.recibido(llegada=T - timedelta(minutes=5))
    assert job.scheduled_at is not None
    desde = cancel_delay_from(job, 20)
    assert desde == T + timedelta(minutes=21)
    # El servidor rechaza un minuto antes y acepta justo en el instante que ofrece.
    with pytest.raises(GuardFailedError):
        await lav.jobs.cancel_for_delay(
            job.id, idempotency_key=_k(), occurred_at=desde - timedelta(seconds=1)
        )
    cancelado = await lav.jobs.cancel_for_delay(job.id, idempotency_key=_k(), occurred_at=desde)
    assert cancelado.status == JobStatus.CANCELADO_DEMORA


async def test_cancelar_por_demora_no_se_ofrece_a_un_walk_in_ni_fuera_de_presente(lav):
    walk_in = await lav.walk_in()
    assert cancel_delay_from(walk_in, 20) is None
    job = await lav.recibido()
    await lav.jobs.start(job.id, idempotency_key=_k(), occurred_at=T)
    assert cancel_delay_from(job, 20) is None


# ── OperationService.day ─────────────────────────────────────────────────────


def _dia_de(instante: datetime) -> date:
    return instante.date()


async def test_el_dia_trae_cada_cosa_en_su_dia_local_del_negocio(lav):
    op = lav.operacion
    # T = 10:00 UTC = 07:00 en Córdoba (UTC−3): el día local es el mismo, 21/9.
    sin_job = await lav.turno(inicio=T)
    en_otro_puesto = await lav.turno(inicio=T + timedelta(hours=2), puesto=lav.puesto_2)
    # 02:30 UTC del 22/9 es 23:30 del 21/9 en Córdoba: sigue siendo el día 21 del negocio.
    medianoche_utc = await lav.turno(inicio=datetime(2026, 9, 22, 2, 30, tzinfo=UTC))
    # 03:30 UTC del 22/9 ya es 00:30 del 22/9 local.
    del_22 = await lav.turno(inicio=datetime(2026, 9, 22, 3, 30, tzinfo=UTC))
    dia = await op.day(date(2026, 9, 21))
    assert dia.timezone == "America/Argentina/Cordoba" and dia.tolerance_min == 20
    assert [r.name for r in dia.resources] == ["Puesto 1", "Puesto 2"]
    assert [(e.kind, e.entry_id_uuid) for e in dia.entries] == [
        ("BOOKING", sin_job.id),
        ("BOOKING", en_otro_puesto.id),
        ("BOOKING", medianoche_utc.id),
    ]
    siguiente = await op.day(date(2026, 9, 22))
    assert [e.entry_id_uuid for e in siguiente.entries] == [del_22.id]
    assert dia.truncated is False


async def test_un_turno_recibido_se_muestra_como_job_y_no_dos_veces(lav):
    turno = await lav.turno(inicio=T)
    job = await lav.recibido(turno, llegada=T + timedelta(minutes=30))
    dia = await lav.operacion.day(date(2026, 9, 21))
    assert [(e.kind, e.entry_id_uuid) for e in dia.entries] == [("JOB", job.id)]
    entrada = dia.entries[0]
    assert entrada.start_at == T and entrada.resource_id == lav.puesto
    assert entrada.total_cents == PRECIO_FIJO and entrada.balance_due_cents == PRECIO_FIJO
    assert entrada.service_names == ["Lavado completo"]
    assert entrada.cancel_delay_from == T + timedelta(minutes=21)


async def test_el_dia_no_muestra_los_turnos_cancelados_vencidos_ni_la_lista_de_espera(lav):
    visible = await lav.turno(inicio=T)
    cancelado = await lav.turno(inicio=T + timedelta(hours=2))
    await lav.turnos.cancel_operational(cancelado.id, requested_at=AHORA, reason="Aviso")
    con_sena = await lav.turno_con_sena(inicio=T + timedelta(hours=4), puesto=lav.puesto_2)
    vencido = await lav.turno_con_sena(inicio=T + timedelta(hours=6), puesto=lav.puesto_2)
    await lav.turnos.expire_holds(lav.puesto_2, AHORA + timedelta(minutes=40))
    estados = {
        e.entry_id_uuid: e.booking.status
        for e in (await lav.operacion.day(date(2026, 9, 21))).entries
        if e.booking
    }
    assert estados == {visible.id: BookingStatus.CONFIRMADO}, (con_sena.id, vencido.id)


async def test_una_carga_retrospectiva_aparece_en_el_dia_de_sus_horas_reales(lav):
    job = await _retro(lav, started_at=T, finished_at=T + timedelta(hours=2))
    dia = await lav.operacion.day(date(2026, 9, 21))
    assert [e.entry_id_uuid for e in dia.entries] == [job.id]
    entrada = dia.entries[0]
    assert entrada.job.entry_mode == JobEntryMode.RETROSPECTIVA
    assert entrada.end_at == T + timedelta(hours=2)  # lo que duró de verdad
    assert entrada.cancel_delay_from is None
    assert (await lav.operacion.day(date(2026, 9, 22))).entries == []


async def test_el_saldo_del_dia_resta_los_cobros_vivos(lav):
    job = await _retro(lav)
    pago = await lav.jobs.record_payment(job.id, lav.pago(400_000, cuando=AHORA_RETRO))
    entrada = (await lav.operacion.day(date(2026, 9, 21))).entries[0]
    assert (entrada.paid_cents, entrada.balance_due_cents) == (400_000, PRECIO_FIJO - 400_000)
    await lav.jobs.void_payment(pago.id, idempotency_key=_k(), occurred_at=T, reason="Error")
    entrada = (await lav.operacion.day(date(2026, 9, 21))).entries[0]
    assert (entrada.paid_cents, entrada.balance_due_cents) == (0, PRECIO_FIJO)


async def test_el_dia_fuera_de_rango_es_una_guarda(lav):
    with pytest.raises(GuardFailedError):
        await lav.operacion.day(date(1999, 12, 31))
    with pytest.raises(GuardFailedError):
        await lav.operacion.day(date.max)


async def test_el_tope_del_dia_se_avisa_en_vez_de_recortar_en_silencio(lav, monkeypatch):
    monkeypatch.setattr("app.application.services.operation_service.DAY_CAP", 1)
    await lav.turno(inicio=T)
    await lav.turno(inicio=T + timedelta(hours=2))
    dia = await lav.operacion.day(date(2026, 9, 21))
    assert dia.truncated is True and len(dia.entries) == 1
    assert DAY_CAP == 500


# ── OperationService.job_detail ──────────────────────────────────────────────


async def test_el_detalle_del_job_trae_servicios_pagos_y_saldo(lav):
    turno = await lav.turno(inicio=T)
    job = await lav.recibido(turno, llegada=T)
    await lav.jobs.start(job.id, idempotency_key=_k(), occurred_at=T)
    await lav.jobs.finish(job.id, idempotency_key=_k(), occurred_at=T + timedelta(hours=1))
    await lav.jobs.record_payment(job.id, lav.pago(300_000))
    d = await lav.operacion.job_detail(job.id)
    assert d.booking_code == turno.code and d.customer is not None
    assert d.customer.name == "Cliente Uno" and d.vehicle is not None
    assert [i.service_name_snapshot for i in d.items] == ["Lavado completo"]
    assert (d.total_cents, d.paid_cents, d.balance_due_cents) == (
        PRECIO_FIJO,
        300_000,
        PRECIO_FIJO - 300_000,
    )
    assert len(d.payments) == 1 and d.inspection is None and d.cancel_delay_from is None


async def test_el_detalle_de_un_job_inexistente_es_not_found(lav):
    with pytest.raises(NotFoundError):
        await lav.operacion.job_detail(uuid.uuid4())


# ── OperationService.receive_job ─────────────────────────────────────────────


async def test_recibir_un_ingreso_sin_datos_crea_un_auto_anonimo_y_ningun_cliente(lav):
    job = await lav.operacion.receive_job(
        idempotency_key=_k(),
        arrived_at=T,
        vehicle_size_id=lav.auto,
        service_ids=[lav.lavado],
    )
    assert job.customer_id is None and job.channel == Channel.CALLE
    assert job.vehicle_id != lav.vehiculo


async def test_recibir_con_patente_reusa_el_auto_y_con_nombre_crea_el_cliente(lav):
    job = await lav.operacion.receive_job(
        idempotency_key=_k(),
        arrived_at=T,
        plate="AB 123 CD",  # la del auto del escenario
        vehicle_size_id=lav.auto,
        service_ids=[lav.lavado],
        customer_name="Cliente Mostrador",
        customer_phone="+54 9 351 000-0303",
    )
    assert job.vehicle_id == lav.vehiculo
    assert job.customer_id is not None and job.customer_id != lav.cliente


async def test_recibir_dos_veces_con_la_misma_clave_no_crea_autos_ni_clientes_de_mas(lav):
    clave = _k()
    datos = {
        "idempotency_key": clave,
        "arrived_at": T,
        "vehicle_size_id": lav.auto,
        "service_ids": [lav.lavado],
        "customer_name": "Cliente Mostrador",
        "customer_phone": "+54 9 351 000-0303",
    }
    uno = await lav.operacion.receive_job(**datos)
    dos = await lav.operacion.receive_job(**datos)
    assert uno.id == dos.id
    autos = await lav.session.scalars(select(Vehicle).where(Vehicle.tenant_id == lav.tenant_id))
    assert len(list(autos.all())) == 2  # el del escenario + uno anónimo
    clientes = await lav.session.scalars(
        select(Customer).where(Customer.tenant_id == lav.tenant_id)
    )
    assert len(list(clientes.all())) == 2


async def test_recibir_un_turno_sin_auto_le_crea_uno_anonimo_solo_la_primera_vez(lav):
    turno = await lav.turno(vehiculo=None)
    assert turno.vehicle_id is None
    uno = await lav.operacion.receive_job(idempotency_key=_k(), arrived_at=T, booking_id=turno.id)
    dos = await lav.operacion.receive_job(idempotency_key=_k(), arrived_at=T, booking_id=turno.id)
    assert uno.id == dos.id and uno.vehicle_id is not None
    assert uno.booking_id == turno.id


async def test_recibir_un_turno_sin_auto_con_patente_la_usa(lav):
    turno = await lav.turno(vehiculo=None)
    job = await lav.operacion.receive_job(
        idempotency_key=_k(), arrived_at=T, booking_id=turno.id, plate="AB 123 CD"
    )
    assert job.vehicle_id == lav.vehiculo


async def test_recibir_un_turno_inexistente_es_not_found(lav):
    with pytest.raises(NotFoundError):
        await lav.operacion.receive_job(idempotency_key=_k(), arrived_at=T, booking_id=uuid.uuid4())


# ── OperationService.create_manual_booking ───────────────────────────────────


async def _a_mano(lav: Lavadero, **pisar):
    datos = {
        "idempotency_key": _k(),
        "now": AHORA,
        "start_at": T,
        "resource_id": lav.puesto,
        "vehicle_size_id": lav.auto,
        "service_ids": [lav.lavado],
        "customer_name": "Cliente Mostrador",
        "customer_phone": "+54 9 351 000-0404",
        **pisar,
    }
    return await lav.operacion.create_manual_booking(**datos)


async def test_un_turno_a_mano_es_interno_con_codigo_int_y_sin_la_anticipacion_del_turnero(lav):
    await lav.configurar(min_advance_min=600)  # el turnero pediría 10 h
    resultado = await _a_mano(lav, start_at=AHORA + timedelta(minutes=5))
    b = resultado.booking
    assert b.source == BookingSource.AGENDA_INTERNA and b.channel == Channel.CARGA_MANUAL
    assert b.code.startswith("INT-") and b.status == BookingStatus.CONFIRMADO
    assert resultado.deposit_paid_cents == 0


async def test_el_turno_del_turnero_sigue_pidiendo_su_anticipacion(lav):
    await lav.configurar(min_advance_min=600)
    with pytest.raises(GuardFailedError, match="advance"):
        await lav.turno(inicio=AHORA + timedelta(minutes=5), ahora=AHORA)


async def test_un_turno_a_mano_no_puede_ser_del_pasado(lav):
    with pytest.raises(GuardFailedError, match="future"):
        await _a_mano(lav, start_at=AHORA - timedelta(seconds=1))
    await _a_mano(lav, start_at=AHORA)  # exactamente ahora se acepta


async def test_un_turno_a_mano_sin_nombre_ni_telefono_es_sin_nombre(lav):
    r = await _a_mano(lav, customer_name=None, customer_phone=None)
    cliente = await lav.session.get(Customer, r.booking.customer_id)
    assert cliente is not None and cliente.name == "Sin nombre"
    assert r.booking.vehicle_id is None  # la patente es opcional


async def test_un_turno_a_mano_con_patente_vincula_el_auto_nuevo(lav):
    r = await _a_mano(lav, plate="ZZ 999 YY")
    assert r.booking.vehicle_id is not None and r.booking.vehicle_id != lav.vehiculo


async def test_el_choque_del_horario_lo_decide_el_exclude_o_el_servicio_segun_el_motor(lav):
    """En SQLite no hay `EXCLUDE`: el choque contra Postgres está en `test_operacion_pg.py`. Acá,
    que un horario libre en otro puesto sí se acepta con el mismo cliente."""
    uno = await _a_mano(lav, customer_phone="+54 9 351 000-0505")
    dos = await _a_mano(lav, customer_phone="+54 9 351 000-0505", resource_id=lav.puesto_2)
    assert uno.booking.customer_id == dos.booking.customer_id  # el teléfono identifica al cliente
    assert uno.booking.code != dos.booking.code


async def test_la_sena_cobrada_confirma_el_turno_y_la_pendiente_lo_deja_en_espera(lav):
    await lav.activar_sena()
    pendiente = await _a_mano(lav, service_ids=[lav.lavado_con_sena])
    assert pendiente.booking.status == BookingStatus.PENDIENTE_SENA
    assert pendiente.booking.hold_expires_at is not None
    cobrada = await _a_mano(
        lav,
        start_at=T + timedelta(hours=5),
        service_ids=[lav.lavado_con_sena],
        deposit=ManualDeposit(payment_method_id=lav.efectivo),
    )
    assert cobrada.booking.status == BookingStatus.CONFIRMADO
    assert cobrada.deposit_paid_cents == cobrada.booking.deposit_required_cents > 0
    con_monto = await _a_mano(
        lav,
        start_at=T + timedelta(hours=8),
        service_ids=[lav.lavado_con_sena],
        deposit=ManualDeposit(payment_method_id=lav.efectivo, amount_cents=1_000),
    )
    assert con_monto.deposit_paid_cents == 1_000


async def test_una_sena_en_un_turno_que_no_la_pide_es_una_guarda(lav):
    with pytest.raises(GuardFailedError, match="deposit"):
        await _a_mano(lav, deposit=ManualDeposit(payment_method_id=lav.efectivo))


async def test_la_clave_de_la_sena_del_turno_a_mano_es_la_del_llamador(lav):
    await lav.activar_sena()
    clave = _k()
    await _a_mano(
        lav,
        idempotency_key=clave,
        service_ids=[lav.lavado_con_sena],
        deposit=ManualDeposit(payment_method_id=lav.efectivo),
    )
    pago = await lav.session.scalar(select(Payment).where(Payment.tenant_id == lav.tenant_id))
    assert pago is not None and pago.idempotency_key == f"{clave}:deposit"


async def test_invalid_transition_de_un_job_no_cambia_nada(lav):
    job = await lav.walk_in()
    with pytest.raises(InvalidTransition):
        await lav.jobs.finish(job.id, idempotency_key=_k(), occurred_at=T)
    assert (await lav.jobs.get(job.id)).status == JobStatus.PRESENTE
