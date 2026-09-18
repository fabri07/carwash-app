"""B8/B9 · FASE-3-CONTRATO §4 — los servicios no cruzan lavaderos, contra **Postgres real**.

Escrito por el Tester-aislamiento de T3, que no escribió los servicios. Todo corre con el rol
de runtime `carwash_app` (RLS activo) y con sesiones **sin** contexto: lo fija el servicio con
el tenant con el que se construyó, que es lo que se ataca.

1. **Ids ajenos** (`_intentos_servicios.INTENTOS`). El lavadero B construye cada servicio con
   su tenant y le pasa un id de A, que apunta a una fila en el estado en que la operación
   funcionaría. Se exige:
   - `NotFoundError` de la tabla esperada (el 404 de F4+);
   - **indistinguible** del de un id inexistente: mismo tipo, misma tabla y mismo mensaje con el
     id reemplazado (así el mensaje tampoco puede llevar ningún dato de A);
   - **ninguna fila de A cambia**: foto con `row_to_json` de todas las tablas de A, con el
     superusuario, antes y después. La transacción de B se **comitea** después del error (el
     peor llamador: uno que se traga la excepción), así un efecto parcial quedaría a la vista.
2. **B9 por los servicios.** La misma patente, teléfono, código de turno e `idempotency_key`
   conviven en A y B: la clave de uno no le da al otro "ya aplicado" ni
   `IdempotencyKeyReusedError`, en los dos órdenes.

La misma tabla de intentos corre en SQLite (`test_aislamiento_servicios.py`, tercera red); ahí
vive también el test que falla si aparece una operación con id sin intento.

Datos sintéticos (`1144445555`, `AB123CD`, `@ejemplo.invalid`).
"""

import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.application.services.errors import (
    AlreadyExistsError,
    IdempotencyKeyReusedError,
    NotFoundError,
)
from app.domain.enums import BookingStatus, DepositStatus
from app.persistence.repositories.jobs import JobEventRepository
from app.tests.application._armado import AHORA, PRECIO_FIJO, SENA, Lavadero, T, armar_catalogo
from app.tests.security._intentos_servicios import (
    CONTROL_NO_APLICA,
    INTENTOS,
    Ids,
    armar_a,
    armar_b,
    cancelada,
    k,
    sin_id,
)
from app.tests.security._poblar_dominio import tablas_tenant

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

Factory = async_sessionmaker[AsyncSession]


@dataclass
class Dos:
    fabrica: Factory
    admin: AsyncEngine
    a: Lavadero
    b: Lavadero


@dataclass
class Escenario(Dos):
    ids_a: Ids
    ids_b: Ids


async def _lavadero(
    fabrica: Factory, pg_user_factory: Any, tenant_id: uuid.UUID, nombre: str
) -> Lavadero:
    owner = await pg_user_factory(
        tenant_id, f"owner-{nombre}-{uuid.uuid4().hex[:6]}@ejemplo.invalid"
    )
    async with fabrica() as s, s.begin():
        return await armar_catalogo(s, tenant_id, owner)


async def _en_tx[R](fabrica: Factory, lv: Lavadero, fn: Callable[[Lavadero], Awaitable[R]]) -> R:
    async with fabrica() as s, s.begin():
        return await fn(lv.en(s))


@pytest_asyncio.fixture
async def dos(
    pg_session_factory: Factory,
    pg_admin_engine: AsyncEngine,
    pg_tenant_a: uuid.UUID,
    pg_tenant_b: uuid.UUID,
    pg_user_factory: Any,
) -> AsyncGenerator[Dos, None]:
    """Dos lavaderos con el mismo catálogo, el mismo teléfono (`11 4444-5555`) y la misma
    patente (`AB 123 CD`): `armar_catalogo` ya es un primer B9."""
    a = await _lavadero(pg_session_factory, pg_user_factory, pg_tenant_a, "a")
    b = await _lavadero(pg_session_factory, pg_user_factory, pg_tenant_b, "b")
    yield Dos(pg_session_factory, pg_admin_engine, a, b)


