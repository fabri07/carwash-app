"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";

import { apiErrorCode, genericMessage } from "@/lib/errors";
import {
  getBookingCounts,
  listBookings,
  listDepositCases,
  type BookingFilters,
} from "@/services/bookings.service";
import type { BookingStatusCode, DepositStatusCode } from "@/types/api";

/** Todo cuelga de `["bookings"]`: una acción invalida la bandeja entera y el contador. */
export const turnosKeys = {
  all: ["bookings"] as const,
  counts: ["bookings", "counts"] as const,
  list: (filters: BookingFilters) => ["bookings", "list", filters] as const,
  cases: ["bookings", "cases"] as const,
};

/** Cada cuánto se refresca el contador del menú (además de al volver a la pestaña). */
export const COUNTS_REFRESH_MS = 60_000;

/** Tope del backend: una página de 200 alcanza para lo que espera decisión. */
export const PAGE = { limit: 200, offset: 0 };

/**
 * Pendientes de seña + lista de espera + señas a resolver (§4.1). Se refresca cada minuto y al
 * volver a la pestaña del navegador (`refetchOnWindowFocus`), aunque la pantalla no cambie.
 */
export function useInboxCounts(enabled = true) {
  return useQuery({
    queryKey: turnosKeys.counts,
    queryFn: getBookingCounts,
    enabled,
    refetchInterval: COUNTS_REFRESH_MS,
    refetchOnWindowFocus: "always",
    staleTime: 0,
  });
}

export function useBookings(filters: BookingFilters) {
  return useQuery({
    queryKey: turnosKeys.list(filters),
    queryFn: () => listBookings(filters),
    refetchOnWindowFocus: "always",
  });
}

export function useDepositCases() {
  return useQuery({
    queryKey: turnosKeys.cases,
    queryFn: () => listDepositCases(PAGE),
    refetchOnWindowFocus: "always",
  });
}

/** Después de una acción: la bandeja, el contador y la agenda se vuelven a pedir. */
export function useRefreshInbox() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: turnosKeys.all });
}

export const STATUS_LABELS: Record<BookingStatusCode, string> = {
  PENDIENTE_SEÑA: "Pendiente de seña",
  PENDIENTE_COTIZACION: "A cotizar",
  CONFIRMADO: "Confirmado",
  RECIBIDO: "Recibido",
  ATENDIDO: "Atendido",
  VENCIDO: "Seña vencida",
  CANCELADO_CLIENTE: "Canceló el cliente",
  AUSENTE_CON_AVISO_POSTERIOR: "Avisó después de la hora",
  CANCELADO_OPERATIVO: "Cancelado por el negocio",
  CANCELADO_DEMORA: "Cancelado por demora",
  NO_ASISTIO: "No asistió",
  PENDIENTE_REVISION: "Lista de espera",
  RECHAZADO: "Rechazado",
};

export const DEPOSIT_STATUS_LABELS: Record<DepositStatusCode, string> = {
  SIN_PAGO: "Sin seña",
  DEVOLUCION_PENDIENTE: "Devolución pendiente",
  EN_REVISION: "Canceló después de la hora: decidí",
  REPROGRAMACION_O_DEVOLUCION_PENDIENTE: "Reprogramar o devolver",
  DEVUELTA: "Devuelta",
  RETENIDA: "Retenida",
  REPROGRAMADA: "Reprogramada",
};

/** Cada acción de la bandeja: da la clave de idempotencia y el texto de sus errores. */
export type ActionName =
  "cancel" | "reject" | "incident" | "noShow" | "approve" | "reschedule" | "deposit" | "resolve";

/** Qué significa un 422 (`VALIDATION_ERROR`) en cada acción: el backend manda texto de sistema. */
const VALIDATION: Record<ActionName, string> = {
  cancel: "Escribí el motivo de la cancelación (hasta 160 caracteres).",
  reject: "Escribí el motivo del rechazo (hasta 160 caracteres).",
  incident: "Escribí qué pasó (hasta 160 caracteres).",
  noShow: "El turno todavía no empezó: la inasistencia se marca después de la hora del turno.",
  approve:
    "No se puede aprobar: el horario del pedido ya pasó o incluye un servicio a cotizar (esos se agendan a mano). Rechazalo o reprogramalo.",
  reschedule: "El horario nuevo tiene que ser futuro.",
  deposit:
    "Revisá la seña: el importe tiene que ser mayor a cero y el medio de pago tiene que aceptar cobros.",
  resolve:
    "Revisá el cierre: el motivo es obligatorio y una devolución no puede superar la seña cobrada.",
};

/** Qué significa un 409 `CONFLICT` en cada acción (el estado del turno cambió). */
const CONFLICT: Partial<Record<ActionName, string>> = {
  deposit:
    "Este turno ya no espera una seña (se confirmó, se canceló o no pedía seña). Recargá la bandeja.",
  noShow: "Solo se marca la inasistencia de un turno confirmado. Recargá la bandeja.",
  approve: "Ese pedido ya no está en la lista de espera. Recargá la bandeja.",
  resolve:
    "Esa seña no se puede cerrar así: retener solo vale si el cliente canceló después de la hora.",
};

/**
 * Errores de la bandeja en castellano, por código y por acción. Los 409 del contrato tienen su
 * texto; el resto, el genérico por estado. `DUPLICATE_IDEMPOTENT` no llega acá: es éxito (ver
 * `runAction`).
 */
export function inboxErrorMessage(e: unknown, action?: ActionName): string {
  switch (apiErrorCode(e)) {
    case "SLOT_TAKEN":
      return "Ese horario ya no está libre en ese puesto. Elegí otro horario u otro puesto.";
    case "BLOCKED":
      return "Hay un bloqueo de agenda en ese horario. Quitá el bloqueo o elegí otro horario.";
    case "CONFLICT":
      return (
        (action && CONFLICT[action]) ??
        "El turno cambió mientras tanto y esa acción ya no corresponde. Recargá la bandeja."
      );
    case "VALIDATION_ERROR":
      return action ? VALIDATION[action] : "Revisá los datos ingresados.";
    default:
      return genericMessage(e);
  }
}

/** Corre una acción: un reenvío con la misma clave (`DUPLICATE_IDEMPOTENT`) cuenta como hecho. */
export async function runAction(action: () => Promise<unknown>, name?: ActionName): Promise<void> {
  try {
    await action();
  } catch (e) {
    if (apiErrorCode(e) === "DUPLICATE_IDEMPOTENT") return;
    throw new Error(inboxErrorMessage(e, name));
  }
}
