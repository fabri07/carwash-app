"""Vocabularios del dominio — FASE-3-CONTRATO §1.6 y §2.

Congelados por T1 para que modelos (Esquema) y máquinas de estado (Dominio) se
construyan en paralelo contra los mismos nombres. Cada enum lleva el nombre de su
tipo nativo en Postgres (`*_ENUM`), compartido por modelo y migración como `role`
(ADR-0005). Agregar un valor es una migración, no un literal.

Los valores con `Ñ` (`PENDIENTE_SEÑA`, `SEÑA`) son los del legacy tal cual: el
identificador Python va sin tilde, el valor persistido no.
"""

from enum import StrEnum

PRICING_MODE_ENUM = "pricing_mode"
CHANNEL_ENUM = "channel"
PLATE_FORMAT_ENUM = "plate_format"
BOOKING_SOURCE_ENUM = "booking_source"
BOOKING_STATUS_ENUM = "booking_status"
JOB_STATUS_ENUM = "job_status"
JOB_EVENT_TYPE_ENUM = "job_event_type"
DIRT_LEVEL_ENUM = "dirt_level"
QUOTE_STATUS_ENUM = "quote_status"
SUPPLIES_PURCHASER_ENUM = "supplies_purchaser"
CANCELLATION_INITIATOR_ENUM = "cancellation_initiator"
CANCELLATION_CLASSIFICATION_ENUM = "cancellation_classification"
DEPOSIT_STATUS_ENUM = "deposit_status"
PAYMENT_KIND_ENUM = "payment_kind"
CASH_DIRECTION_ENUM = "cash_direction"
CASH_MOVEMENT_KIND_ENUM = "cash_movement_kind"


class PricingMode(StrEnum):
    """D-006. Explícita: borrar el precio no vuelve "a cotizar" un servicio (R-T-002)."""

    PRECIO_FIJO = "PRECIO_FIJO"
    A_COTIZAR = "A_COTIZAR"


class Channel(StrEnum):
    """D-004 — de dónde vino el cliente. El mapeo desde los 5 vocabularios legacy es de F8."""

    TURNERO_WEB = "TURNERO_WEB"
    WHATSAPP = "WHATSAPP"
    INSTAGRAM = "INSTAGRAM"
    REFERIDO = "REFERIDO"
    CALLE = "CALLE"
    CLIENTE_ANTERIOR = "CLIENTE_ANTERIOR"
    CARGA_MANUAL = "CARGA_MANUAL"
    OTRO = "OTRO"


class PlateFormat(StrEnum):
    AR_1994 = "AR_1994"  # AAA000
    MERCOSUR = "MERCOSUR"  # AA000AA
    OTRO = "OTRO"  # se acepta con advertencia: no bloquea la recepción


class BookingSource(StrEnum):
    """Quién cargó el turno. Distinto del canal (R-C-013: `AGENDA_INTERNA_LJ` no es un canal)."""

    TURNERO_WEB = "TURNERO_WEB"
    PANEL = "PANEL"
    AGENDA_INTERNA = "AGENDA_INTERNA"
    LISTA_ESPERA = "LISTA_ESPERA"


class BookingStatus(StrEnum):
    """§2.1. Los cuatro primeros bloquean el intervalo del puesto."""

    PENDIENTE_SENA = "PENDIENTE_SEÑA"
    PENDIENTE_COTIZACION = "PENDIENTE_COTIZACION"
    CONFIRMADO = "CONFIRMADO"
    RECIBIDO = "RECIBIDO"
    ATENDIDO = "ATENDIDO"
    VENCIDO = "VENCIDO"
    CANCELADO_CLIENTE = "CANCELADO_CLIENTE"
    AUSENTE_CON_AVISO_POSTERIOR = "AUSENTE_CON_AVISO_POSTERIOR"
    CANCELADO_OPERATIVO = "CANCELADO_OPERATIVO"
    CANCELADO_DEMORA = "CANCELADO_DEMORA"
    NO_ASISTIO = "NO_ASISTIO"


