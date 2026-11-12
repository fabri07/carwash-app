/**
 * Fechas de la agenda en la zona del negocio (FASE-5-CONTRATO Z9): el front muestra la
 * hora del lavadero, no la del navegador. Los instantes llegan en UTC (ISO 8601).
 */

function parts(iso: string | Date, timeZone: string) {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  // Armado a mano: el separador de `es-AR` cambia según la versión de ICU del entorno.
  const get = (opts: Intl.DateTimeFormatOptions) =>
    Object.fromEntries(
      new Intl.DateTimeFormat("es-AR", { timeZone, ...opts })
        .formatToParts(d)
        .map((p) => [p.type, p.value]),
    );
  const day = get({ weekday: "short", day: "2-digit", month: "2-digit" });
  const clock = get({ hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const weekday = String(day.weekday ?? "").replace(".", "");
  return {
    date: `${weekday} ${day.day}/${day.month}`,
    time: `${clock.hour}:${clock.minute}`,
  };
}

/** `YYYY-MM-DD` de `now` en la zona del negocio (lo que espera `<input type="date">`). */
export function localDate(now: Date, timeZone: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

/** Hora `HH:MM` de un instante en la zona del negocio. */
export function localTime(iso: string, timeZone: string): string {
  return parts(iso, timeZone).time;
}

/**
 * `[inicio, fin)` legible: "lun 21/09 · todo el día", "lun 21/09 al mié 23/09 · todo el día",
 * "lun 21/09 · 09:00 a 11:00" o, si cruza de día, "lun 21/09 22:00 a mar 22/09 02:00".
 */
export function formatRange(startsAt: string, endsAt: string, timeZone: string): string {
  const start = parts(startsAt, timeZone);
  const end = parts(endsAt, timeZone);
  if (start.time === "00:00" && end.time === "00:00") {
    const last = parts(new Date(new Date(endsAt).getTime() - 60_000), timeZone);
    const days = last.date === start.date ? start.date : `${start.date} al ${last.date}`;
    return `${days} · todo el día`;
  }
  if (start.date === end.date) return `${start.date} · ${start.time} a ${end.time}`;
  return `${start.date} ${start.time} a ${end.date} ${end.time}`;
}
