"""Bandeja del dueño: turnos, señas, lista de espera y cancelaciones — FASE-5-CONTRATO §4.1.

Los instantes viajan en UTC con zona (Z9); los filtros por día de `GET /bookings` son días locales
del negocio. `extra="ignore"`: el `tenant_id` de un cuerpo se descarta.
"""

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BeforeValidator, Field, model_validator

from app.application.services.booking_inbox_service import (
    BookingDetail,
    BookingRow,
    DepositCase,
    InboxCounts,
)
from app.domain.enums import (
    BookingSource,
    BookingStatus,
    CancellationClassification,
    CancellationInitiator,
    Channel,
    DepositStatus,
    PaymentKind,
    PricingMode,
)
from app.persistence.models.agenda import AGENDA_REASON_MAX, CustomerIncidentMark
from app.persistence.models.cancellation import Cancellation
from app.persistence.models.money import Payment
from app.schemas.auth import _sin_nul
from app.schemas.catalog import _Body, _Out
from app.schemas.common import CamelModel

#: Tope de un importe: lejos de cualquier seña real y del `bigint` de la base (sin 500).
MAX_AMOUNT_CENTS = 10**12
#: `CHECK (length(reason) <= 160)` de `cancellations` y de las marcas de incidencia.
REASON_MAX = AGENDA_REASON_MAX
RESOLUTION_REASON_MAX = 500


