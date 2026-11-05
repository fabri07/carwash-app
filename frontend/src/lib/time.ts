/** `"09:30:00"` o `"09:30"` → `"09:30"` (lo que espera `<input type="time">`). */
export function hhmm(time: string): string {
  return time.slice(0, 5);
}