@pytest_asyncio.fixture
async def esc(dos: Dos) -> Escenario:
    ids_a = await _en_tx(dos.fabrica, dos.a, armar_a)
    ids_b = await _en_tx(dos.fabrica, dos.b, armar_b)
    return Escenario(dos.fabrica, dos.admin, dos.a, dos.b, ids_a, ids_b)


async def _foto(admin: AsyncEngine, tenant_id: uuid.UUID) -> dict[str, list[str]]:
    """Todas las filas del lavadero, tabla por tabla, como JSON (superusuario: sin RLS)."""
    foto: dict[str, list[str]] = {}
    async with admin.connect() as conn:
        for tabla in tablas_tenant():
            filas = await conn.scalars(
                text(
                    f"SELECT row_to_json(t)::text FROM {tabla.name} t "  # noqa: S608
                    "WHERE tenant_id = :t ORDER BY id"
                ),
                {"t": tenant_id},
            )
            foto[tabla.name] = list(filas.all())
    return foto


async def _intentar(
    fabrica: Factory, lv: Lavadero, fn: Callable[[Lavadero], Awaitable[Any]]
) -> BaseException | None:
    """Corre `fn` y **comitea igual** si levantó una excepción de Python (el peor llamador)."""
    async with fabrica() as s:
        await s.begin()
        error: BaseException | None = None
        try:
            await fn(lv.en(s))
        except Exception as exc:  # noqa: BLE001
            error = exc
        try:
            await s.commit()
        except DBAPIError:  # la base ya había rechazado: la transacción no se puede comitear
            await s.rollback()
    return error


# ── 1. Ids ajenos: operación por operación ───────────────────────────────────


@pytest.mark.parametrize("nombre", sorted(INTENTOS))
async def test_b_con_un_id_de_a_recibe_not_found_indistinguible_y_a_no_cambia(
    esc: Escenario, nombre: str
) -> None:
    intento = INTENTOS[nombre]
    id_a = esc.ids_a[intento.de_a]
    inexistente = uuid.uuid4()
    antes = await _foto(esc.admin, esc.a.tenant_id)

    ajeno = await _intentar(esc.fabrica, esc.b, lambda lv: intento.llamar(lv, esc.ids_b, id_a))
    nada = await _intentar(
        esc.fabrica, esc.b, lambda lv: intento.llamar(lv, esc.ids_b, inexistente)
    )

    assert await _foto(esc.admin, esc.a.tenant_id) == antes, "B cambió filas de A"
    assert isinstance(ajeno, NotFoundError), f"{nombre}: {type(ajeno).__name__}: {ajeno}"
    assert ajeno.entity == intento.tabla
    assert type(nada) is type(ajeno)
    assert isinstance(nada, NotFoundError) and nada.entity == ajeno.entity
    assert sin_id(str(ajeno), id_a) == sin_id(str(nada), inexistente)
    assert str(esc.a.tenant_id) not in str(ajeno)


async def test_el_control_con_el_tenant_a_funciona(esc: Escenario) -> None:
    """Control: con su propio tenant, las filas de A sí sirven para la operación. Si no, el
    `NotFoundError` de arriba podría venir del estado y no del tenant."""
    fallidas = []
    for nombre, intento in INTENTOS.items():
        if nombre in CONTROL_NO_APLICA:
            continue
        # Cada una en su transacción y con rollback: varias usan la misma fila de A.
        async with esc.fabrica() as s:
            await s.begin()
            try:
                await intento.llamar(esc.a.en(s), esc.ids_a, esc.ids_a[intento.de_a])
            except Exception as exc:  # noqa: BLE001
                fallidas.append(f"{nombre}: {type(exc).__name__}: {exc}")
            finally:
                await s.rollback()
    assert not fallidas, "\n".join(fallidas)


