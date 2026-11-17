import axios from "axios";

import { apiErrorCode, genericMessage } from "@/lib/errors";

/** Acción operativa a la que pertenece un error: define qué significa un 422 en cada una. */
export type OperacionAction =
  | "receive"
  | "start"
  | "finish"
  | "pickUp"
  | "cancelDelay"
  | "pay"
  | "void"
  | "inspection"
  | "retrospective"
  | "manualBooking";

/** Qué significa un 422 (`VALIDATION_ERROR`): el backend manda texto de sistema, en inglés. */
const VALIDATION: Record<OperacionAction, string> = {
  receive:
    "No se pudo registrar la llegada: revisá el servicio, el tamaño del vehículo y el puesto.",
  start: "No se pudo iniciar el trabajo.",
  finish: "No se pudo finalizar el trabajo.",
  pickUp:
    "No se puede retirar con deuda a un cliente nuevo: cobrá el saldo primero (o cobralo y retiralo).",
  cancelDelay:
    "Todavía no se puede cancelar por demora: la demora tiene que superar la tolerancia del negocio.",
  pay: "Revisá el cobro: el medio de pago tiene que aceptar cobros y el importe no puede superar el saldo.",
  void: "Escribí el motivo de la anulación.",
  inspection: "Revisá los datos de la recepción.",
  retrospective:
    "Revisá la carga: el fin no puede ser anterior al inicio, ninguno puede ser a futuro, el importe cobrado no puede superar el total y no se cargan servicios a cotizar.",
  manualBooking:
    "Revisá el turno: tiene que ser a futuro, el servicio no puede ser a cotizar y la seña no puede superar el precio.",
};

/**
 * Error de una acción operativa en castellano (contrato §12.3). Un 409 `INVALID_TRANSITION` o
 * `CONFLICT` no pierde nada: se vuelve a leer el estado y se avisa. `DUPLICATE_IDEMPOTENT` no llega
 * acá: la cola offline lo cuenta como éxito.
 */
export function operacionErrorMessage(e: unknown, action: OperacionAction): string {
  if (
    axios.isAxiosError(e) &&
    e.response?.status === 403 &&
    (action === "retrospective" || action === "manualBooking")
  ) {
    return "No tenés permiso para cargar cobros ni señas: cargalo como pendiente, o pedile el permiso al dueño.";
  }
  switch (apiErrorCode(e)) {
    case "INVALID_TRANSITION":
      return "Ese paso ya no corresponde: el trabajo cambió de estado (otro dispositivo o ya estaba hecho). Se actualizó la pantalla; no se perdió nada.";
    case "SLOT_TAKEN":
      return "Ese horario ya no está libre en ese puesto. Elegí otro horario u otro puesto.";
    case "INVALID_PHONE":
      return "El teléfono no es válido. Escribilo con característica, por ejemplo +54 9 351 000-0000, o dejalo vacío.";
    case "BLOCKED":
      return "Hay un bloqueo de agenda en ese horario. Quitá el bloqueo o elegí otro horario.";
    case "CONFLICT":
      return "Esa operación ya se había registrado con otra acción. Recargá la pantalla.";
    case "VALIDATION_ERROR":
      return VALIDATION[action];
    default:
      return genericMessage(e);
  }
}
