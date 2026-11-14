"""Bandeja del dueño (PR 5.3) en la suite rápida — FASE-5-CONTRATO §4.1, D5-9, D5-10, B11.

Lo que depende del `EXCLUDE` (choque al reprogramar, aprobar con el horario tomado, seña tardía
sin horario) corre contra Postgres en `test_bandeja_pg.py`: SQLite no tiene la restricción.

Datos sintéticos: el lavadero se arma por los servicios (`_armado`).
"""

import uuid
from datetime import time, timedelta

import pytest
import pytest_asyncio

from app.application.services import BookingInboxService
from app.application.services._base import as_aware
from app.application.services.agenda_service import LocalRange
from app.application.services.errors import BlockedError, NotFoundError
from app.domain.enums import (
    BookingStatus,
    CancellationClassification,
    CancellationInitiator,
    DepositStatus,
)
from app.domain.exceptions import GuardFailedError, InvalidTransition
from app.persistence.repositories.agenda import BookingItemRepository
from app.tests.application._armado import (
    AHORA,
    PRECIO_FIJO,
    SENA,
    SENA_BPS,
    Lavadero,
    T,
    armar,
)
from app.tests.application._bandeja import en_revision, horario_lunes, local, regla


@pytest_asyncio.fixture
async def lv(db_session) -> Lavadero:
    return await armar(db_session)


def _bandeja(lv: Lavadero) -> BookingInboxService:
    return BookingInboxService(lv.session, lv.tenant_id, lv.owner_id)


# ── Aprobar y rechazar la lista de espera (D5-9) ─────────────────────────────


async def test_aprobar_sin_sena_confirma_en_la_misma_fila(lv):
    pedido = await en_revision(lv, puesto=lv.puesto_2)
    aprobado = await lv.turnos.approve_review(pedido.id, now=AHORA)
    assert aprobado.id == pedido.id
    assert aprobado.status == BookingStatus.CONFIRMADO
    assert aprobado.hold_expires_at is None and aprobado.deposit_required_cents == 0
    # D5-6: el puesto se vuelve a elegir, el primero por orden.
    assert aprobado.resource_id == lv.puesto


async def test_aprobar_recalcula_la_sena_con_la_configuracion_vigente(lv):
    pedido = await en_revision(lv)
    assert pedido.deposit_required_cents == 0  # sin seña al pedir
    await lv.activar_sena()
    aprobado = await lv.turnos.approve_review(pedido.id, now=AHORA)
    assert aprobado.status == BookingStatus.PENDIENTE_SENA
    assert aprobado.deposit_required_cents == PRECIO_FIJO * SENA_BPS // 10_000
    settings = await lv.configurar()
    assert aprobado.hold_expires_at == AHORA + timedelta(minutes=settings.hold_min)
    items = await BookingItemRepository(lv.session).list_for_booking(pedido.id, lv.tenant_id)
    assert [i.deposit_bps for i in items] == [SENA_BPS]


async def test_aprobar_saltea_el_puesto_bloqueado(lv):
    pedido = await en_revision(lv)
    await lv.turnos.add_block(starts_at=T, ends_at=T + timedelta(hours=1), resource_id=lv.puesto)
    aprobado = await lv.turnos.approve_review(pedido.id, now=AHORA)
    assert aprobado.resource_id == lv.puesto_2


async def test_aprobar_con_todo_bloqueado_es_blocked(lv):
    pedido = await en_revision(lv)
    await lv.turnos.add_block(starts_at=T - timedelta(hours=2), ends_at=T + timedelta(hours=2))
    with pytest.raises(BlockedError):
        await lv.turnos.approve_review(pedido.id, now=AHORA)
    # Cada puesto con su propio bloqueo: tampoco queda dónde.
    otro = await en_revision(lv, inicio=T + timedelta(days=1))
    for puesto in (lv.puesto, lv.puesto_2):
        await lv.turnos.add_block(
            starts_at=T + timedelta(days=1),
            ends_at=T + timedelta(days=1, hours=1),
            resource_id=puesto,
        )
    with pytest.raises(BlockedError):
        await lv.turnos.approve_review(otro.id, now=AHORA)


async def test_no_se_aprueba_un_pedido_vencido_ni_un_turno_que_no_esta_en_revision(lv):
    pedido = await en_revision(lv)
    with pytest.raises(GuardFailedError):
        await lv.turnos.approve_review(pedido.id, now=T)
    confirmado = await lv.turno(inicio=T + timedelta(hours=3))
    with pytest.raises(InvalidTransition):
        await lv.turnos.approve_review(confirmado.id, now=AHORA)


