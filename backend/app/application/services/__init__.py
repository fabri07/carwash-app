"""Servicios de aplicación — FASE-3-CONTRATO §4.

Los casos de uso del dominio, sin HTTP (X2): cada endpoint llega en su fase (F4–F7). Todos se
construyen con `(session, tenant_id, actor_user_id)`, hacen `flush` (nunca `commit`) dentro de
la transacción del llamador y levantan excepciones del dominio o de `errors.py`.
"""

from app.application.services._payments import PaymentInput
from app.application.services.agenda_service import (
    AgendaService,
    Availability,
    BlockCreated,
    LocalRange,
)
from app.application.services.booking_inbox_service import BookingInboxService
from app.application.services.booking_service import (
    BookingService,
    DepositConfirmation,
    InboxDeposit,
    Rescheduled,
)
from app.application.services.catalog_service import CatalogService, check_price
from app.application.services.customer_service import (
    CustomerResolution,
    CustomerService,
    VehicleResolution,
    VehicleService,
)
from app.application.services.deposit_resolution_service import DepositResolutionService
from app.application.services.job_service import JobService
from app.application.services.quote_service import QuoteService

__all__ = [
    "AgendaService",
    "Availability",
    "BlockCreated",
    "LocalRange",
    "BookingInboxService",
    "BookingService",
    "CatalogService",
    "CustomerResolution",
    "CustomerService",
    "DepositConfirmation",
    "DepositResolutionService",
    "InboxDeposit",
    "JobService",
    "PaymentInput",
    "QuoteService",
    "Rescheduled",
    "VehicleResolution",
    "VehicleService",
    "check_price",
]