class JobStatus(StrEnum):
    """§2.2. `jobs.status` es caché del último `job_events.to_status`."""

    PRESENTE = "PRESENTE"
    EN_PROCESO = "EN_PROCESO"
    FINALIZADO = "FINALIZADO"
    COBRADO = "COBRADO"
    RETIRADO = "RETIRADO"
    CANCELADO_DEMORA = "CANCELADO_DEMORA"


class JobEventType(StrEnum):
    JOB_RECEIVED = "JOB_RECEIVED"
    JOB_STARTED = "JOB_STARTED"
    JOB_FINISHED = "JOB_FINISHED"
    JOB_SETTLED = "JOB_SETTLED"
    JOB_PICKED_UP = "JOB_PICKED_UP"
    JOB_CANCELLED_DELAY = "JOB_CANCELLED_DELAY"
    DEPOSIT_RETAINED = "DEPOSIT_RETAINED"
    DEPOSIT_RETENTION_REVERSED = "DEPOSIT_RETENTION_REVERSED"
    PAYMENT_RECORDED = "PAYMENT_RECORDED"
    PAYMENT_VOIDED = "PAYMENT_VOIDED"
    INSPECTION_RECORDED = "INSPECTION_RECORDED"
    PRICE_ADJUSTED = "PRICE_ADJUSTED"


class DirtLevel(StrEnum):
    """Los 6 niveles de `RECEPCION!B15` / `ADMIN_DIRT_LEVELS`. Los 12 valores libres de
    `REGISTRO_DIARIO.O` se mapean en la migración de F8."""

    NORMAL = "NORMAL"
    INTENSA = "INTENSA"
    BARRO = "BARRO"
    ARENA = "ARENA"
    PELOS_DE_MASCOTA = "PELOS_DE_MASCOTA"
    TRABAJO_ESPECIAL = "TRABAJO_ESPECIAL"


class QuoteStatus(StrEnum):
    """§2.3 — diseñado de cero (R-C-027: el legacy es insert-only). `VENCIDO` es nuevo."""

    PENDIENTE = "PENDIENTE"
    COTIZADO = "COTIZADO"
    ACEPTADO = "ACEPTADO"
    RECHAZADO = "RECHAZADO"
    CANCELADO = "CANCELADO"
    VENCIDO = "VENCIDO"


class SuppliesPurchaser(StrEnum):
    """`Compra_insumos`. `SOLA CLEANCARS` del legacy pasa a `NEGOCIO` (multi-tenant)."""

    A_DEFINIR = "A_DEFINIR"
    CLIENTE = "CLIENTE"
    NEGOCIO = "NEGOCIO"
    NO_APLICA = "NO_APLICA"


class CancellationInitiator(StrEnum):
    CLIENTE = "CLIENTE"
    NEGOCIO = "NEGOCIO"


class CancellationClassification(StrEnum):
    """Valores exactos del código (R-T-037, C-06), no los del inventario."""

    NORMAL = "NORMAL"
    TARDIA = "TARDIA"
    POSTERIOR_AL_TURNO = "POSTERIOR_AL_TURNO"
    OPERATIVA = "OPERATIVA"


class DepositStatus(StrEnum):
    """§2.4. Los cuatro primeros abren un caso (legacy); los tres últimos lo cierran (nuevos)."""

    SIN_PAGO = "SIN_PAGO"
    DEVOLUCION_PENDIENTE = "DEVOLUCION_PENDIENTE"
    EN_REVISION = "EN_REVISION"
    REPROGRAMACION_O_DEVOLUCION_PENDIENTE = "REPROGRAMACION_O_DEVOLUCION_PENDIENTE"
    DEVUELTA = "DEVUELTA"
    RETENIDA = "RETENIDA"
    REPROGRAMADA = "REPROGRAMADA"


class PaymentKind(StrEnum):
    SENA = "SEÑA"
    SALDO = "SALDO"
    DEVOLUCION = "DEVOLUCION"  # resta


class CashDirection(StrEnum):
    ENTRADA = "ENTRADA"
    SALIDA = "SALIDA"


class CashMovementKind(StrEnum):
    """Extensible: F7B agrega gastos, retiros y pagos a empleados por migración."""

    COBRO = "COBRO"
    DEVOLUCION = "DEVOLUCION"
    AJUSTE = "AJUSTE"
