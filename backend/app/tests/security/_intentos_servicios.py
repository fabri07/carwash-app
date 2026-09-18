"""Intentos de cruce por los servicios — compartido por `test_aislamiento_servicios.py` (SQLite,
tercera red: el filtro por `tenant_id` del repositorio) y `test_aislamiento_servicios_pg.py`
(Postgres con RLS, el rol real). Tester-aislamiento de T3.

- `armar_a` deja una fila de A lista para cada operación: en el estado en que la operación
  **funcionaría** si el tenant no se chequeara (un job `PRESENTE` para `start`, un turno
  `PENDIENTE_SEÑA` para `confirm_deposit`, …). Así un "no encontrado" por estado no tapa una fuga.
- `INTENTOS`: cada operación pública de servicio que recibe un id, con el id de A que se le pasa
  y la tabla que tiene que nombrar el `NotFoundError`.
- `operaciones_con_id()` descubre las operaciones por introspección: una nueva sin intento hace
  fallar el test de cobertura.

Datos sintéticos (`1144445555`, `AB123CD`, `@ejemplo.invalid`).
"""

import inspect
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from app.application import services as paquete_servicios
from app.application.services._base import ServiceBase
from app.domain.enums import BookingSource, Channel, DepositStatus, PricingMode
from app.tests.application._armado import AHORA, PRECIO_FIJO, SENA, Lavadero, T

Ids = dict[str, uuid.UUID]


def k() -> str:
    return f"k-{uuid.uuid4()}"


# ── Armado ────────────────────────────────────────────────────────────────────


def _catalogo(lv: Lavadero) -> Ids:
    return {
        "owner": lv.owner_id,
        "auto": lv.auto,
        "lavado": lv.lavado,
        "tapizado": lv.tapizado,
        "puesto": lv.puesto,
        "efectivo": lv.efectivo,
        "cliente": lv.cliente,
        "vehiculo": lv.vehiculo,
    }


async def cancelada(lv: Lavadero, inicio: Any) -> Any:
    """Turno con seña pagada, cancelado a tiempo: caso `DEVOLUCION_PENDIENTE`."""
    turno = await lv.turno(
        inicio=inicio, servicio=lv.lavado_con_sena, hold=AHORA + timedelta(hours=2)
    )
    await lv.turnos.confirm_deposit(turno.id, lv.pago(SENA), now=AHORA)
    cancelacion = await lv.turnos.cancel_by_client(turno.id, requested_at=AHORA)
    assert cancelacion.deposit_status == DepositStatus.DEVOLUCION_PENDIENTE
    return cancelacion


async def armar_a(lv: Lavadero) -> Ids:
    """Una fila de A lista para cada operación: si el tenant no se chequeara, funcionaría."""
    ids = _catalogo(lv)
    ids["job_presente"] = (await lv.walk_in()).id
    en_proceso = await lv.walk_in()
    await lv.jobs.start(en_proceso.id, idempotency_key=k(), occurred_at=T)
    ids["job_en_proceso"] = en_proceso.id
    ids["job_finalizado"] = (await lv.finalizado()).id
    con_pago = await lv.finalizado()
    ids["pago"] = (await lv.jobs.record_payment(con_pago.id, lv.pago(1_000))).id
    cobrado = await lv.finalizado()
    await lv.jobs.record_payment(cobrado.id, lv.pago(PRECIO_FIJO))
    ids["job_cobrado"] = cobrado.id
    # Turno a las T (puesto 1), llegó 30' tarde: cancelable por demora con tolerancia 20.
    ids["job_turno_presente"] = (await lv.recibido(llegada=T + timedelta(minutes=30))).id
    otra_hora = T + timedelta(hours=8)
    demorado = await lv.recibido(
        await lv.turno(inicio=otra_hora), llegada=otra_hora + timedelta(minutes=30)
    )
    await lv.jobs.cancel_for_delay(
        demorado.id,
        idempotency_key=k(),
        occurred_at=otra_hora + timedelta(minutes=40),
        tolerance_min=20,
    )
    ids["job_cancelado_demora"] = demorado.id
    ids["booking_conf"] = (await lv.turno(inicio=T + timedelta(hours=2))).id
    ids["booking_pend_sena"] = (
        await lv.turno(
            inicio=T + timedelta(hours=4),
            servicio=lv.lavado_con_sena,
            hold=AHORA + timedelta(hours=2),
        )
    ).id
    vencido = await lv.turno(
        inicio=T + timedelta(hours=6),
        servicio=lv.lavado_con_sena,
        puesto=lv.puesto_2,
        hold=AHORA + timedelta(minutes=10),
    )
    await lv.turnos.expire_holds(lv.puesto_2, AHORA + timedelta(minutes=20))
    ids["booking_vencido"] = vencido.id
    ids["cancelacion"] = (await cancelada(lv, T + timedelta(hours=10))).id

    async def cotizacion() -> Any:
        return await lv.cotizaciones.create(
            customer_id=lv.cliente,
            service_id=lv.tapizado,
            vehicle_size_id=lv.auto,
            vehicle_id=lv.vehiculo,
            requested_at=AHORA,
            expires_at=AHORA + timedelta(hours=1),
        )

    ids["quote_pendiente"] = (await cotizacion()).id
    cotizada = await cotizacion()
    await lv.cotizaciones.quote(
        cotizada.id, agreed_price_cents=1_000_000, agreed_duration_min=120, quoted_at=AHORA
    )
    ids["quote_cotizado"] = cotizada.id
    aceptada = await cotizacion()
    await lv.cotizaciones.quote(
        aceptada.id, agreed_price_cents=1_000_000, agreed_duration_min=120, quoted_at=AHORA
    )
    await lv.cotizaciones.accept(aceptada.id, decided_at=AHORA, now=AHORA)
    ids["quote_aceptado"] = aceptada.id
    return ids