async def test_expire_holds_con_el_puesto_de_a_no_vence_nada(esc: Escenario) -> None:
    antes = await _foto(esc.admin, esc.a.tenant_id)
    vencidos: list[Any] = []

    async def vencer(lv: Lavadero) -> None:
        vencidos.extend(await lv.turnos.expire_holds(esc.ids_a["puesto"], T + timedelta(days=30)))

    assert await _intentar(esc.fabrica, esc.b, vencer) is None
    assert vencidos == []
    assert await _foto(esc.admin, esc.a.tenant_id) == antes
    async with esc.fabrica() as s, s.begin():  # el hold de A sigue vivo
        turno = await esc.a.en(s).turnos.get(esc.ids_a["booking_pend_sena"])
        assert turno.status == BookingStatus.PENDIENTE_SENA


# ── 2. B9 por los servicios ──────────────────────────────────────────────────


async def test_mismo_telefono_y_patente_en_a_y_b_cada_uno_resuelve_el_suyo(dos: Dos) -> None:
    a, b = dos.a, dos.b
    assert a.cliente != b.cliente and a.vehiculo != b.vehiculo  # `armar_catalogo` en los dos

    async def resolver(lv: Lavadero) -> tuple[Any, Any]:
        return (
            await lv.clientes.resolve_by_phone("1144445555", name="Otro nombre"),
            await lv.vehiculos.resolve_by_plate("ab123cd"),
        )

    for lv in (a, b):
        cliente, auto = await _en_tx(dos.fabrica, lv, resolver)
        assert not cliente.created and cliente.customer.id == lv.cliente
        assert not auto.created and auto.vehicle.id == lv.vehiculo

    # Una clave nueva: A la crea, B **también** la crea (no "ya existe"), A vuelve a la suya.
    async def nuevo(lv: Lavadero) -> tuple[Any, Any]:
        return (
            await lv.clientes.resolve_by_phone("11 5555-6666", name="Cliente Dos"),
            await lv.vehiculos.resolve_by_plate("AC 456 DE"),
        )

    c_a, v_a = await _en_tx(dos.fabrica, a, nuevo)
    c_b, v_b = await _en_tx(dos.fabrica, b, nuevo)
    assert c_a.created and c_b.created and v_a.created and v_b.created
    assert c_a.customer.id != c_b.customer.id and v_a.vehicle.id != v_b.vehicle.id
    assert c_b.customer.tenant_id == b.tenant_id and v_b.vehicle.tenant_id == b.tenant_id
    c_a2, v_a2 = await _en_tx(dos.fabrica, a, nuevo)
    assert (c_a2.customer.id, v_a2.vehicle.id) == (c_a.customer.id, v_a.vehicle.id)


async def test_mismo_codigo_de_turno_y_mismo_horario_en_a_y_b(dos: Dos) -> None:
    inicio = T + timedelta(days=5)

    async def reservar(lv: Lavadero) -> Any:
        return await lv.turno(inicio=inicio, codigo="TUR-COMUN")

    turno_a = await _en_tx(dos.fabrica, dos.a, reservar)
    turno_b = await _en_tx(dos.fabrica, dos.b, reservar)
    assert turno_a.id != turno_b.id
    assert turno_b.tenant_id == dos.b.tenant_id and turno_b.status == BookingStatus.CONFIRMADO
    with pytest.raises(AlreadyExistsError):  # control: dentro de A sí choca
        await _en_tx(
            dos.fabrica,
            dos.a,
            lambda lv: lv.turno(inicio=inicio + timedelta(days=1), codigo="TUR-COMUN"),
        )


@dataclass(frozen=True)
class ConClave:
    """Operación con clave de idempotencia: `preparar` deja la fila, `aplicar` usa la clave y
    devuelve el id de lo que la clave identifica (el evento o el pago)."""

    preparar: Callable[[Lavadero], Awaitable[Any]]
    aplicar: Callable[[Lavadero, Any, str], Awaitable[uuid.UUID]]


