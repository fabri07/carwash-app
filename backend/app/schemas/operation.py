"""Agenda del día y operación del job — FASE-6-CONTRATO §3 (PR 6.1).

Los instantes viajan en UTC con zona (Z9) y `occurred_at` es **del cliente**: el momento del toque,
no el de la sincronización (la cola offline lo manda tal cual). El día de `GET /agenda/day` es el
del negocio, no el del navegador. `extra="ignore"`: el `tenant_id` de un cuerpo se descarta.
"""

import datetime as dt
import uuid
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BeforeValidator, Field, model_validator

from app.application.services.operation_service import (
    DayEntry,
    DayView,
    JobDetail,
    ManualBooking,
)
from app.domain.enums import (
    BookingStatus,
    Channel,
    DirtLevel,
    JobEntryMode,
    JobEventType,
    JobStatus,
    PricingMode,
)
from app.persistence.models.customer import Customer
from app.persistence.models.job import JobEvent, JobInspection
from app.schemas.auth import _sin_nul
from app.schemas.bookings import (
    Amount,
    BookingSummary,
    CustomerBrief,
    PaymentBrief,
    Reason,
)
from app.schemas.catalog import _Body
from app.schemas.common import CamelModel

#: Un lavadero no carga más servicios que esto en un mismo trabajo (el catálogo son decenas).
MAX_SERVICES = 20
FREE_TEXT_MAX = 1000
NAME_MAX = 80  # `CHECK (length(name) BETWEEN 1 AND 80)` de `customers`
PHONE_MAX = 40
PLATE_MAX = 20
NOTES_MAX = 500
REFERENCE_MAX = 120

ShortText = Annotated[str, BeforeValidator(_sin_nul)]
Name = Annotated[ShortText, Field(max_length=NAME_MAX)]
Phone = Annotated[ShortText, Field(max_length=PHONE_MAX)]
Plate = Annotated[ShortText, Field(max_length=PLATE_MAX)]
Notes = Annotated[ShortText, Field(max_length=NOTES_MAX)]
Reference = Annotated[ShortText, Field(max_length=REFERENCE_MAX)]
ServiceIds = Annotated[list[uuid.UUID], Field(min_length=1, max_length=MAX_SERVICES)]


#: Cuánto puede adelantarse el reloj de un celular antes de que se lo acote al del servidor.
FUTURE_SKEW = dt.timedelta(minutes=5)

# ── Cuerpos ──────────────────────────────────────────────────────────────────


class OccurredBody(_Body):
    """Toda acción del job: la hora del toque, del dispositivo.

    Un reloj del celular adelantado (más de `FUTURE_SKEW` por delante del servidor) no se rechaza:
    un 422 perdería el toque en la cola offline. Se **acota a la hora del servidor**, así no
    envenena la línea de tiempo ni mueve un ingreso a la agenda de otro día (§12.11).
    """

    occurred_at: AwareDatetime

    @model_validator(mode="after")
    def _clamp_future(self) -> Self:
        now = dt.datetime.now(dt.UTC)
        if self.occurred_at > now + FUTURE_SKEW:
            self.occurred_at = now
        return self


class CancelDelayBody(OccurredBody):
    reason: Reason | None = None


class VoidPaymentBody(OccurredBody):
    reason: Reason


class PaymentBody(OccurredBody):
    payment_method_id: uuid.UUID
    #: Sin importe = el saldo completo (cobrar en 2 toques: abrir, confirmar).
    amount_cents: Amount | None = None
    reference: Reference | None = None


class InspectionBody(OccurredBody):
    """La recepción completa (D-007): opcional; cada envío trae el formulario entero."""

    dirt_level: DirtLevel | None = None
    pre_existing_damage: Annotated[ShortText, Field(max_length=FREE_TEXT_MAX)] | None = None
    valuables: Annotated[ShortText, Field(max_length=FREE_TEXT_MAX)] | None = None
    photo_consent: bool | None = None
    checklist: (
        Annotated[dict[Annotated[str, Field(max_length=60)], bool], Field(max_length=50)] | None
    ) = None


class ReceiveBody(OccurredBody):
    """Recibir: con `booking_id` (el turno) o un ingreso sin turno.

    Sin turno: el tamaño y al menos un servicio; el auto, por `vehicle_id` o por `plate` (sin
    ninguno se crea uno anónimo); el cliente, por `customer_id` o por nombre y/o teléfono (sin
    nada, el job queda sin cliente). `channel` por defecto `CALLE`.
    """

    booking_id: uuid.UUID | None = None
    vehicle_id: uuid.UUID | None = None
    plate: Plate | None = None
    vehicle_size_id: uuid.UUID | None = None
    service_ids: Annotated[list[uuid.UUID], Field(max_length=MAX_SERVICES)] = []
    customer_id: uuid.UUID | None = None
    customer_name: Name | None = None
    customer_phone: Phone | None = None
    channel: Channel | None = None
    resource_id: uuid.UUID | None = None
    quote_id: uuid.UUID | None = None
    notes: Notes | None = None

    @model_validator(mode="after")
    def _turno_o_ingreso(self) -> Self:
        if self.booking_id is None and (self.vehicle_size_id is None or not self.service_ids):
            raise ValueError("a walk-in needs vehicle_size_id and at least one service")
        return self