async def armar_b(lv: Lavadero) -> Ids:
    """Lo propio de B que algunas operaciones combinan con un id de A."""
    ids = _catalogo(lv)
    ids["job_finalizado"] = (await lv.finalizado()).id
    ids["booking_pend_sena"] = (
        await lv.turno(
            inicio=T + timedelta(hours=4),
            servicio=lv.lavado_con_sena,
            hold=AHORA + timedelta(hours=2),
        )
    ).id
    ids["cancelacion"] = (await cancelada(lv, T + timedelta(hours=10))).id
    return ids


# ── 1. Ids ajenos: operación por operación ───────────────────────────────────

Llamada = Callable[[Lavadero, Ids, uuid.UUID], Awaitable[Any]]


@dataclass(frozen=True)
class Intento:
    #: Qué fila de A se pasa (clave de `_armar_a`).
    de_a: str
    #: Tabla que tiene que nombrar el `NotFoundError`.
    tabla: str
    llamar: Llamada
    #: `Servicio.metodo` que cubre (para el chequeo de cobertura).
    cubre: str


def _walk_in(lv: Lavadero, ib: Ids, **pisar: Any) -> Awaitable[Any]:
    datos: dict[str, Any] = {
        "idempotency_key": k(),
        "arrived_at": T,
        "vehicle_id": ib["vehiculo"],
        "vehicle_size_id": ib["auto"],
        "service_id": ib["lavado"],
        "channel": Channel.CALLE,
    }
    return lv.jobs.receive(**{**datos, **pisar})


def _turno_b(lv: Lavadero, ib: Ids, **pisar: Any) -> Awaitable[Any]:
    datos: dict[str, Any] = {
        "code": f"TUR-{uuid.uuid4().hex[:8]}",
        "source": BookingSource.PANEL,
        "channel": Channel.WHATSAPP,
        "resource_id": ib["puesto"],
        "start_at": T + timedelta(days=3),
        "customer_id": ib["cliente"],
        "service_id": ib["lavado"],
        "vehicle_size_id": ib["auto"],
        "vehicle_id": ib["vehiculo"],
        "now": AHORA,
    }
    return lv.turnos.create(**{**datos, **pisar})


def _cotizacion_b(lv: Lavadero, ib: Ids, **pisar: Any) -> Awaitable[Any]:
    datos: dict[str, Any] = {
        "customer_id": ib["cliente"],
        "service_id": ib["tapizado"],
        "vehicle_size_id": ib["auto"],
        "vehicle_id": ib["vehiculo"],
        "requested_at": AHORA,
    }
    return lv.cotizaciones.create(**{**datos, **pisar})


