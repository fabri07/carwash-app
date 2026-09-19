"""FASE-3-CONTRATO §1.3, §2 y §4 — los servicios contra **Postgres real**, como `carwash_app`.

Lo que SQLite no puede probar:

- `EXCLUDE` + vencimiento perezoso: reservar sobre un hold vencido del mismo puesto funciona;
  sobre uno vigente choca (`SlotTakenError`), y la transacción sigue usable (SAVEPOINT).
- `SELECT … FOR UPDATE`: dos cobros concurrentes se serializan y el segundo ve al primero.
- Idempotencia ante la carrera: dos sesiones con la misma clave aplican **una** vez.
- `job_events` append-only: el runtime no puede modificar la historia que escriben los servicios.
- B13 de punta a punta y A1.

Todos los servicios corren sobre sesiones **sin** contexto de tenant: lo fijan ellos (`SET
LOCAL`), que es lo que se afirma de paso en cada test. Las sesiones de la carrera usan un engine
propio con `NullPool` (el `pg_engine` compartido tiene una sola conexión).
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.application.services.errors import QuoteAlreadyUsedError, SlotTakenError
from app.domain.enums import BookingStatus, Channel, DepositStatus, JobEventType, JobStatus
from app.domain.exceptions import GuardFailedError, InvalidTransition
from app.persistence.models import Customer, Job, JobEvent
from app.persistence.repositories.agenda import BookingRepository
from app.tests.application._armado import (
    AHORA,
    PRECIO_FIJO,
    SENA,
    Lavadero,
    T,
    armar_catalogo,
)
from app.tests.conftest_pg import PG_TEST_URL, _as_app_role, _with_driver

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

HOLD = AHORA + timedelta(minutes=30)
#: Cuánto se espera para afirmar que una sesión quedó bloqueada en un lock.
ESPERA = 0.5

Factory = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def fabrica(pg_admin_engine) -> AsyncGenerator[Factory, None]:
    engine = create_async_engine(
        _as_app_role(_with_driver(PG_TEST_URL, "asyncpg")), poolclass=NullPool
    )
    yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def lav(fabrica: Factory, pg_tenant_a, pg_user_factory) -> Lavadero:
    owner = await pg_user_factory(pg_tenant_a, f"owner-{uuid.uuid4().hex[:6]}@ejemplo.invalid")
    async with fabrica() as s, s.begin():
        armado = await armar_catalogo(s, pg_tenant_a, owner)
    return armado


async def _en_tx[R](fabrica: Factory, lav: Lavadero, fn: Callable[[Lavadero], Awaitable[R]]) -> R:
    """Corre `fn` en una transacción propia y comitea."""
    async with fabrica() as s, s.begin():
        return await fn(lav.en(s))


async def _contar(pg_admin_engine, sql: str, **params: Any) -> int:
    async with pg_admin_engine.connect() as conn:
        return int(await conn.scalar(text(sql), params) or 0)


# ── EXCLUDE y vencimiento perezoso (B10) ─────────────────────────────────────


async def test_reservar_sobre_un_hold_vencido_del_mismo_puesto_funciona(fabrica, lav):
    viejo = await _en_tx(fabrica, lav, lambda lv: lv.turno(servicio=lv.lavado_con_sena, hold=HOLD))

    async def reservar(lv: Lavadero):
        return await lv.turno(ahora=HOLD + timedelta(minutes=1))

    nuevo = await _en_tx(fabrica, lav, reservar)
    assert nuevo.status == BookingStatus.CONFIRMADO
    async with fabrica() as s, s.begin():
        assert (await lav.en(s).turnos.get(viejo.id)).status == BookingStatus.VENCIDO


async def test_reservar_sobre_un_hold_vigente_choca_y_la_transaccion_sigue(fabrica, lav):
    await _en_tx(fabrica, lav, lambda lv: lv.turno(servicio=lv.lavado_con_sena, hold=HOLD))
    async with fabrica() as s, s.begin():
        lv = lav.en(s)
        with pytest.raises(SlotTakenError):
            await lv.turno(inicio=T + timedelta(minutes=30), ahora=AHORA)
        # el SAVEPOINT dejó la transacción usable: otro puesto, y el borde del mismo, entran
        otro = await lv.turno(puesto=lv.puesto_2)
        borde = await lv.turno(inicio=T + timedelta(minutes=90))  # el de seña dura 90
        assert otro.status == borde.status == BookingStatus.CONFIRMADO


async def test_confirmacion_tardia_sobre_un_horario_ya_tomado_choca(fabrica, lav):
    vencido = await _en_tx(
        fabrica, lav, lambda lv: lv.turno(servicio=lv.lavado_con_sena, hold=HOLD)
    )
    await _en_tx(fabrica, lav, lambda lv: lv.turno(ahora=HOLD))  # vence el hold y toma el horario
    async with fabrica() as s, s.begin():
        with pytest.raises(SlotTakenError):
            lv = lav.en(s)
            await lv.turnos.confirm_late(vencido.id, now=HOLD, payment=lv.pago(SENA))


async def test_aceptar_una_cotizacion_que_alarga_el_turno_sobre_otro_choca(fabrica, lav):
    turno = await _en_tx(
        fabrica, lav, lambda lv: lv.turno(servicio=lv.tapizado, hold=AHORA + timedelta(hours=3))
    )
    await _en_tx(fabrica, lav, lambda lv: lv.turno(inicio=T + timedelta(hours=3)))
    assert turno.quote_id is not None

    async def aceptar(lv: Lavadero):
        await lv.cotizaciones.quote(
            turno.quote_id, agreed_price_cents=1_000_000, agreed_duration_min=240, quoted_at=AHORA
        )
        return await lv.cotizaciones.accept(turno.quote_id, decided_at=AHORA, now=AHORA)

    with pytest.raises(SlotTakenError):
        await _en_tx(fabrica, lav, aceptar)


# ── FOR UPDATE ───────────────────────────────────────────────────────────────


async def _en_paralelo(
    fabrica: Factory,
    lav: Lavadero,
    primera: Callable[[Lavadero], Awaitable[Any]],
    segunda: Callable[[Lavadero], Coroutine[Any, Any, Any]],
) -> tuple[Any, Any, bool]:
    """La primera toma su lock y no comitea; la segunda arranca y **debe** esperar.

    Devuelve los dos resultados y si la segunda quedó bloqueada mientras la primera vivía.
    """
    async with fabrica() as s1, fabrica() as s2:
        await s1.begin()
        uno = await primera(lav.en(s1))
        await s2.begin()
        tarea: asyncio.Task[Any] = asyncio.create_task(segunda(lav.en(s2)))
        await asyncio.sleep(ESPERA)
        bloqueada = not tarea.done()
        await s1.commit()
        dos = await tarea
        await s2.commit()
    return uno, dos, bloqueada


async def test_dos_cobros_concurrentes_se_serializan_y_el_segundo_salda(
    fabrica, lav, pg_admin_engine
):
    job = await _en_tx(fabrica, lav, lambda lv: lv.finalizado())
    mitad = PRECIO_FIJO // 2
    uno, dos, bloqueada = await _en_paralelo(
        fabrica,
        lav,
        lambda lv: lv.jobs.record_payment(job.id, lv.pago(mitad, "p1")),
        lambda lv: lv.jobs.record_payment(job.id, lv.pago(mitad, "p2")),
    )
    assert bloqueada, "el segundo cobro no esperó el lock del job"
    assert uno.id != dos.id
    async with fabrica() as s, s.begin():
        lv = lav.en(s)
        assert (await lv.jobs.get(job.id)).status == JobStatus.COBRADO
        assert await lv.jobs.balance(job.id) == 0
    assert (
        await _contar(
            pg_admin_engine,
            "SELECT count(*) FROM job_events WHERE job_id = :j AND event_type = 'JOB_SETTLED'",
            j=job.id,
        )
        == 1
    )


# ── Idempotencia ante la carrera ─────────────────────────────────────────────


async def test_dos_reenvios_concurrentes_de_la_misma_transicion_aplican_una_vez(
    fabrica, lav, pg_admin_engine
):
    job = await _en_tx(fabrica, lav, lambda lv: lv.walk_in())
    uno, dos, bloqueada = await _en_paralelo(
        fabrica,
        lav,
        lambda lv: lv.jobs.start(job.id, idempotency_key="k", occurred_at=T),
        lambda lv: lv.jobs.start(job.id, idempotency_key="k", occurred_at=T),
    )
    assert bloqueada
    assert uno.id == dos.id == job.id
    assert dos.status == JobStatus.EN_PROCESO
    assert (
        await _contar(
            pg_admin_engine, "SELECT count(*) FROM job_events WHERE idempotency_key = 'k'"
        )
        == 1
    )


async def test_dos_walk_in_concurrentes_con_la_misma_clave_crean_un_solo_job(
    fabrica, lav, pg_admin_engine
):
    # Sin fila que bloquear: la red es UNIQUE (tenant_id, idempotency_key). El segundo
    # espera en el índice, choca al comitear el primero y devuelve el job ganador.
    uno, dos, bloqueada = await _en_paralelo(
        fabrica, lav, lambda lv: lv.walk_in(clave="w"), lambda lv: lv.walk_in(clave="w")
    )
    assert bloqueada
    assert uno.id == dos.id
    assert await _contar(pg_admin_engine, "SELECT count(*) FROM jobs") == 1


async def test_dos_recepciones_concurrentes_del_mismo_turno_dan_el_mismo_job(
    fabrica, lav, pg_admin_engine
):
    turno = await _en_tx(fabrica, lav, lambda lv: lv.turno())
    uno, dos, bloqueada = await _en_paralelo(
        fabrica,
        lav,
        lambda lv: lv.recibido(turno, clave="tablet"),
        lambda lv: lv.recibido(turno, clave="celular"),
    )
    assert bloqueada
    assert uno.id == dos.id
    assert await _contar(pg_admin_engine, "SELECT count(*) FROM jobs") == 1


async def test_dos_altas_concurrentes_del_mismo_telefono_dan_un_cliente(
    fabrica, lav, pg_admin_engine
):
    uno, dos, bloqueada = await _en_paralelo(
        fabrica,
        lav,
        lambda lv: lv.clientes.resolve_by_phone("1177778888", name="Nuevo"),
        lambda lv: lv.clientes.resolve_by_phone("11 7777-8888", name="Nuevo"),
    )
    assert bloqueada
    assert uno.created and not dos.created
    assert uno.customer.id == dos.customer.id
    assert (
        await _contar(
            pg_admin_engine, "SELECT count(*) FROM customers WHERE phone_e164 = '+5491177778888'"
        )
        == 1
    )


async def test_dos_altas_concurrentes_de_la_misma_patente_dan_un_auto(fabrica, lav):
    uno, dos, _ = await _en_paralelo(
        fabrica,
        lav,
        lambda lv: lv.vehiculos.resolve_by_plate("EF789GH"),
        lambda lv: lv.vehiculos.resolve_by_plate("ef 789 gh"),
    )
    assert uno.vehicle.id == dos.vehicle.id and not dos.created


async def test_dos_vinculos_concurrentes_dan_uno(fabrica, lav):
    uno, dos, _ = await _en_paralelo(
        fabrica,
        lav,
        lambda lv: lv.vehiculos.link_customer(lv.cliente, lv.vehiculo),
        lambda lv: lv.vehiculos.link_customer(lv.cliente, lv.vehiculo),
    )
    assert uno.id == dos.id


async def test_dos_cancelaciones_concurrentes_dan_una(fabrica, lav):
    turno = await _en_tx(fabrica, lav, lambda lv: lv.turno())
    uno, dos, bloqueada = await _en_paralelo(
        fabrica,
        lav,
        lambda lv: lv.turnos.cancel_by_client(turno.id, now=AHORA),
        lambda lv: lv.turnos.cancel_by_client(turno.id, now=AHORA),
    )
    assert bloqueada
    assert uno.id == dos.id


# ── job_events append-only (X8, B11) ─────────────────────────────────────────


async def test_el_runtime_no_puede_reescribir_la_historia(fabrica, lav):
    job = await _en_tx(fabrica, lav, lambda lv: lv.walk_in())
    for sentencia in (
        "UPDATE job_events SET idempotency_key = 'otra' WHERE job_id = :j",
        "DELETE FROM job_events WHERE job_id = :j",
    ):
        async with fabrica() as s, s.begin():
            await lav.en(s).jobs.get(job.id)  # fija el contexto del tenant
            with pytest.raises(DBAPIError, match="permission denied"):
                await s.execute(text(sentencia), {"j": job.id})


async def test_los_servicios_fijan_el_tenant_y_rls_filtra_el_resto(fabrica, lav, pg_tenant_b):
    job = await _en_tx(fabrica, lav, lambda lv: lv.walk_in())
    async with fabrica() as s, s.begin():
        # Sin contexto: RLS no deja ver nada (el servicio no se usó en esta sesión).
        assert (await s.scalars(select(Job))).all() == []
    async with fabrica() as s, s.begin():
        await lav.en(s).jobs.get(job.id)
        assert [j.id for j in (await s.scalars(select(Job))).all()] == [job.id]
        assert (await s.scalars(select(JobEvent))).all() != []
        assert (await s.scalars(select(Customer))).all() != []


# ── B13 de punta a punta y A1 ────────────────────────────────────────────────


async def test_b13_y_a1_contra_postgres(fabrica, lav, pg_admin_engine):
    # recibir dos veces el mismo turno devuelve el mismo job
    turno = await _en_tx(fabrica, lav, lambda lv: lv.turno(servicio=lv.lavado_con_sena, hold=HOLD))
    await _en_tx(
        fabrica, lav, lambda lv: lv.turnos.confirm_deposit(turno.id, lv.pago(SENA), now=AHORA)
    )
    job = await _en_tx(fabrica, lav, lambda lv: lv.recibido(turno, clave="r1"))
    assert (await _en_tx(fabrica, lav, lambda lv: lv.recibido(turno, clave="r2"))).id == job.id

    # segundo cobro suma
    job = await _en_tx(fabrica, lav, lambda lv: lv.finalizado(job))
    await _en_tx(fabrica, lav, lambda lv: lv.jobs.record_payment(job.id, lv.pago(1_000_000)))
    saldo_final = await _en_tx(fabrica, lav, lambda lv: lv.jobs.balance(job.id))
    assert saldo_final == 3_000_000 - SENA - 1_000_000
    ultimo = await _en_tx(
        fabrica, lav, lambda lv: lv.jobs.record_payment(job.id, lv.pago(saldo_final, "ultimo"))
    )
    async with fabrica() as s, s.begin():
        assert (await lav.en(s).jobs.get(job.id)).status == JobStatus.COBRADO

    # A1: anular un cobro de un COBRADO que dejaría saldo > 0 falla
    with pytest.raises(GuardFailedError):
        await _en_tx(
            fabrica,
            lav,
            lambda lv: lv.jobs.void_payment(
                ultimo.id, idempotency_key="v", occurred_at=T, reason="Error de carga"
            ),
        )

    # cancelar por demora un FINALIZADO (acá ya COBRADO) falla
    with pytest.raises(InvalidTransition):
        await _en_tx(
            fabrica,
            lav,
            lambda lv: lv.jobs.cancel_for_delay(
                job.id, idempotency_key="c", occurred_at=T + timedelta(hours=2), tolerance_min=20
            ),
        )

    # walk-in no se cancela por demora
    walk = await _en_tx(fabrica, lav, lambda lv: lv.walk_in())
    with pytest.raises(GuardFailedError):
        await _en_tx(
            fabrica,
            lav,
            lambda lv: lv.jobs.cancel_for_delay(
                walk.id, idempotency_key="c", occurred_at=T + timedelta(hours=2), tolerance_min=20
            ),
        )
    assert (
        await _contar(
            pg_admin_engine,
            "SELECT count(*) FROM job_events WHERE event_type = :t",
            t=JobEventType.JOB_CANCELLED_DELAY.value,
        )
        == 0
    )


async def test_cancelar_por_demora_y_revertir_contra_postgres(fabrica, lav, pg_admin_engine):
    turno = await _en_tx(fabrica, lav, lambda lv: lv.turno())
    job = await _en_tx(
        fabrica, lav, lambda lv: lv.recibido(turno, llegada=T + timedelta(minutes=45))
    )
    await _en_tx(
        fabrica,
        lav,
        lambda lv: lv.jobs.cancel_for_delay(
            job.id, idempotency_key="c", occurred_at=T + timedelta(minutes=45), tolerance_min=20
        ),
    )
    await _en_tx(
        fabrica,
        lav,
        lambda lv: lv.jobs.reverse_retention(
            job.id, idempotency_key="rev", occurred_at=T + timedelta(hours=1), reason="Avisó"
        ),
    )
    async with fabrica() as s, s.begin():
        lv = lav.en(s)
        assert (await lv.turnos.get(turno.id)).status == BookingStatus.CANCELADO_DEMORA
        tipos = [e.event_type for e in await lv.jobs.history(job.id)]
    assert tipos[-3:] == [
        JobEventType.JOB_CANCELLED_DELAY,
        JobEventType.DEPOSIT_RETAINED,
        JobEventType.DEPOSIT_RETENTION_REVERSED,
    ]


# ── Arreglos de T3 (revisor adversarial y /code-review) ─────────────────────


async def test_f3_dos_walk_in_concurrentes_con_la_misma_cotizacion_dan_un_job(
    fabrica, lav, pg_admin_engine
):
    """El único parcial `(tenant_id, quote_id)` es la red: el segundo espera en el índice y,
    al comitear el primero, recibe un error de dominio (no un `IntegrityError` → 500)."""

    async def aceptada(lv: Lavadero) -> Any:
        quote = await lv.cotizaciones.create(
            customer_id=lv.cliente,
            service_id=lv.tapizado,
            vehicle_size_id=lv.auto,
            requested_at=AHORA,
        )
        await lv.cotizaciones.quote(
            quote.id, agreed_price_cents=1_000_000, agreed_duration_min=60, quoted_at=AHORA
        )
        return await lv.cotizaciones.accept(quote.id, decided_at=AHORA, now=AHORA)

    quote = await _en_tx(fabrica, lav, aceptada)

    def walk_in(lv: Lavadero, clave: str) -> Coroutine[Any, Any, Any]:
        return lv.jobs.receive(
            idempotency_key=clave,
            arrived_at=T,
            vehicle_id=lv.vehiculo,
            vehicle_size_id=lv.auto,
            service_id=lv.tapizado,
            channel=Channel.CALLE,
            quote_id=quote.id,
        )

    with pytest.raises(QuoteAlreadyUsedError):
        await _en_paralelo(
            fabrica, lav, lambda lv: walk_in(lv, "tablet"), lambda lv: walk_in(lv, "celular")
        )
    assert (
        await _contar(pg_admin_engine, "SELECT count(*) FROM jobs WHERE quote_id = :q", q=quote.id)
        == 1
    )


async def test_f5_la_sena_que_llega_despues_de_perder_el_horario_queda_registrada(
    fabrica, lav, pg_admin_engine
):
    """El escenario del revisor: el hold de A vence, B toma el horario y la seña de A llega
    después. `confirm_deposit` y `confirm_late` fallan; la plata igual queda registrada."""
    a = await _en_tx(fabrica, lav, lambda lv: lv.turno(servicio=lv.lavado_con_sena, hold=HOLD))
    await _en_tx(fabrica, lav, lambda lv: lv.turno(ahora=HOLD + timedelta(minutes=1)))
    tarde = HOLD + timedelta(minutes=2)
    with pytest.raises(InvalidTransition):
        await _en_tx(
            fabrica, lav, lambda lv: lv.turnos.confirm_deposit(a.id, lv.pago(SENA), now=tarde)
        )
    with pytest.raises(SlotTakenError):
        await _en_tx(
            fabrica,
            lav,
            lambda lv: lv.turnos.confirm_late(a.id, now=tarde, payment=lv.pago(SENA)),
        )
    c = await _en_tx(
        fabrica,
        lav,
        lambda lv: lv.turnos.record_deposit_after_slot_lost(
            a.id, lv.pago(SENA, "sena-tarde"), now=tarde
        ),
    )
    otra = await _en_tx(
        fabrica,
        lav,
        lambda lv: lv.turnos.record_deposit_after_slot_lost(
            a.id, lv.pago(SENA, "sena-tarde"), now=tarde
        ),
    )
    assert otra.id == c.id
    assert c.deposit_status == DepositStatus.REPROGRAMACION_O_DEVOLUCION_PENDIENTE
    assert c.deposit_paid_cents == SENA
    sql = "SELECT count(*) FROM payments WHERE booking_id = :b AND kind = 'SEÑA'"
    assert await _contar(pg_admin_engine, sql, b=a.id) == 1
    assert (
        await _contar(
            pg_admin_engine, "SELECT count(*) FROM cancellations WHERE booking_id = :b", b=a.id
        )
        == 1
    )
    async with fabrica() as s, s.begin():
        assert (await lav.en(s).turnos.get(a.id)).status == BookingStatus.VENCIDO


async def test_f12_dos_turnos_vencidos_del_mismo_puesto_no_se_bloquean_en_cruz(fabrica, lav):
    """Cada transacción tiene tomado **su** turno (un hold vencido) y vence los holds del
    puesto: sin `SKIP LOCKED` una espera el turno de la otra y viceversa (40P01)."""
    x = await _en_tx(fabrica, lav, lambda lv: lv.turno(servicio=lv.lavado_con_sena, hold=HOLD))
    y = await _en_tx(
        fabrica,
        lav,
        lambda lv: lv.turno(inicio=T + timedelta(hours=3), servicio=lv.lavado_con_sena, hold=HOLD),
    )
    tarde = HOLD + timedelta(minutes=1)

    async def tomar(lv: Lavadero, turno_id: uuid.UUID) -> None:
        await lv.turnos.get(turno_id)  # fija el contexto del tenant
        await BookingRepository(lv.session).get_for_update(turno_id, lv.tenant_id)

    async with fabrica() as s1, fabrica() as s2:
        await s1.begin()
        await s2.begin()
        uno, dos = lav.en(s1), lav.en(s2)
        await tomar(uno, x.id)
        await tomar(dos, y.id)
        resultados = await asyncio.wait_for(
            asyncio.gather(
                uno.turnos.expire_holds(lav.puesto, tarde),
                dos.turnos.expire_holds(lav.puesto, tarde),
                return_exceptions=True,
            ),
            timeout=10,
        )
        errores = [r for r in resultados if isinstance(r, BaseException)]
        assert not errores, errores
        await s1.commit()
        await s2.commit()
    vencidos_1, vencidos_2 = resultados
    assert [b.id for b in vencidos_1] == [x.id]  # el de la otra lo resuelve la otra
    assert [b.id for b in vencidos_2] == [y.id]
