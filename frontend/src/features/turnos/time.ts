/**
 * Hora local del negocio → instante UTC (FASE-5-CONTRATO Z9). El dueño elige día y hora del
 * lavadero; el backend recibe el instante con zona. Sin librerías: el desfasaje se lee de `Intl`
 * para ese momento (así un cambio de horario de verano, si algún día vuelve, también entra).
 */

/** Minutos que `timeZone` está adelantada respecto de UTC en el instante `utcMs`. */
function offsetMinutes(utcMs: number, timeZone: string): number {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone,
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    })
      .formatToParts(new Date(utcMs))
      .map((p) => [p.type, p.value]),
  );
  const asUtc = Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
    Number(parts.hour),
    Number(parts.minute),
    Number(parts.second),
  );
  return Math.round((asUtc - utcMs) / 60_000);
}

/** `YYYY-MM-DD` + `HH:MM` en la zona del negocio → ISO 8601 en UTC (`…Z`). */
export function zonedToUtcIso(date: string, time: string, timeZone: string): string {
  const [y, m, d] = date.split("-").map(Number);
  const [hh, mm] = time.split(":").map(Number);
  const naive = Date.UTC(y!, m! - 1, d!, hh!, mm!);
  // Dos pasadas: la primera estima con el desfasaje de la hora "ingenua", la segunda corrige.
  let utc = naive - offsetMinutes(naive, timeZone) * 60_000;
  utc = naive - offsetMinutes(utc, timeZone) * 60_000;
  return new Date(utc).toISOString();
}

/** `YYYY-MM-DD` de `date` más `days` días (para rangos de la lista). */
export function addDays(date: string, days: number): string {
  const [y, m, d] = date.split("-").map(Number);
  return new Date(Date.UTC(y!, m! - 1, d! + days)).toISOString().slice(0, 10);
}

/** "lunes 23/11": el día de un instante en la zona del negocio (encabezado de la lista). */
export function formatDay(iso: string, timeZone: string): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("es-AR", {
      timeZone,
      weekday: "long",
      day: "2-digit",
      month: "2-digit",
    })
      .formatToParts(new Date(iso))
      .map((p) => [p.type, p.value]),
  );
  return `${parts.weekday} ${parts.day}/${parts.month}`;
}

/** Link de WhatsApp a un E.164 (`+5493510000001` → `https://wa.me/5493510000001`). */
export function whatsappLink(phoneE164: string | null | undefined): string | null {
  if (!phoneE164) return null;
  const digits = phoneE164.replace(/\D/g, "");
  return digits ? `https://wa.me/${digits}` : null;
}