async def _evento(lv: Lavadero, clave: str) -> uuid.UUID:
    evento = await JobEventRepository(lv.session).get_by_key(clave, lv.tenant_id)
    assert evento is not None and evento.tenant_id == lv.tenant_id
    return evento.id


async def _nada(lv: Lavadero) -> None:
    return None


async def _con_pago_parcial(lv: Lavadero) -> Any:
    return await lv.jobs.record_payment((await lv.finalizado()).id, lv.pago(1_000))


async def _cobrado(lv: Lavadero) -> Any:
    job = await lv.finalizado()
    await lv.jobs.record_payment(job.id, lv.pago(PRECIO_FIJO))
    return job


async def _recibido_tarde(lv: Lavadero) -> Any:
    inicio = T + timedelta(days=2)
    return await lv.recibido(await lv.turno(inicio=inicio), llegada=inicio + timedelta(minutes=30))


async def _cancelado_por_demora(lv: Lavadero) -> Any:
    job = await _recibido_tarde(lv)
    await lv.jobs.cancel_for_delay(
        job.id,
        idempotency_key=k(),
        occurred_at=T + timedelta(days=2, minutes=40),
        tolerance_min=20,
    )
    return job


async def _en_proceso(lv: Lavadero) -> Any:
    job = await lv.walk_in()
    await lv.jobs.start(job.id, idempotency_key=k(), occurred_at=T)
    return job


async def _aplicar_y_evento(lv: Lavadero, op: Awaitable[Any], clave: str) -> uuid.UUID:
    await op
    return await _evento(lv, clave)


CON_CLAVE: dict[str, ConClave] = {
    "receive.walk_in": ConClave(
        _nada, lambda lv, _, k: _aplicar_y_evento(lv, lv.walk_in(clave=k), k)
    ),
    "receive.booking": ConClave(
        lambda lv: lv.turno(inicio=T + timedelta(days=3)),
        lambda lv, turno, k: _aplicar_y_evento(lv, lv.recibido(turno, clave=k), k),
    ),
    "start": ConClave(
        lambda lv: lv.walk_in(),
        lambda lv, job, k: _aplicar_y_evento(
            lv, lv.jobs.start(job.id, idempotency_key=k, occurred_at=T), k
        ),
    ),
    "finish": ConClave(
        _en_proceso,
        lambda lv, job, k: _aplicar_y_evento(
            lv, lv.jobs.finish(job.id, idempotency_key=k, occurred_at=T), k
        ),
    ),
    "pick_up": ConClave(
        _cobrado,
        lambda lv, job, k: _aplicar_y_evento(
            lv, lv.jobs.pick_up(job.id, idempotency_key=k, occurred_at=T), k
        ),
    ),
    "record_payment": ConClave(
        lambda lv: lv.finalizado(),
        lambda lv, job, k: _id(lv.jobs.record_payment(job.id, lv.pago(1_000, k))),
    ),
    "void_payment": ConClave(
        _con_pago_parcial,
        lambda lv, pago, k: _aplicar_y_evento(
            lv, lv.jobs.void_payment(pago.id, idempotency_key=k, occurred_at=T, reason="Doble"), k
        ),
    ),
    "adjust_price": ConClave(
        lambda lv: lv.walk_in(),
        lambda lv, job, k: _aplicar_y_evento(
            lv,
            lv.jobs.adjust_price(
                job.id,
                idempotency_key=k,
                occurred_at=T,
                surcharge_cents=0,
                discount_cents=1_000,
                reason="Promo",
            ),
            k,
        ),
    ),
    "record_inspection": ConClave(
        lambda lv: lv.walk_in(),
        lambda lv, job, k: _aplicar_y_evento(
            lv, lv.jobs.record_inspection(job.id, idempotency_key=k, occurred_at=T), k
        ),
    ),
    "cancel_for_delay": ConClave(
        _recibido_tarde,
        lambda lv, job, k: _aplicar_y_evento(
            lv,
            lv.jobs.cancel_for_delay(
                job.id,
                idempotency_key=k,
                occurred_at=T + timedelta(days=2, minutes=40),
                tolerance_min=20,
            ),
            k,
        ),
    ),
    "reverse_retention": ConClave(
        _cancelado_por_demora,
        lambda lv, job, k: _aplicar_y_evento(
            lv,
            lv.jobs.reverse_retention(job.id, idempotency_key=k, occurred_at=T, reason="Avisó"),
            k,
        ),
    ),
    "confirm_deposit": ConClave(
        lambda lv: lv.turno(
            inicio=T + timedelta(days=4),
            servicio=lv.lavado_con_sena,
            hold=AHORA + timedelta(hours=2),
        ),
        lambda lv, turno, k: _id_pago_sena(lv, turno, k),
    ),
    "resolve.refund": ConClave(
        lambda lv: cancelada(lv, T + timedelta(days=6)),
        lambda lv, c, k: _id_devolucion(lv, c, k),
    ),
}