INTENTOS: dict[str, Intento] = {
    # ── JobService ──
    "job.get": Intento("job_presente", "jobs", lambda lv, ib, x: lv.jobs.get(x), "JobService.get"),
    "job.history": Intento(
        "job_presente", "jobs", lambda lv, ib, x: lv.jobs.history(x), "JobService.history"
    ),
    "job.balance": Intento(
        "job_finalizado", "jobs", lambda lv, ib, x: lv.jobs.balance(x), "JobService.balance"
    ),
    "job.start": Intento(
        "job_presente",
        "jobs",
        lambda lv, ib, x: lv.jobs.start(x, idempotency_key=k(), occurred_at=T),
        "JobService.start",
    ),
    "job.finish": Intento(
        "job_en_proceso",
        "jobs",
        lambda lv, ib, x: lv.jobs.finish(x, idempotency_key=k(), occurred_at=T),
        "JobService.finish",
    ),
    "job.pick_up": Intento(
        "job_cobrado",
        "jobs",
        lambda lv, ib, x: lv.jobs.pick_up(x, idempotency_key=k(), occurred_at=T),
        "JobService.pick_up",
    ),
    "job.record_payment": Intento(
        "job_finalizado",
        "jobs",
        # con el medio de pago de B: si el job se leyera, el cobro entraría
        lambda lv, ib, x: lv.jobs.record_payment(x, lv.pago(1_000)),
        "JobService.record_payment",
    ),
    "job.record_payment.medio": Intento(
        "efectivo",
        "payment_methods",
        lambda lv, ib, x: lv.jobs.record_payment(ib["job_finalizado"], lv.pago(1_000, medio=x)),
        "JobService.record_payment",
    ),
    "job.void_payment": Intento(
        "pago",
        "payments",
        lambda lv, ib, x: lv.jobs.void_payment(
            x, idempotency_key=k(), occurred_at=T, reason="Cargado dos veces"
        ),
        "JobService.void_payment",
    ),
    "job.cancel_for_delay": Intento(
        "job_turno_presente",
        "jobs",
        lambda lv, ib, x: lv.jobs.cancel_for_delay(
            x, idempotency_key=k(), occurred_at=T + timedelta(minutes=40), tolerance_min=20
        ),
        "JobService.cancel_for_delay",
    ),
    "job.reverse_retention": Intento(
        "job_cancelado_demora",
        "jobs",
        lambda lv, ib, x: lv.jobs.reverse_retention(
            x, idempotency_key=k(), occurred_at=T, reason="Avisó por WhatsApp"
        ),
        "JobService.reverse_retention",
    ),
    "job.record_inspection": Intento(
        "job_presente",
        "jobs",
        lambda lv, ib, x: lv.jobs.record_inspection(
            x, idempotency_key=k(), occurred_at=T, pre_existing_damage="rayón"
        ),
        "JobService.record_inspection",
    ),
    "job.adjust_price": Intento(
        "job_presente",
        "jobs",
        lambda lv, ib, x: lv.jobs.adjust_price(
            x,
            idempotency_key=k(),
            occurred_at=T,
            surcharge_cents=0,
            discount_cents=100_000,
            reason="Cliente frecuente",
        ),
        "JobService.adjust_price",
    ),
    "job.receive.booking": Intento(
        "booking_conf",
        "bookings",
        lambda lv, ib, x: lv.jobs.receive(idempotency_key=k(), arrived_at=T, booking_id=x),
        "JobService.receive",
    ),
    "job.receive.vehicle": Intento(
        "vehiculo",
        "vehicles",
        lambda lv, ib, x: _walk_in(lv, ib, vehicle_id=x),
        "JobService.receive",
    ),
    "job.receive.vehicle_size": Intento(
        "auto",
        "vehicle_sizes",
        lambda lv, ib, x: _walk_in(lv, ib, vehicle_size_id=x),
        "JobService.receive",
    ),
    "job.receive.service": Intento(
        "lavado", "services", lambda lv, ib, x: _walk_in(lv, ib, service_id=x), "JobService.receive"
    ),
    "job.receive.customer": Intento(
        "cliente",
        "customers",
        lambda lv, ib, x: _walk_in(lv, ib, customer_id=x),
        "JobService.receive",
    ),
    "job.receive.resource": Intento(
        "puesto",
        "resources",
        lambda lv, ib, x: _walk_in(lv, ib, resource_id=x),
        "JobService.receive",
    ),
    "job.receive.quote": Intento(
        "quote_aceptado",
        "quotes",
        # servicio A_COTIZAR de B con la cotización ACEPTADO de A
        lambda lv, ib, x: _walk_in(lv, ib, service_id=ib["tapizado"], quote_id=x),
        "JobService.receive",
    ),
    "job.receive.responsible": Intento(
        "owner",
        "users",
        lambda lv, ib, x: _walk_in(lv, ib, responsible_user_id=x),
        "JobService.receive",
    ),
    # ── BookingService ──
    "booking.get": Intento(
        "booking_conf", "bookings", lambda lv, ib, x: lv.turnos.get(x), "BookingService.get"
    ),
    "booking.confirm_deposit": Intento(
        "booking_pend_sena",
        "bookings",
        lambda lv, ib, x: lv.turnos.confirm_deposit(x, lv.pago(SENA), now=AHORA),
        "BookingService.confirm_deposit",
    ),
    "booking.confirm_deposit.medio": Intento(
        "efectivo",
        "payment_methods",
        lambda lv, ib, x: lv.turnos.confirm_deposit(
            ib["booking_pend_sena"], lv.pago(SENA, medio=x), now=AHORA
        ),
        "BookingService.confirm_deposit",
    ),
    "booking.confirm_late": Intento(
        "booking_vencido",
        "bookings",
        lambda lv, ib, x: lv.turnos.confirm_late(x, now=AHORA + timedelta(minutes=30)),
        "BookingService.confirm_late",
    ),
    "booking.cancel_by_client": Intento(
        "booking_conf",
        "bookings",
        lambda lv, ib, x: lv.turnos.cancel_by_client(x, requested_at=AHORA),
        "BookingService.cancel_by_client",
    ),
    "booking.cancel_operational": Intento(
        "booking_conf",
        "bookings",
        lambda lv, ib, x: lv.turnos.cancel_operational(x, requested_at=AHORA, reason="Lluvia"),
        "BookingService.cancel_operational",
    ),
    "booking.mark_no_show": Intento(
        "booking_conf",
        "bookings",
        lambda lv, ib, x: lv.turnos.mark_no_show(x),
        "BookingService.mark_no_show",
    ),
    "booking.create.resource": Intento(
        "puesto",
        "resources",
        lambda lv, ib, x: _turno_b(lv, ib, resource_id=x),
        "BookingService.create",
    ),
    "booking.create.customer": Intento(
        "cliente",
        "customers",
        lambda lv, ib, x: _turno_b(lv, ib, customer_id=x),
        "BookingService.create",
    ),
    "booking.create.service": Intento(
        "lavado",
        "services",
        lambda lv, ib, x: _turno_b(lv, ib, service_id=x),
        "BookingService.create",
    ),
    "booking.create.vehicle_size": Intento(
        "auto",
        "vehicle_sizes",
        lambda lv, ib, x: _turno_b(lv, ib, vehicle_size_id=x),
        "BookingService.create",
    ),
    "booking.create.vehicle": Intento(
        "vehiculo",
        "vehicles",
        lambda lv, ib, x: _turno_b(lv, ib, vehicle_id=x),
        "BookingService.create",
    ),
    "booking.add_block": Intento(
        "puesto",
        "resources",
        lambda lv, ib, x: lv.turnos.add_block(
            starts_at=T, ends_at=T + timedelta(hours=1), resource_id=x
        ),
        "BookingService.add_block",
    ),
    # ── QuoteService ──
    "quote.quote": Intento(
        "quote_pendiente",
        "quotes",
        lambda lv, ib, x: lv.cotizaciones.quote(
            x, agreed_price_cents=1, agreed_duration_min=60, quoted_at=AHORA
        ),
        "QuoteService.quote",
    ),
    "quote.accept": Intento(
        "quote_cotizado",
        "quotes",
        lambda lv, ib, x: lv.cotizaciones.accept(x, decided_at=AHORA, now=AHORA),
        "QuoteService.accept",
    ),
    "quote.reject": Intento(
        "quote_cotizado",
        "quotes",
        lambda lv, ib, x: lv.cotizaciones.reject(x, decided_at=AHORA),
        "QuoteService.reject",
    ),
    "quote.cancel": Intento(
        "quote_pendiente",
        "quotes",
        lambda lv, ib, x: lv.cotizaciones.cancel(x, decided_at=AHORA),
        "QuoteService.cancel",
    ),
    "quote.expire": Intento(
        "quote_pendiente",
        "quotes",
        lambda lv, ib, x: lv.cotizaciones.expire(x, now=AHORA + timedelta(hours=2)),
        "QuoteService.expire",
    ),
    "quote.create.customer": Intento(
        "cliente",
        "customers",
        lambda lv, ib, x: _cotizacion_b(lv, ib, customer_id=x),
        "QuoteService.create",
    ),
    "quote.create.service": Intento(
        "tapizado",
        "services",
        lambda lv, ib, x: _cotizacion_b(lv, ib, service_id=x),
        "QuoteService.create",
    ),
    "quote.create.vehicle_size": Intento(
        "auto",
        "vehicle_sizes",
        lambda lv, ib, x: _cotizacion_b(lv, ib, vehicle_size_id=x),
        "QuoteService.create",
    ),
    "quote.create.vehicle": Intento(
        "vehiculo",
        "vehicles",
        lambda lv, ib, x: _cotizacion_b(lv, ib, vehicle_id=x),
        "QuoteService.create",
    ),
    # ── DepositResolutionService ──
    "deposit.resolve": Intento(
        "cancelacion",
        "cancellations",
        lambda lv, ib, x: lv.senas.resolve(
            x, status=DepositStatus.REPROGRAMADA, reason="Pasa al sábado", resolved_at=T
        ),
        "DepositResolutionService.resolve",
    ),
    "deposit.resolve.medio": Intento(
        "efectivo",
        "payment_methods",
        lambda lv, ib, x: lv.senas.resolve(
            ib["cancelacion"],
            status=DepositStatus.DEVUELTA,
            reason="Transferido",
            resolved_at=T,
            refund=lv.pago(SENA, medio=x),
        ),
        "DepositResolutionService.resolve",
    ),
    # ── Clientes, vehículos y catálogo ──
    "vehicle.link_customer.customer": Intento(
        "cliente",
        "customers",
        lambda lv, ib, x: lv.vehiculos.link_customer(x, ib["vehiculo"]),
        "VehicleService.link_customer",
    ),
    "vehicle.link_customer.vehicle": Intento(
        "vehiculo",
        "vehicles",
        lambda lv, ib, x: lv.vehiculos.link_customer(ib["cliente"], x),
        "VehicleService.link_customer",
    ),
    "vehicle.resolve_by_plate.size": Intento(
        "auto",
        "vehicle_sizes",
        lambda lv, ib, x: lv.vehiculos.resolve_by_plate("AC 999 ZZ", vehicle_size_id=x),
        "VehicleService.resolve_by_plate",
    ),
    "catalog.set_price.service": Intento(
        "lavado",
        "services",
        lambda lv, ib, x: lv.catalogo.set_price(x, ib["auto"], price_cents=1, duration_min=60),
        "CatalogService.set_price",
    ),
    "catalog.set_price.size": Intento(
        "auto",
        "vehicle_sizes",
        lambda lv, ib, x: lv.catalogo.set_price(ib["lavado"], x, price_cents=1, duration_min=60),
        "CatalogService.set_price",
    ),
    "catalog.change_pricing_mode": Intento(
        "lavado",
        "services",
        lambda lv, ib, x: lv.catalogo.change_pricing_mode(x, PricingMode.A_COTIZAR),
        "CatalogService.change_pricing_mode",
    ),
    "catalog.check_service": Intento(
        "lavado",
        "services",
        lambda lv, ib, x: lv.catalogo.check_service(x),
        "CatalogService.check_service",
    ),
}

