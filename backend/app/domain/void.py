"""Motivos de anulación — ADR-0003.

Un solo mecanismo de soft-delete (`voided_at` + `void_reason`), con la semántica
adentro del motivo: dar de baja (`DESACTIVADO`) no es lo mismo que "nunca debió
existir" (`ERROR_DE_CARGA`). Crece solo por migración.
"""

from enum import StrEnum

VOID_REASON_ENUM_NAME = "void_reason"


class VoidReason(StrEnum):
    ERROR_DE_CARGA = "ERROR_DE_CARGA"
    PEDIDO_DEL_USUARIO = "PEDIDO_DEL_USUARIO"
    DESACTIVADO = "DESACTIVADO"
