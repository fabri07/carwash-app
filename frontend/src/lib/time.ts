/** `"09:30:00"` o `"09:30"` → `"09:30"` (lo que espera `<input type="time">`). */
export function hhmm(time: string): string {
  return time.slice(0, 5);
}

/**
 * Minutos → texto legible sin descartar unidades: `45` → `"45 min"`, `90` → `"1 h 30 min"`,
 * `1440` → `"1 d"`, `1470` → `"1 d 30 min"`, `1530` → `"1 d 1 h 30 min"`. El detailing puede durar días.
 */
export function formatDuration(minutes: number): string {
  if (!Number.isFinite(minutes) || minutes < 0) return "—";
  const total = Math.round(minutes);
  if (total < 60) return `${total} min`;
  const days = Math.floor(total / 1440);
  const hours = Math.floor((total % 1440) / 60);
  const mins = total % 60;
  return [days > 0 && `${days} d`, hours > 0 && `${hours} h`, mins > 0 && `${mins} min`]
    .filter(Boolean)
    .join(" ");
}
