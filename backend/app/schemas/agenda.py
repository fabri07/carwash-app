"""Agenda: bloqueos, aperturas especiales y disponibilidad — FASE-5-CONTRATO §4.1 (PR 5.1).

Bloqueos y aperturas se cargan en **hora local del negocio** (Z9): día (y opcionalmente día final)
más horas opcionales; sin horas es el día completo. La respuesta devuelve los instantes en UTC con
zona; el front los muestra en la zona del negocio. `extra="ignore"`: el `tenant_id` de un cuerpo se
descarta.
"""

import datetime as dt
import uuid
from typing import Annotated

from pydantic import BeforeValidator, Field, field_validator, model_validator

from app.application.services.agenda_service import LocalRange
from app.domain.enums import BookingStatus
from app.persistence.models.agenda import AGENDA_REASON_MAX
from app.schemas.auth import _sin_nul
from app.schemas.catalog import _Body, _naive_time, _Out
from app.schemas.common import CamelModel

Reason = Annotated[str, BeforeValidator(_sin_nul), Field(max_length=AGENDA_REASON_MAX)]


class LocalRangeBody(_Body):
    """`date` (y `end_date`, inclusive) más `start_time`/`end_time` opcionales, hora local."""

    date: dt.date
    end_date: dt.date | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    #: `null` = todos los puestos.
    resource_id: uuid.UUID | None = None
    reason: Reason | None = None

    _sin_zona = field_validator("start_time", "end_time")(_naive_time)

    @model_validator(mode="after")
    def _orden(self) -> "LocalRangeBody":
        if self.end_date is not None and self.end_date < self.date:
            raise ValueError("end_date must be on or after date")
        return self

    def to_range(self) -> LocalRange:
        return LocalRange(self.date, self.end_date, self.start_time, self.end_time)


class BlockCreate(LocalRangeBody):
    pass


class OpeningCreate(LocalRangeBody):
    pass


class BlockResponse(_Out):
    resource_id: uuid.UUID | None
    starts_at: dt.datetime
    ends_at: dt.datetime
    reason: str
    created_by_user_id: uuid.UUID


class OpeningResponse(BlockResponse):
    pass


class OverlappingBooking(CamelModel):
    """Un turno activo que quedó dentro de un bloqueo nuevo: hay que avisarle al cliente."""

    id: uuid.UUID
    code: str
    status: BookingStatus
    resource_id: uuid.UUID
    start_at: dt.datetime
    end_at: dt.datetime
    customer_name: str


class BlockCreatedResponse(CamelModel):
    block: BlockResponse
    #: Por inicio. El bloqueo **no** los cancela (R-T-052).
    overlapping_bookings: list[OverlappingBooking]


class AvailableStart(CamelModel):
    start_at: dt.datetime
    end_at: dt.datetime
    #: Puestos libres en ese inicio, en `sort_order` (D5-6: el primero es el que se asignaría).
    resource_ids: list[uuid.UUID]


class AvailabilityResponse(CamelModel):
    date: dt.date
    #: Zona del negocio (Z9): el front muestra los inicios en esta zona.
    timezone: str
    duration_min: int
    slots: list[AvailableStart]