async def _id(op: Awaitable[Any]) -> uuid.UUID:
    fila = await op
    return uuid.UUID(str(fila.id))


async def _id_pago_sena(lv: Lavadero, turno: Any, clave: str) -> uuid.UUID:
    confirmacion = await lv.turnos.confirm_deposit(turno.id, lv.pago(SENA, clave), now=AHORA)
    return uuid.UUID(str(confirmacion.payment.id))


async def _id_devolucion(lv: Lavadero, cancelacion: Any, clave: str) -> uuid.UUID:
    resuelta = await lv.senas.resolve(
        cancelacion.id,
        status=DepositStatus.DEVUELTA,
        reason="Transferido",
        resolved_at=T,
        refund=lv.pago(SENA, clave),
    )
    assert resuelta.refund_payment_id is not None
    return uuid.UUID(str(resuelta.refund_payment_id))


@pytest.mark.parametrize("orden", ["a_primero", "b_primero"])
@pytest.mark.parametrize("op", sorted(CON_CLAVE))
async def test_la_misma_clave_de_idempotencia_convive_en_a_y_b(
    dos: Dos, op: str, orden: str
) -> None:
    """La clave del primero no le da al segundo "ya aplicado" ni `IdempotencyKeyReusedError`."""
    caso = CON_CLAVE[op]
    primero, segundo = (dos.a, dos.b) if orden == "a_primero" else (dos.b, dos.a)
    clave = f"clave-comun-{op}"

    async def correr(lv: Lavadero) -> tuple[Any, uuid.UUID]:
        fila = await caso.preparar(lv)
        return fila, await caso.aplicar(lv, fila, clave)

    fila_1, id_1 = await _en_tx(dos.fabrica, primero, correr)
    fila_2, id_2 = await _en_tx(dos.fabrica, segundo, correr)  # no levanta
    assert id_1 != id_2, "el segundo lavadero recibió el resultado del primero"

    # Cada uno sigue viendo lo suyo: el reenvío del primero devuelve su resultado original.
    assert await _en_tx(dos.fabrica, primero, lambda lv: caso.aplicar(lv, fila_1, clave)) == id_1
    assert await _en_tx(dos.fabrica, segundo, lambda lv: caso.aplicar(lv, fila_2, clave)) == id_2


async def test_la_clave_usada_por_a_choca_dentro_de_a_y_no_en_b(dos: Dos) -> None:
    """Control de B9: el chequeo de reuso está vivo (choca dentro de A) y es por tenant."""
    await _en_tx(dos.fabrica, dos.a, lambda lv: lv.walk_in(clave="clave-de-a"))

    async def iniciar_otro(lv: Lavadero) -> Any:
        job = await lv.walk_in()
        return await lv.jobs.start(job.id, idempotency_key="clave-de-a", occurred_at=T)

    with pytest.raises(IdempotencyKeyReusedError):
        await _en_tx(dos.fabrica, dos.a, iniciar_otro)
    iniciado = await _en_tx(dos.fabrica, dos.b, iniciar_otro)
    assert iniciado.tenant_id == dos.b.tenant_id