async def test_no_se_aprueba_un_pedido_con_servicio_a_cotizar(lv):
    pedido = await en_revision(lv, servicio=lv.tapizado)
    with pytest.raises(GuardFailedError):
        await lv.turnos.approve_review(pedido.id, now=AHORA)


async def test_rechazar_guarda_motivo_y_quien_y_es_idempotente(lv):
    pedido = await en_revision(lv)
    rechazo = await lv.turnos.reject_review(pedido.id, reason="  Sin lugar  ", now=AHORA)
    turno = await lv.turnos.get(pedido.id)
    assert turno.status == BookingStatus.RECHAZADO
    assert rechazo.reason == "Sin lugar"
    assert rechazo.initiator == CancellationInitiator.NEGOCIO
    assert rechazo.classification == CancellationClassification.OPERATIVA
    assert rechazo.deposit_status == DepositStatus.SIN_PAGO
    assert rechazo.actor_user_id == lv.owner_id
    assert rechazo.resulting_status == BookingStatus.RECHAZADO
    otra = await lv.turnos.reject_review(pedido.id, reason="Otra vez", now=AHORA)
    assert otra.id == rechazo.id


async def test_rechazar_pide_motivo_y_estado_de_revision(lv):
    pedido = await en_revision(lv)
    for motivo in ("", "   ", "x" * 161):
        with pytest.raises(GuardFailedError):
            await lv.turnos.reject_review(pedido.id, reason=motivo, now=AHORA)
    confirmado = await lv.turno(inicio=T + timedelta(hours=3))
    with pytest.raises(InvalidTransition):
        await lv.turnos.reject_review(confirmado.id, reason="No", now=AHORA)


# ── Reprogramar (D5-10) ──────────────────────────────────────────────────────


async def test_cambiar_de_puesto_mantiene_horario_estado_y_sena(lv):
    await horario_lunes(lv)
    turno = await lv.turno_con_sena()
    hold = turno.hold_expires_at
    r = await lv.turnos.reschedule(turno.id, now=AHORA, resource_id=lv.puesto_2)
    assert r.booking.resource_id == lv.puesto_2
    assert r.booking.start_at == T and r.booking.status == BookingStatus.PENDIENTE_SENA
    assert as_aware(r.booking.hold_expires_at) == as_aware(hold)
    assert r.booking.deposit_required_cents == SENA
    assert r.outside_rules is False


async def test_reprogramar_recalcula_el_fin_con_la_duracion_snapshot(lv):
    await horario_lunes(lv)
    turno = await lv.turno()
    nuevo = local(14)
    r = await lv.turnos.reschedule(turno.id, now=AHORA, start_at=nuevo)
    assert r.booking.start_at == nuevo
    assert r.booking.end_at == nuevo + timedelta(minutes=turno.duration_min)
    assert r.booking.resource_id == lv.puesto and r.outside_rules is False


async def test_fuera_del_horario_se_acepta_con_aviso(lv):
    await horario_lunes(lv)
    turno = await lv.turno()
    tarde = await lv.turnos.reschedule(turno.id, now=AHORA, start_at=local(17, 30))  # cruza 18
    assert tarde.outside_rules is True
    domingo = await lv.turnos.reschedule(
        turno.id, now=AHORA, start_at=local(10) - timedelta(days=1)
    )
    assert domingo.outside_rules is True
    assert domingo.booking.start_at == local(10) - timedelta(days=1)


async def test_fuera_de_la_regla_del_servicio_se_acepta_con_aviso(lv):
    await horario_lunes(lv)
    await regla(lv, lv.lavado, 1, time(9), time(11))
    turno = await lv.turno(inicio=local(9))
    dentro = await lv.turnos.reschedule(turno.id, now=AHORA, start_at=local(11))
    assert dentro.outside_rules is False
    fuera = await lv.turnos.reschedule(turno.id, now=AHORA, start_at=local(15))
    assert fuera.outside_rules is True


async def test_una_apertura_especial_cuenta_como_horario(lv):
    turno = await lv.turno()  # sin franjas: todo queda fuera
    assert (await lv.turnos.reschedule(turno.id, now=AHORA, start_at=local(10))).outside_rules
    await lv.agenda.create_opening(LocalRange(T.date()))
    r = await lv.turnos.reschedule(turno.id, now=AHORA, start_at=local(11))
    assert r.outside_rules is False


