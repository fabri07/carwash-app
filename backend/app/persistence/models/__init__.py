"""Importa todos los modelos para que `Base.metadata` (y el autogenerate) los vea."""

from app.persistence.models.agenda import Booking, BookingItem, ScheduleBlock
from app.persistence.models.cancellation import Cancellation
from app.persistence.models.catalog import (
    BusinessHours,
    PaymentMethod,
    Resource,
    Service,
    ServicePrice,
    VehicleSize,
)
from app.persistence.models.customer import Customer, CustomerVehicle, Vehicle
from app.persistence.models.dummy_resource import DummyResource
from app.persistence.models.idempotency_key import IdempotencyKey
from app.persistence.models.job import Job, JobEvent, JobInspection, JobItem
from app.persistence.models.money import CashMovement, Payment
from app.persistence.models.quote import Quote
from app.persistence.models.tenant import Tenant
from app.persistence.models.user import User

__all__ = [
    "Booking",
    "BookingItem",
    "BusinessHours",
    "Cancellation",
    "CashMovement",
    "Customer",
    "CustomerVehicle",
    "DummyResource",
    "IdempotencyKey",
    "Job",
    "JobEvent",
    "JobInspection",
    "JobItem",
    "Payment",
    "PaymentMethod",
    "Quote",
    "Resource",
    "ScheduleBlock",
    "Service",
    "ServicePrice",
    "Tenant",
    "User",
    "Vehicle",
    "VehicleSize",
]
