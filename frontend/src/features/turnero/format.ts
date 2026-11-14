import axios from "axios";

import { apiErrorCode } from "@/lib/errors";
import { formatARS } from "@/lib/money";
import type { TurneroBooking, TurneroService } from "@/types/api";

/**
 * Piezas del turnero público (PR 5.2) sin React: fechas en la zona del negocio (Z9), textos de
 * estado, el link de WhatsApp y los mensajes de error por código del contrato.
 */

/** Los días reservables desde hoy (en la zona del negocio) hasta `hoy + horizonte`, inclusive. */
export function bookableDays(now: Date, timeZone: string, horizonDays: number): string[] {
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
  const [y, m, d] = today.split("-").map(Number) as [number, number, number];
  return Array.from({ length: horizonDays + 1 }, (_, i) =>
    new Date(Date.UTC(y, m - 1, d + i)).toISOString().slice(0, 10),
  );
}

/** `"2026-11-23"` → `"lun 23/11"`. Es una fecha de calendario: no se convierte de zona. */
export function dayLabel(isoDate: string): string {
  const [y, m, d] = isoDate.split("-").map(Number) as [number, number, number];
  const weekday = new Intl.DateTimeFormat("es-AR", { weekday: "short", timeZone: "UTC" })
    .format(new Date(Date.UTC(y, m - 1, d)))
    .replace(".", "");
  return `${weekday} ${String(d).padStart(2, "0")}/${String(m).padStart(2, "0")}`;
}

/** Instante → `"lun 23/11 · 10:00"` en la zona del negocio. */
export function whenLabel(iso: string, timeZone: string): string {
  const get = (opts: Intl.DateTimeFormatOptions) =>
    Object.fromEntries(
      new Intl.DateTimeFormat("es-AR", { timeZone, ...opts })
        .formatToParts(new Date(iso))
        .map((p) => [p.type, p.value]),
    );
  const day = get({ weekday: "short", day: "2-digit", month: "2-digit" });
  const clock = get({ hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  return `${String(day.weekday).replace(".", "")} ${day.day}/${day.month} · ${clock.hour}:${clock.minute}`;
}

/** Precio y duración de un servicio para el tamaño elegido; `undefined` si no tiene. */
export function priceFor(service: TurneroService, sizeId: string) {
  return service.prices.find((p) => p.vehicle_size_id === sizeId);
}

/** `"$ 20.000,00"`, o `"desde $ 15.000,00"` para un servicio a cotizar (D5-5). */
export function priceLabel(service: TurneroService, sizeId: string): string {
  const price = priceFor(service, sizeId);
  if (!price) return "";
  const amount = formatARS(price.price_cents);
  return service.bookable ? amount : `desde ${amount}`;
}

export const STATUS_LABELS: Record<string, string> = {
  PENDIENTE_SEÑA: "Pendiente de seña",
  CONFIRMADO: "Confirmado",
  PENDIENTE_REVISION: "En revisión",
  CANCELADO_CLIENTE: "Cancelado",
  AUSENTE_CON_AVISO_POSTERIOR: "Cancelado",
  CANCELADO_OPERATIVO: "Cancelado por el negocio",
  VENCIDO: "Vencido",
  RECHAZADO: "No aceptado",
  RECIBIDO: "En el lavadero",
  ATENDIDO: "Atendido",
  NO_ASISTIO: "No asistió",
  CANCELADO_DEMORA: "Cancelado",
  PENDIENTE_COTIZACION: "A cotizar",
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

/** `https://wa.me/5493510009999?text=…`. `null` si el negocio no cargó WhatsApp. */
export function whatsappLink(e164: string | null | undefined, text: string): string | null {
  const digits = (e164 ?? "").replace(/\D/g, "");
  if (!digits) return null;
  return `https://wa.me/${digits}?text=${encodeURIComponent(text)}`;
}

/** Texto precargado para mandar el comprobante de la seña (D5-1). */
export function depositMessage(booking: TurneroBooking): string {
  return [
    `Hola! Reservé el turno ${booking.code} para el ${whenLabel(booking.start_at, booking.timezone)}.`,
    booking.deposit_cents > 0
      ? `Te mando el comprobante de la seña de ${formatARS(booking.deposit_cents)}.`
      : "Quería confirmarlo.",
  ].join(" ");
}

/** Consulta por un servicio a cotizar (D5-5). */
export function quoteMessage(serviceName: string, sizeLabel: string): string {
  return `Hola! Quería consultar el precio de ${serviceName} para ${sizeLabel}.`;
}

const BOOKING_ERRORS: Record<string, string> = {
  SLOT_TAKEN: "Ese horario se acaba de ocupar. Elegí otro.",
  TOO_MANY_ACTIVE_BOOKINGS:
    "Ya tenés dos turnos pedidos con este WhatsApp. Para otro, escribile al negocio.",
  SERVICE_NOT_BOOKABLE: "Uno de los servicios ya no se puede reservar online. Volvé a elegir.",
  INVALID_PHONE: "Revisá el WhatsApp: tiene que ser un celular argentino con código de área.",
  RATE_LIMITED: "Hiciste demasiados intentos seguidos. Esperá un minuto y probá de nuevo.",
  NOT_FOUND: "No encontramos un turno con ese código y ese WhatsApp.",
  CONFLICT: "Este turno ya no se puede cancelar desde la web. Escribile al negocio.",
};

/** Mensaje para el cliente por código del contrato; si no hay, uno genérico por estado. */
export function turneroErrorMessage(e: unknown): string {
  const generic = "Algo salió mal. Probá de nuevo en unos minutos.";
  if (!axios.isAxiosError(e)) return generic;
  if (!e.response) return "No hay conexión. Revisá tu internet y probá de nuevo.";
  const code = apiErrorCode(e);
  if (code && BOOKING_ERRORS[code]) return BOOKING_ERRORS[code]!;
  if (e.response.status === 429) return BOOKING_ERRORS.RATE_LIMITED!;
  if (e.response.status === 404) return BOOKING_ERRORS.NOT_FOUND!;
  return generic;
}