class ManualDepositBody(_Body):
    payment_method_id: uuid.UUID
    amount_cents: Amount | None = None
    reference: Reference | None = None


class ManualBookingBody(_Body):
    """Un turno "solo a mano": cliente (nombre y/o teléfono; sin nada, "Sin nombre"), auto
    (patente opcional), servicios, horario, puesto, y la seña si ya la cobró."""

    start_at: AwareDatetime
    resource_id: uuid.UUID
    vehicle_size_id: uuid.UUID
    service_ids: ServiceIds
    customer_name: Name | None = None
    customer_phone: Phone | None = None
    plate: Plate | None = None
    channel: Channel | None = None
    notes: Notes | None = None
    deposit: ManualDepositBody | None = None


class RetroPaymentBody(_Body):
    payment_method_id: uuid.UUID
    #: Sin importe = el saldo completo.
    amount_cents: Amount | None = None
    reference: Reference | None = None


class RetrospectiveBody(_Body):
    """Un trabajo ya hecho (D6-3): horas reales, ambas en el pasado y `finished_at >= started_at`.

    `payment: null` = cobro pendiente (el job queda `FINALIZADO` y "debe"). El cliente puede ser
    "sin nombre" y la patente es opcional.
    """

    started_at: AwareDatetime
    finished_at: AwareDatetime
    vehicle_size_id: uuid.UUID
    service_ids: ServiceIds
    customer_name: Name | None = None
    customer_phone: Phone | None = None
    plate: Plate | None = None
    channel: Channel | None = None
    resource_id: uuid.UUID | None = None
    notes: Notes | None = None
    payment: RetroPaymentBody | None = None

    @model_validator(mode="after")
    def _orden(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must be on or after started_at")
        return self


# ── Respuestas ───────────────────────────────────────────────────────────────


class JobServiceLine(CamelModel):
    service_id: uuid.UUID
    name: str
    duration_min: int
    price_cents: int
    pricing_mode: PricingMode


class InspectionResponse(CamelModel):
    dirt_level: DirtLevel | None
    pre_existing_damage: str | None
    valuables: str | None
    photo_consent: bool | None
    checklist: dict[str, bool] | None
    inspected_at: dt.datetime | None

    @classmethod
    def of(cls, i: JobInspection) -> "InspectionResponse":
        return cls(
            dirt_level=i.dirt_level,
            pre_existing_damage=i.pre_existing_damage,
            valuables=i.valuables,
            photo_consent=i.photo_consent,
            checklist={str(k): bool(v) for k, v in (i.checklist or {}).items()}
            if i.checklist is not None
            else None,
            inspected_at=i.inspected_at,
        )


class JobResponse(CamelModel):
    id: uuid.UUID
    status: JobStatus
    entry_mode: JobEntryMode
    booking_id: uuid.UUID | None
    booking_code: str | None
    customer: CustomerBrief | None
    #: La patente es PII fuerte: solo para el negocio, nunca en logs.
    vehicle_plate: str | None
    vehicle_size_id: uuid.UUID
    resource_id: uuid.UUID | None
    channel: Channel
    scheduled_at: dt.datetime | None
    arrived_at: dt.datetime
    arrival_delay_min: int | None
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    settled_at: dt.datetime | None
    picked_up_at: dt.datetime | None
    services: list[JobServiceLine]
    base_price_cents: int
    surcharge_cents: int
    discount_cents: int
    total_cents: int
    #: Pagado neto vivo (señas y saldos, menos devoluciones).
    paid_cents: int
    #: Saldo derivado (X9): `> 0` es "debe"; negativo, saldo a favor.
    balance_due_cents: int
    notes: str | None
    tolerance_min: int
    #: Desde cuándo el servidor deja "cancelar por demora" (D6-2). Siempre botón manual.
    cancel_delay_from: dt.datetime | None
    payments: list[PaymentBrief]
    inspection: InspectionResponse | None

    @classmethod
    def of(cls, d: JobDetail) -> "JobResponse":
        j = d.job
        return cls(
            id=j.id,
            status=j.status,
            entry_mode=j.entry_mode,
            booking_id=j.booking_id,
            booking_code=d.booking_code,
            customer=_customer(d.customer),
            vehicle_plate=d.vehicle.plate if d.vehicle is not None else None,
            vehicle_size_id=j.vehicle_size_id,
            resource_id=j.resource_id,
            channel=j.channel,
            scheduled_at=j.scheduled_at,
            arrived_at=j.arrived_at,
            arrival_delay_min=j.arrival_delay_min,
            started_at=j.started_at,
            finished_at=j.finished_at,
            settled_at=j.settled_at,
            picked_up_at=j.picked_up_at,
            services=[
                JobServiceLine(
                    service_id=i.service_id,
                    name=i.service_name_snapshot,
                    duration_min=i.duration_min,
                    price_cents=i.price_cents,
                    pricing_mode=i.pricing_mode,
                )
                for i in d.items
            ],
            base_price_cents=j.base_price_cents,
            surcharge_cents=j.surcharge_cents,
            discount_cents=j.discount_cents,
            total_cents=d.total_cents,
            paid_cents=d.paid_cents,
            balance_due_cents=d.balance_due_cents,
            notes=j.notes,
            tolerance_min=d.tolerance_min,
            cancel_delay_from=d.cancel_delay_from,
            payments=[PaymentBrief.of(p) for p in d.payments],
            inspection=InspectionResponse.of(d.inspection) if d.inspection is not None else None,
        )


class JobEventResponse(CamelModel):
    id: uuid.UUID
    event_type: JobEventType
    from_status: JobStatus | None
    to_status: JobStatus | None
    #: Del cliente: la hora del toque (o la real que se cargó a mano).
    occurred_at: dt.datetime
    #: Del servidor: cuándo llegó.
    created_at: dt.datetime
    actor_user_id: uuid.UUID
    #: `reason`, `delay_min`, `amount_cents`, `payment_id`, `manual_entry`, …
    metadata: dict[str, object]

    @classmethod
    def of(cls, e: JobEvent) -> "JobEventResponse":
        return cls(
            id=e.id,
            event_type=e.event_type,
            from_status=e.from_status,
            to_status=e.to_status,
            occurred_at=e.occurred_at,
            created_at=e.created_at,
            actor_user_id=e.actor_user_id,
            metadata=dict(e.event_metadata),
        )


def _customer(c: Customer | None) -> CustomerBrief | None:
    if c is None:
        return None
    return CustomerBrief(id=c.id, name=c.name, phone_e164=c.phone_e164)


class DayResource(CamelModel):
    id: uuid.UUID
    name: str


class DayEntryResponse(CamelModel):
    """Un renglón de la agenda: `kind` dice si es un turno todavía sin job o un job."""

    kind: Literal["BOOKING", "JOB"]
    #: El id del turno (`kind=BOOKING`) o del job (`kind=JOB`).
    id: uuid.UUID
    booking_id: uuid.UUID | None
    job_id: uuid.UUID | None
    code: str | None
    #: Estado del turno (`BOOKING`) o del job (`JOB`): solo uno de los dos viene.
    booking_status: BookingStatus | None
    job_status: JobStatus | None
    entry_mode: JobEntryMode | None
    resource_id: uuid.UUID | None
    start_at: dt.datetime
    end_at: dt.datetime
    customer: CustomerBrief | None
    #: La patente es PII fuerte: solo para el negocio, nunca en logs.
    vehicle_plate: str | None
    services: list[str]
    total_cents: int | None
    paid_cents: int
    #: `> 0` es "debe". `None` si el turno todavía no tiene precio.
    balance_due_cents: int | None
    cancel_delay_from: dt.datetime | None

    @classmethod
    def of(cls, e: DayEntry) -> "DayEntryResponse":
        b, j = e.booking, e.job
        return cls(
            kind=e.kind,
            id=e.entry_id_uuid,
            booking_id=b.id if b is not None else (j.booking_id if j is not None else None),
            job_id=j.id if j is not None else None,
            code=b.code if b is not None else None,
            booking_status=b.status if b is not None else None,
            job_status=j.status if j is not None else None,
            entry_mode=j.entry_mode if j is not None else None,
            resource_id=e.resource_id,
            start_at=e.start_at,
            end_at=e.end_at,
            customer=_customer(e.customer),
            vehicle_plate=e.vehicle.plate if e.vehicle is not None else None,
            services=e.service_names,
            total_cents=e.total_cents,
            paid_cents=e.paid_cents,
            balance_due_cents=e.balance_due_cents,
            cancel_delay_from=e.cancel_delay_from,
        )


class DayResponse(CamelModel):
    """La agenda de un día del negocio: una columna por puesto (D6-1). Los renglones sin puesto
    (`resource_id` nulo: un ingreso sin puesto asignado) van en la columna "sin puesto"."""

    date: dt.date
    timezone: str
    tolerance_min: int
    resources: list[DayResource]
    entries: list[DayEntryResponse]
    #: Hubo más de `DAY_CAP` renglones y se recortó (no pasa en un lavadero real).
    truncated: bool

    @classmethod
    def of(cls, v: DayView) -> "DayResponse":
        return cls(
            date=v.day,
            timezone=v.timezone,
            tolerance_min=v.tolerance_min,
            resources=[DayResource(id=r.id, name=r.name) for r in v.resources],
            entries=[DayEntryResponse.of(e) for e in v.entries],
            truncated=v.truncated,
        )


class ManualBookingResponse(CamelModel):
    booking: BookingSummary
    deposit_paid_cents: int

    @classmethod
    def of(cls, m: ManualBooking, summary: BookingSummary) -> "ManualBookingResponse":
        return cls(booking=summary, deposit_paid_cents=m.deposit_paid_cents)
