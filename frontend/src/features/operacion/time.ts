import { localDate, localTime } from "@/lib/agenda";

/** "23/11 10:05": día y hora de un instante en la zona del negocio (línea de tiempo y cobros). */
export function formatStamp(iso: string, timeZone: string): string {
  const day = localDate(new Date(iso), timeZone); // YYYY-MM-DD
  return `${day.slice(8, 10)}/${day.slice(5, 7)} ${localTime(iso, timeZone)}`;
}

/** Hora del dispositivo en el momento del toque (`occurred_at`): se llama primero en el manejador. */
export function tapTime(): string {
  return new Date().toISOString();
}
