"""FASE-5-CONTRATO §3, §4.1 y §5 — `AgendaService` en la suite rápida (PR 5.1).

Bloqueos y aperturas, la disponibilidad armada desde la base (el motor puro tiene sus casos E1–E16
en `tests/domain/test_availability.py`) y el conteo de incidencias de B10. El negocio sintético
está en `America/Argentina/Cordoba` (UTC−3): T es el lunes 21/9/2026 a las 07:00 locales.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import update

from app.application.services.agenda_service import LocalRange
from app.application.services.errors import CatalogIncoherentError, InUseError, NotFoundError
from app.application.services.service_catalog_service import RuleInput, ServiceCatalogService
from app.domain.enums import (
    BookingSource,
    BookingStatus,
    CancellationClassification,
    Channel,
)
from app.domain.exceptions import GuardFailedError, InvalidAmountError
from app.domain.void import VoidReason
from app.persistence.models import Booking, Cancellation, CustomerIncidentMark
from app.tests.application._armado import AHORA, Lavadero, T, armar

TZ = ZoneInfo("America/Argentina/Cordoba")
LUNES = T.date()  # 21/9/2026


def local(h: int, m: int = 0, dia: date = LUNES) -> datetime:
    return datetime.combine(dia, time(h, m), tzinfo=TZ).astimezone(UTC)


@pytest_asyncio.fixture
async def lav(db_session) -> Lavadero:
    lv = await armar(db_session)
    await lv.catalogo.add_business_hours(1, time(9, 0), time(13, 0))
    return lv


async def _inicios(lav: Lavadero, *, dia: date = LUNES, ahora: datetime = AHORA, **kw):
    resultado = await lav.agenda.availability(
        day=dia,
        vehicle_size_id=kw.get("tamano", lav.auto),
        service_ids=kw.get("servicios", [lav.lavado]),
        now=ahora,
        public=kw.get("public", False),
    )
    return {s.start_at: s.resource_ids for s in resultado.starts}


# ── Disponibilidad ────────────────────────────────────────────────────────────


async def test_la_disponibilidad_usa_el_horario_la_zona_y_los_puestos_en_orden(lav):
    resultado = await lav.agenda.availability(
        day=LUNES, vehicle_size_id=lav.auto, service_ids=[lav.lavado], now=AHORA
    )
    assert resultado.timezone == "America/Argentina/Cordoba"
    assert resultado.duration_min == 60
    inicios = [s.start_at for s in resultado.starts]
    assert inicios[0] == local(9) and inicios[-1] == local(12)  # 60' en 09–13
    assert resultado.starts[0].resource_ids == (lav.puesto, lav.puesto_2)


async def test_un_turno_confirmado_ocupa_su_puesto_y_el_otro_sigue_libre(lav):
    await lav.turno(inicio=local(10))
    inicios = await _inicios(lav)
    assert inicios[local(10)] == (lav.puesto_2,)
    assert inicios[local(9)] == (lav.puesto, lav.puesto_2)  # [9, 10) toca sin pisar


async def test_un_hold_vencido_no_ocupa_aunque_siga_en_pendiente_de_sena(lav):
    turno = await lav.turno_con_sena(inicio=local(10))
    assert turno.status == BookingStatus.PENDIENTE_SENA
    vigente = await _inicios(lav, ahora=AHORA + timedelta(minutes=10))
    assert vigente[local(10)] == (lav.puesto_2,)
    vencido = await _inicios(lav, ahora=AHORA + timedelta(minutes=31))
    assert vencido[local(10)] == (lav.puesto, lav.puesto_2)


async def test_la_duracion_suma_los_servicios_del_tamano(lav):
    resultado = await lav.agenda.availability(
        day=LUNES,
        vehicle_size_id=lav.auto,
        service_ids=[lav.lavado, lav.lavado_con_sena],
        now=AHORA,
    )
    assert resultado.duration_min == 150
    assert [s.start_at for s in resultado.starts][-1] == local(10, 30)


async def test_un_bloqueo_global_vacia_el_dia_y_uno_de_puesto_deja_el_otro(lav):
    await lav.agenda.create_block(LocalRange(LUNES), resource_id=lav.puesto)
    inicios = await _inicios(lav)
    assert inicios and all(v == (lav.puesto_2,) for v in inicios.values())
    await lav.agenda.create_block(LocalRange(LUNES, start_time=time(9), end_time=time(11)))
    inicios = await _inicios(lav)
    assert min(inicios) == local(11)


async def test_una_regla_del_servicio_recorta_los_inicios(lav):
    servicios = ServiceCatalogService(lav.session, lav.tenant_id, lav.owner_id)
    await servicios.update_service(
        lav.lavado, schedule_rules=[RuleInput(1, time(10), time(10, 30))]
    )
    assert sorted(await _inicios(lav)) == [local(10), local(10, 30)]
    await servicios.update_service(lav.lavado, schedule_rules=[RuleInput(5, time(9), time(12))])
    assert await _inicios(lav) == {}
    # Vaciar la lista vuelve a todo el horario del negocio.
    await servicios.update_service(lav.lavado, schedule_rules=[])
    assert len(await _inicios(lav)) == 7


async def test_una_apertura_especial_abre_un_domingo(lav):
    domingo = LUNES + timedelta(days=6)
    assert await _inicios(lav, dia=domingo) == {}
    await lav.agenda.create_opening(
        LocalRange(domingo, start_time=time(10), end_time=time(12)), resource_id=lav.puesto_2
    )
    assert await _inicios(lav, dia=domingo) == {
        local(10, dia=domingo): (lav.puesto_2,),
        local(10, 30, dia=domingo): (lav.puesto_2,),
        local(11, dia=domingo): (lav.puesto_2,),
    }


async def test_el_horizonte_y_el_paso_salen_de_la_configuracion(lav):
    await lav.configurar(slot_step_min=60, booking_horizon_days=1)
    assert sorted(await _inicios(lav, public=True)) == [local(9), local(10), local(11), local(12)]
    assert await _inicios(lav, dia=LUNES + timedelta(days=7), public=True) == {}


async def test_la_disponibilidad_del_negocio_no_usa_horizonte_ni_anticipacion(lav):
    """El negocio agenda a mano a quien llama para dentro de 3 semanas o ya está en el lavadero:
    horizonte y anticipación mínima son reglas del turnero público (5.2)."""
    await lav.configurar(booking_horizon_days=1, min_advance_min=240)
    lejos = LUNES + timedelta(days=21)
    assert await _inicios(lav, dia=lejos, public=True) == {}
    assert len(await _inicios(lav, dia=lejos)) == 7
    # A las 07:00 del mismo día, con 240' de anticipación, el público arranca a las 11.
    assert min(await _inicios(lav, ahora=local(7), public=True)) == local(11)
    assert min(await _inicios(lav, ahora=local(7))) == local(9)


@pytest.mark.parametrize("dia", [date(9999, 12, 31), date(1, 1, 1)])
async def test_una_fecha_absurda_se_rechaza_en_vez_de_desbordar(lav, dia):
    with pytest.raises(GuardFailedError):
        await _inicios(lav, dia=dia)
    with pytest.raises(GuardFailedError):
        await lav.agenda.create_block(LocalRange(dia))
    with pytest.raises(GuardFailedError):
        await lav.agenda.create_block(LocalRange(LUNES, end_day=date(9999, 12, 31)))


async def test_un_servicio_sin_precio_para_el_tamano_es_incoherente(lav):
    servicio = await lav.catalogo.create_service("Pulido sin precio", lav_pricing_mode())
    with pytest.raises(CatalogIncoherentError):
        await lav.agenda.availability(
            day=LUNES, vehicle_size_id=lav.auto, service_ids=[servicio.id], now=AHORA
        )


def lav_pricing_mode():
    from app.domain.enums import PricingMode  # noqa: PLC0415

    return PricingMode.PRECIO_FIJO


async def test_un_tamano_inexistente_es_not_found(lav):
    with pytest.raises(NotFoundError):
        await lav.agenda.availability(
            day=LUNES, vehicle_size_id=uuid.uuid4(), service_ids=[lav.lavado], now=AHORA
        )


# ── Bloqueos ──────────────────────────────────────────────────────────────────


async def test_un_bloqueo_devuelve_los_turnos_que_pisa_y_no_los_cancela(lav):
    adentro = await lav.turno(inicio=local(10))
    otro_puesto = await lav.turno(inicio=local(10), puesto=lav.puesto_2)
    afuera = await lav.turno(inicio=local(12))
    creado = await lav.agenda.create_block(
        LocalRange(LUNES, start_time=time(9), end_time=time(11)),
        resource_id=lav.puesto,
        reason="  Mantenimiento  ",
    )
    assert creado.block.reason == "Mantenimiento"
    assert creado.block.created_by_user_id == lav.owner_id
    assert [b.id for b in creado.overlapping] == [adentro.id]
    assert creado.customer_names[adentro.customer_id] == "Cliente Uno"
    global_ = await lav.agenda.create_block(LocalRange(LUNES))
    assert {b.id for b in global_.overlapping} == {adentro.id, otro_puesto.id, afuera.id}
    assert global_.block.reason == "Bloqueo operativo"
    for turno in (adentro, otro_puesto, afuera):
        assert (await lav.turnos.get(turno.id)).status == BookingStatus.CONFIRMADO


async def test_un_bloqueo_de_dia_completo_va_de_medianoche_a_medianoche_local(lav):
    creado = await lav.agenda.create_block(LocalRange(LUNES, end_day=LUNES + timedelta(days=1)))
    assert creado.block.starts_at == local(0)
    assert creado.block.ends_at == local(0, dia=LUNES + timedelta(days=2))


@pytest.mark.parametrize(
    "rango",
    [
        LocalRange(LUNES, start_time=time(11), end_time=time(9)),
        LocalRange(LUNES, end_day=LUNES + timedelta(days=400)),
    ],
)
async def test_un_rango_invertido_o_de_mas_de_un_anio_se_rechaza(lav, rango):
    with pytest.raises(GuardFailedError):
        await lav.agenda.create_block(rango)
    with pytest.raises(GuardFailedError):
        await lav.agenda.create_opening(rango)


async def test_un_motivo_de_mas_de_160_caracteres_se_rechaza(lav):
    with pytest.raises(GuardFailedError):
        await lav.agenda.create_block(LocalRange(LUNES), reason="x" * 161)


async def test_los_listados_muestran_lo_vigente_y_anular_lo_saca(lav):
    pasado = await lav.agenda.create_block(LocalRange(LUNES - timedelta(days=10)))
    vigente = await lav.agenda.create_block(LocalRange(LUNES))
    apertura = await lav.agenda.create_opening(LocalRange(LUNES + timedelta(days=6)))
    pagina = await lav.agenda.list_blocks(now=AHORA, limit=50, offset=0)
    assert [b.id for b in pagina.items] == [vigente.block.id] and pagina.total == 1
    assert pasado.block.id not in [b.id for b in pagina.items]
    assert [o.id for o in (await lav.agenda.list_openings(now=AHORA, limit=1, offset=0)).items] == [
        apertura.id
    ]
    await lav.agenda.void_block(vigente.block.id)
    await lav.agenda.void_opening(apertura.id)
    assert (await lav.agenda.list_blocks(now=AHORA, limit=50, offset=0)).total == 0
    assert (await lav.agenda.list_openings(now=AHORA, limit=50, offset=0)).total == 0
    assert vigente.block.void_reason == VoidReason.DESACTIVADO
    with pytest.raises(NotFoundError):
        await lav.agenda.void_block(vigente.block.id)
    with pytest.raises(NotFoundError):
        await lav.agenda.void_opening(apertura.id)


async def test_sin_actor_no_se_bloquea_ni_se_abre(lav):
    from app.application.services import AgendaService  # noqa: PLC0415

    anonimo = AgendaService(lav.session, lav.tenant_id, None)
    with pytest.raises(GuardFailedError):
        await anonimo.create_block(LocalRange(LUNES))
    with pytest.raises(GuardFailedError):
        await anonimo.create_opening(LocalRange(LUNES))


# ── Puestos con turnos (DELETE /resources → IN_USE) ──────────────────────────


@pytest.mark.parametrize(
    "estado",
    [
        BookingStatus.PENDIENTE_REVISION,
        BookingStatus.CONFIRMADO,
        BookingStatus.PENDIENTE_COTIZACION,
    ],
)
async def test_no_se_anula_un_puesto_con_turnos_activos_a_futuro(lav, estado):
    futuro = datetime.now(UTC) + timedelta(days=3)
    turno = await lav.turno(inicio=futuro, ahora=datetime.now(UTC))
    await lav.session.execute(
        update(Booking).where(Booking.id == turno.id).values(status=estado, hold_expires_at=None)
    )
    with pytest.raises(InUseError):
        await lav.catalogo.void_resource(lav.puesto)


async def test_un_puesto_con_turnos_cerrados_o_pasados_se_anula(lav):
    await lav.turno(inicio=T)  # 2026: ya pasó
    await lav.catalogo.void_resource(lav.puesto)


# ── Incidencias (B10) ─────────────────────────────────────────────────────────

AHORA_INC = T + timedelta(days=10)


async def _cancelacion(lav: Lavadero, clasificacion: CancellationClassification, cuando: datetime):
    turno = await lav.turno(inicio=cuando + timedelta(hours=1), ahora=cuando - timedelta(days=1))
    cancelacion = await lav.turnos.cancel_by_client(turno.id, now=cuando)
    await lav.session.execute(
        update(Cancellation)
        .where(Cancellation.id == cancelacion.id)
        .values(classification=clasificacion)
    )
    return cancelacion


async def _con_estado(lav: Lavadero, estado: BookingStatus, inicio: datetime) -> Booking:
    turno = await lav.turno(inicio=inicio, ahora=inicio - timedelta(days=1))
    await lav.session.execute(
        update(Booking).where(Booking.id == turno.id).values(status=estado, hold_expires_at=None)
    )
    return turno


async def test_cuentan_tardia_posterior_demora_no_asistio_y_marca(lav):
    assert await lav.agenda.count_incidents(lav.cliente, now=AHORA_INC) == 0
    await _cancelacion(lav, CancellationClassification.TARDIA, T + timedelta(days=1))
    await _cancelacion(lav, CancellationClassification.POSTERIOR_AL_TURNO, T + timedelta(days=2))
    await _con_estado(lav, BookingStatus.CANCELADO_DEMORA, T + timedelta(days=3))
    await _con_estado(lav, BookingStatus.NO_ASISTIO, T + timedelta(days=4))
    lav.session.add(
        CustomerIncidentMark(
            tenant_id=lav.tenant_id,
            customer_id=lav.cliente,
            reason="Vino con otro auto",
            created_by_user_id=lav.owner_id,
        )
    )
    await lav.session.flush()
    assert await lav.agenda.count_incidents(lav.cliente, now=AHORA_INC) == 5


async def test_no_cuentan_operativa_normal_anuladas_ni_las_de_mas_de_90_dias(lav):
    await _cancelacion(lav, CancellationClassification.OPERATIVA, T + timedelta(days=1))
    await _cancelacion(lav, CancellationClassification.NORMAL, T + timedelta(days=2))
    await _cancelacion(lav, CancellationClassification.TARDIA, T - timedelta(days=100))
    await _con_estado(lav, BookingStatus.NO_ASISTIO, T - timedelta(days=95))
    await _con_estado(lav, BookingStatus.ATENDIDO, T + timedelta(days=3))
    anulada = CustomerIncidentMark(
        tenant_id=lav.tenant_id,
        customer_id=lav.cliente,
        reason="Marcada por error",
        created_by_user_id=lav.owner_id,
        voided_at=datetime.now(UTC),
        void_reason=VoidReason.ERROR_DE_CARGA,
    )
    lav.session.add(anulada)
    await lav.session.flush()
    # La marca anulada no cuenta; una vieja (created_at de hace 100 días) tampoco.
    vieja = CustomerIncidentMark(
        tenant_id=lav.tenant_id,
        customer_id=lav.cliente,
        reason="Hace mucho",
        created_by_user_id=lav.owner_id,
        created_at=AHORA_INC - timedelta(days=100),
    )
    lav.session.add(vieja)
    await lav.session.flush()
    assert await lav.agenda.count_incidents(lav.cliente, now=AHORA_INC) == 0
    assert await lav.agenda.count_incidents(lav.cliente, now=datetime.now(UTC)) == 0


async def test_las_incidencias_de_otro_cliente_no_cuentan(lav):
    otro = await lav.clientes.resolve_by_phone(
        "11 4444-6666", name="Cliente Dos", channel=Channel.WHATSAPP
    )
    turno = await lav.turnos.create(
        code=f"TUR-{uuid.uuid4().hex[:8]}",
        source=BookingSource.PANEL,
        channel=Channel.WHATSAPP,
        resource_id=lav.puesto,
        start_at=T + timedelta(days=4),
        customer_id=otro.customer.id,
        service_ids=[lav.lavado],
        vehicle_size_id=lav.auto,
        now=T,
    )
    await lav.session.execute(
        update(Booking).where(Booking.id == turno.id).values(status=BookingStatus.NO_ASISTIO)
    )
    assert await lav.agenda.count_incidents(lav.cliente, now=AHORA_INC) == 0
    assert await lav.agenda.count_incidents(otro.customer.id, now=AHORA_INC) == 1


# ── Reglas del servicio (§3.2) ────────────────────────────────────────────────


async def test_las_reglas_se_reemplazan_como_lista_completa(lav):
    servicios = ServiceCatalogService(lav.session, lav.tenant_id, lav.owner_id)
    vista = await servicios.update_service(
        lav.lavado,
        schedule_rules=[RuleInput(6, time(9), time(16)), RuleInput(5, time(14), time(16))],
    )
    assert [(r.weekday, r.window_start) for r in vista.rules] == [(5, time(14)), (6, time(9))]
    vista = await servicios.update_service(lav.lavado, name="Lavado completo plus")
    assert len(vista.rules) == 2  # sin `schedule_rules`, no se tocan
    vista = await servicios.update_service(
        lav.lavado, schedule_rules=[RuleInput(1, time(9), time(9))]
    )
    assert [(r.weekday, r.last_start) for r in vista.rules] == [(1, time(9))]


@pytest.mark.parametrize(
    "regla", [RuleInput(0, time(9), time(10)), RuleInput(1, time(11), time(10))]
)
async def test_una_regla_invalida_se_rechaza(lav, regla):
    servicios = ServiceCatalogService(lav.session, lav.tenant_id, lav.owner_id)
    with pytest.raises(InvalidAmountError):
        await servicios.update_service(lav.lavado, schedule_rules=[regla])


async def test_anular_un_servicio_anula_sus_reglas(lav):
    servicios = ServiceCatalogService(lav.session, lav.tenant_id, lav.owner_id)
    vista = await servicios.update_service(
        lav.tapizado, schedule_rules=[RuleInput(1, time(9), time(10))]
    )
    regla = vista.rules[0]
    await servicios.void_service(lav.tapizado)
    assert regla.voided_at is not None


async def test_un_turno_anulado_no_suma_su_cancelacion_tardia(lav):
    cancelacion = await _cancelacion(lav, CancellationClassification.TARDIA, T + timedelta(days=1))
    await lav.session.execute(
        update(Booking)
        .where(Booking.id == cancelacion.booking_id)
        .values(voided_at=datetime.now(UTC), void_reason=VoidReason.ERROR_DE_CARGA)
    )
    assert await lav.agenda.count_incidents(lav.cliente, now=AHORA_INC) == 0


async def test_bajarse_de_la_lista_de_espera_no_es_incidencia(lav):
    cuando = T + timedelta(days=1)
    turno = await _con_estado(lav, BookingStatus.PENDIENTE_REVISION, cuando + timedelta(hours=1))
    cancelacion = await lav.turnos.cancel_by_client(turno.id, now=cuando)
    assert cancelacion.classification == CancellationClassification.NORMAL
    assert await lav.agenda.count_incidents(lav.cliente, now=AHORA_INC) == 0


async def test_no_se_anula_un_servicio_con_un_pedido_en_lista_de_espera(lav):
    futuro = datetime.now(UTC) + timedelta(days=3)
    turno = await lav.turno(inicio=futuro, ahora=datetime.now(UTC))
    await lav.session.execute(
        update(Booking)
        .where(Booking.id == turno.id)
        .values(status=BookingStatus.PENDIENTE_REVISION, hold_expires_at=None)
    )
    servicios = ServiceCatalogService(lav.session, lav.tenant_id, lav.owner_id)
    with pytest.raises(InUseError):
        await servicios.void_service(lav.lavado)