async def test_un_bloqueo_no_se_saltea(lv):
    turno = await lv.turno()
    await lv.turnos.add_block(
        starts_at=local(12), ends_at=local(13), resource_id=lv.puesto_2, reason="Mantenimiento"
    )
    with pytest.raises(BlockedError):
        await lv.turnos.reschedule(turno.id, now=AHORA, start_at=local(12), resource_id=lv.puesto_2)
    # El bloqueo del otro puesto no molesta; uno de todo el lavadero, sí.
    ok = await lv.turnos.reschedule(turno.id, now=AHORA, start_at=local(12))
    assert ok.booking.resource_id == lv.puesto
    await lv.turnos.add_block(starts_at=local(15), ends_at=local(16))
    with pytest.raises(BlockedError):
        await lv.turnos.reschedule(turno.id, now=AHORA, start_at=local(15, 30))
    assert as_aware((await lv.turnos.get(turno.id)).start_at) == local(12)


async def test_reprogramar_pide_algo_y_no_va_al_pasado(lv):
    turno = await lv.turno()
    with pytest.raises(GuardFailedError):
        await lv.turnos.reschedule(turno.id, now=AHORA)
    with pytest.raises(GuardFailedError):
        await lv.turnos.reschedule(turno.id, now=AHORA, start_at=AHORA - timedelta(hours=1))
    # Solo el puesto de un turno que ya empezó, sí.
    r = await lv.turnos.reschedule(turno.id, now=T + timedelta(minutes=5), resource_id=lv.puesto_2)
    assert r.booking.resource_id == lv.puesto_2


async def test_una_lista_de_espera_se_reprograma(lv):
    pedido = await en_revision(lv)
    r = await lv.turnos.reschedule(pedido.id, now=AHORA, start_at=local(15))
    assert r.booking.status == BookingStatus.PENDIENTE_REVISION


async def test_un_hold_vencido_o_un_turno_cerrado_no_se_reprograman(lv):
    turno = await lv.turno_con_sena()
    with pytest.raises(InvalidTransition):
        await lv.turnos.reschedule(
            turno.id, now=AHORA + timedelta(hours=1), resource_id=lv.puesto_2
        )
    assert (await lv.turnos.get(turno.id)).status == BookingStatus.VENCIDO
    cancelado = await lv.turno(inicio=T + timedelta(hours=4))
    await lv.turnos.cancel_operational(cancelado.id, requested_at=AHORA, reason="Lluvia")
    with pytest.raises(InvalidTransition):
        await lv.turnos.reschedule(cancelado.id, now=AHORA, resource_id=lv.puesto_2)


async def test_un_puesto_inexistente_es_not_found(lv):
    turno = await lv.turno()
    with pytest.raises(NotFoundError):
        await lv.turnos.reschedule(turno.id, now=AHORA, resource_id=uuid.uuid4())


# ── Seña desde la bandeja ────────────────────────────────────────────────────


async def test_confirmar_la_sena_con_el_hold_vigente(lv):
    turno = await lv.turno_con_sena()
    pago = lv.pago(SENA, "sena-1")
    r = await lv.turnos.confirm_deposit_or_late(turno.id, pago, now=AHORA + timedelta(minutes=5))
    assert r.late is False and r.booking.status == BookingStatus.CONFIRMADO
    otra = await lv.turnos.confirm_deposit_or_late(turno.id, pago, now=AHORA)
    assert otra.booking.id == turno.id and otra.booking.status == BookingStatus.CONFIRMADO


async def test_confirmar_la_sena_con_el_hold_vencido_confirma_tarde(lv):
    turno = await lv.turno_con_sena()
    r = await lv.turnos.confirm_deposit_or_late(
        turno.id, lv.pago(SENA), now=AHORA + timedelta(hours=2)
    )
    assert r.late is True and r.booking.status == BookingStatus.CONFIRMADO
    assert r.booking.hold_expires_at is None


async def test_un_turno_sin_sena_no_se_confirma_con_pago(lv):
    turno = await lv.turno()
    with pytest.raises(InvalidTransition):
        await lv.turnos.confirm_deposit_or_late(turno.id, lv.pago(SENA), now=AHORA)


# ── Lecturas de la bandeja ───────────────────────────────────────────────────