def _no_blank(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


Reason = Annotated[
    str, BeforeValidator(_sin_nul), Field(max_length=REASON_MAX), AfterValidator(_no_blank)
]
ResolutionReason = Annotated[
    str,
    BeforeValidator(_sin_nul),
    Field(max_length=RESOLUTION_REASON_MAX),
    AfterValidator(_no_blank),
]
Amount = Annotated[int, Field(gt=0, le=MAX_AMOUNT_CENTS)]


# ── Respuestas ───────────────────────────────────────────────────────────────


class CustomerBrief(CamelModel):
    id: uuid.UUID
    name: str
    #: Para escribirle por WhatsApp desde la bandeja.
    phone_e164: str | None


class BookingServiceLine(CamelModel):
    service_id: uuid.UUID
    name: str
    duration_min: int
    price_cents: int
    pricing_mode: PricingMode


class BookingSummary(CamelModel):
    id: uuid.UUID
    code: str
    status: BookingStatus
    source: BookingSource
    channel: Channel
    resource_id: uuid.UUID
    start_at: dt.datetime
    end_at: dt.datetime
    #: Vence la retención de un `PENDIENTE_SEÑA`.
    hold_expires_at: dt.datetime | None
    duration_min: int
    #: Para pedir la disponibilidad al reprogramar (`GET /agenda/availability`).
    vehicle_size_id: uuid.UUID
    price_cents: int | None
    deposit_required_cents: int
    #: Señas vivas ya cobradas.
    deposit_paid_cents: int
    customer: CustomerBrief | None
    #: La patente es PII fuerte: solo para el negocio, nunca en logs.
    vehicle_plate: str | None
    services: list[BookingServiceLine]
    notes: str | None
    created_at: dt.datetime

    @classmethod
    def of(cls, row: BookingRow) -> "BookingSummary":
        b = row.booking
        return cls(
            id=b.id,
            code=b.code,
            status=b.status,
            source=b.source,
            channel=b.channel,
            resource_id=b.resource_id,
            start_at=b.start_at,
            end_at=b.end_at,
            hold_expires_at=b.hold_expires_at,
            duration_min=b.duration_min,
            vehicle_size_id=b.vehicle_size_id,
            price_cents=b.price_cents,
            deposit_required_cents=b.deposit_required_cents,
            deposit_paid_cents=row.deposit_paid_cents,
            customer=(
                CustomerBrief(
                    id=row.customer.id, name=row.customer.name, phone_e164=row.customer.phone_e164
                )
                if row.customer is not None
                else None
            ),
            vehicle_plate=row.vehicle.plate if row.vehicle is not None else None,
            services=[
                BookingServiceLine(
                    service_id=i.service_id,
                    name=i.service_name_snapshot,
                    duration_min=i.duration_min,
                    price_cents=i.price_cents,
                    pricing_mode=i.pricing_mode,
                )
                for i in row.items
            ],
            notes=b.notes,
            created_at=b.created_at,
        )


class PaymentBrief(CamelModel):
    id: uuid.UUID
    kind: PaymentKind
    amount_cents: int
    payment_method_id: uuid.UUID
    occurred_at: dt.datetime
    voided: bool

    @classmethod
    def of(cls, p: Payment) -> "PaymentBrief":
        return cls(
            id=p.id,
            kind=p.kind,
            amount_cents=p.amount_cents,
            payment_method_id=p.payment_method_id,
            occurred_at=p.occurred_at,
            voided=p.voided_at is not None,
        )


class CancellationResponse(_Out):
    booking_id: uuid.UUID
    initiator: CancellationInitiator
    classification: CancellationClassification
    requested_at: dt.datetime
    anticipation_min: int
    reason: str
    previous_status: BookingStatus
    resulting_status: BookingStatus
    deposit_paid_cents: int
    deposit_status: DepositStatus
    resolved_at: dt.datetime | None
    resolution_reason: str | None
    refund_payment_id: uuid.UUID | None

    @classmethod
    def of(cls, c: Cancellation) -> "CancellationResponse":
        return cls.model_validate(c)


class IncidentMarkResponse(_Out):
    customer_id: uuid.UUID
    booking_id: uuid.UUID | None
    reason: str
    created_by_user_id: uuid.UUID

    @classmethod
    def of(cls, m: CustomerIncidentMark) -> "IncidentMarkResponse":
        return cls.model_validate(m)


class BookingDetailResponse(BookingSummary):
    payments: list[PaymentBrief]
    #: La cancelación o el rechazo del turno, si lo hubo.
    cancellation: CancellationResponse | None
    #: Incidencias del cliente en los últimos 90 días (D-014; con 2 o más, lista de espera).
    incidents: int
    incident_marks: list[IncidentMarkResponse]

    @classmethod
    def of_detail(cls, d: BookingDetail) -> "BookingDetailResponse":
        return cls(
            **BookingSummary.of(d.row).model_dump(),
            payments=[PaymentBrief.of(p) for p in d.payments],
            cancellation=CancellationResponse.of(d.cancellation) if d.cancellation else None,
            incidents=d.incidents,
            incident_marks=[IncidentMarkResponse.of(m) for m in d.marks],
        )


class BookingCountsResponse(CamelModel):
    pending_deposit: int
    in_review: int
    deposit_cases: int
    #: La suma: el número del menú.
    total: int

    @classmethod
    def of(cls, c: InboxCounts) -> "BookingCountsResponse":
        return cls(
            pending_deposit=c.pending_deposit,
            in_review=c.in_review,
            deposit_cases=c.deposit_cases,
            total=c.total,
        )


class ConfirmDepositResponse(CamelModel):
    booking: BookingSummary
    #: Se confirmó tarde: el hold ya había vencido y el horario seguía libre.
    late: bool


class RescheduleResponse(CamelModel):
    booking: BookingSummary
    #: El horario nuevo queda fuera del horario del negocio o de las reglas del servicio: se
    #: guardó igual (D5-10) y la pantalla lo avisa.
    outside_rules: bool


class DepositCaseResponse(CamelModel):
    cancellation: CancellationResponse
    booking: BookingSummary

    @classmethod
    def of(cls, c: DepositCase) -> "DepositCaseResponse":
        return cls(
            cancellation=CancellationResponse.of(c.cancellation), booking=BookingSummary.of(c.row)
        )


# ── Cuerpos ──────────────────────────────────────────────────────────────────


class ConfirmDepositBody(_Body):
    payment_method_id: uuid.UUID
    #: Sin importe = la seña pedida del turno.
    amount_cents: Amount | None = None
    reference: Annotated[str, BeforeValidator(_sin_nul), Field(max_length=120)] | None = None


class ReasonBody(_Body):
    reason: Reason


class RescheduleBody(_Body):
    """Horario nuevo y/o puesto nuevo (D5-10). Con zona: el front manda el instante en UTC."""

    start_at: AwareDatetime | None = None
    resource_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _algo(self) -> "RescheduleBody":
        if self.start_at is None and self.resource_id is None:
            raise ValueError("start_at or resource_id is required")
        return self


class RefundBody(_Body):
    payment_method_id: uuid.UUID
    amount_cents: Amount


class ResolveBody(_Body):
    """Cierre del caso de seña (D-005). `DEVUELTA` exige `refund` (el pago que sale de caja)."""

    status: Literal[DepositStatus.DEVUELTA, DepositStatus.RETENIDA, DepositStatus.REPROGRAMADA]
    reason: ResolutionReason
    refund: RefundBody | None = None

    @model_validator(mode="after")
    def _devolucion(self) -> "ResolveBody":
        if (self.status == DepositStatus.DEVUELTA) != (self.refund is not None):
            raise ValueError("refund is required for DEVUELTA and only for DEVUELTA")
        return self