#: Operaciones públicas que reciben un `uuid.UUID` y **no** terminan en `NotFoundError`,
#: con dónde se cubren.
CUBIERTAS_APARTE: dict[str, str] = {
    "BookingService.expire_holds": "test_expire_holds_con_el_puesto_de_a_no_vence_nada",
}


def operaciones_con_id() -> set[str]:
    """`Servicio.metodo` público de cada servicio exportado que recibe algún `uuid.UUID`."""
    encontradas = set()
    for nombre in paquete_servicios.__all__:
        cls = getattr(paquete_servicios, nombre)
        if not (inspect.isclass(cls) and issubclass(cls, ServiceBase)):
            continue
        for metodo, fn in inspect.getmembers(cls, inspect.iscoroutinefunction):
            if metodo.startswith("_"):
                continue
            anotaciones = inspect.get_annotations(fn, eval_str=True)
            if any(
                a is uuid.UUID or (hasattr(a, "__args__") and uuid.UUID in a.__args__)
                for k, a in anotaciones.items()
                if k != "return"
            ):
                encontradas.add(f"{cls.__name__}.{metodo}")
    return encontradas


def sin_id(mensaje: str, id_: uuid.UUID) -> str:
    return mensaje.replace(str(id_), "<id>")


#: Intentos cuyo control con el tenant A no aplica: combinan una fila de B con un id de A (el
#: "propio" de A sería una fila de B) o pasan un id de catálogo en una alta.
CONTROL_NO_APLICA: frozenset[str] = frozenset(
    nombre
    for nombre, intento in INTENTOS.items()
    if nombre.endswith(".medio")
    or intento.de_a
    in {"vehiculo", "auto", "lavado", "cliente", "puesto", "owner", "tapizado", "quote_aceptado"}
)