async def test_lista_por_estado_y_dia_local(lv):
    confirmado = await lv.turno()
    con_sena = await lv.turno_con_sena(inicio=T + timedelta(hours=2))
    otro_dia = await lv.turno(inicio=T + timedelta(days=1))
    pedido = await en_revision(lv, inicio=T + timedelta(hours=5))
    bandeja = _bandeja(lv)
    dia = T.date()
    todo = await bandeja.list_bookings(from_day=dia, to_day=dia, limit=50, offset=0)
    assert [r.booking.id for r in todo.items] == [confirmado.id, con_sena.id, pedido.id]
    assert todo.total == 3
    fila = todo.items[0]
    assert fila.customer is not None and fila.customer.name == "Cliente Uno"
    assert fila.vehicle is not None and [i.service_name_snapshot for i in fila.items] == [
        "Lavado completo"
    ]
    solo = await bandeja.list_bookings(statuses=[BookingStatus.PENDIENTE_SENA], limit=50, offset=0)
    assert [r.booking.id for r in solo.items] == [con_sena.id]
    desde = await bandeja.list_bookings(from_day=dia + timedelta(days=1), limit=50, offset=0)
    assert [r.booking.id for r in desde.items] == [otro_dia.id]
    pagina = await bandeja.list_bookings(limit=1, offset=1)
    assert pagina.total == 4 and [r.booking.id for r in pagina.items] == [con_sena.id]
    with pytest.raises(GuardFailedError):
        await bandeja.list_bookings(from_day=dia, to_day=dia - timedelta(days=1), limit=1, offset=0)


async def test_el_contador_suma_sena_lista_de_espera_y_senas_a_resolver(lv):
    bandeja = _bandeja(lv)
    assert (await bandeja.counts(now=AHORA)).total == 0
    await lv.turno_con_sena()
    await en_revision(lv, inicio=T + timedelta(hours=4))
    cancelado = await lv.turno_con_sena(inicio=T + timedelta(hours=6))
    await lv.turnos.confirm_deposit(cancelado.id, lv.pago(SENA), now=AHORA)
    await lv.turnos.cancel_operational(cancelado.id, requested_at=AHORA, reason="Lluvia")
    c = await bandeja.counts(now=AHORA)
    assert (c.pending_deposit, c.in_review, c.deposit_cases, c.total) == (1, 1, 1, 3)
    # Lo que ya empezó no espera decisión.
    assert (await bandeja.counts(now=T + timedelta(hours=5))).pending_deposit == 0
    casos = await bandeja.list_deposit_cases(limit=50, offset=0)
    assert casos.total == 1
    caso = casos.items[0]
    assert caso.row.booking.id == cancelado.id and caso.row.deposit_paid_cents == SENA
    assert caso.cancellation.deposit_status == DepositStatus.REPROGRAMACION_O_DEVOLUCION_PENDIENTE
    await lv.senas.resolve(
        caso.cancellation.id,
        status=DepositStatus.REPROGRAMADA,
        reason="Pasa al sábado",
        resolved_at=T,
    )
    assert (await bandeja.counts(now=AHORA)).deposit_cases == 0


async def test_detalle_con_pagos_incidencias_y_marcas(lv):
    turno = await lv.turno_con_sena()
    await lv.turnos.confirm_deposit(turno.id, lv.pago(SENA), now=AHORA)
    bandeja = _bandeja(lv)
    d = await bandeja.detail(turno.id, now=AHORA)
    assert d.row.deposit_paid_cents == SENA and [p.amount_cents for p in d.payments] == [SENA]
    assert d.cancellation is None and d.incidents == 0 and d.marks == []

    marca = await bandeja.mark_incident(turno.id, reason="  Llegó tarde y se fue  ")
    assert marca.reason == "Llegó tarde y se fue" and marca.customer_id == lv.cliente
    d = await bandeja.detail(turno.id, now=AHORA)
    assert d.incidents == 1 and [m.id for m in d.marks] == [marca.id]
    await bandeja.void_incident_mark(marca.id)
    d = await bandeja.detail(turno.id, now=AHORA)
    assert d.incidents == 0 and d.marks == []
    with pytest.raises(NotFoundError):
        await bandeja.void_incident_mark(marca.id)  # anulada: ya no se ve


async def test_la_marca_pide_motivo(lv):
    turno = await lv.turno()
    for motivo in ("", "  ", "x" * 161):
        with pytest.raises(GuardFailedError):
            await _bandeja(lv).mark_incident(turno.id, reason=motivo)


async def test_no_asistio_cuenta_como_incidencia(lv):
    turno = await lv.turno()
    await lv.turnos.mark_no_show(turno.id)
    d = await _bandeja(lv).detail(turno.id, now=T + timedelta(hours=2))
    assert d.row.booking.status == BookingStatus.NO_ASISTIO and d.incidents == 1
