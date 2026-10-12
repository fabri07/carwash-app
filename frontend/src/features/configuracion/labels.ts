/**
 * Textos de los catálogos de operación, compartidos por las tablas, los
 * formularios y el wizard de alta (PR 4.6).
 */

/** Día ISO (1 = lunes … 7 = domingo), igual que `business_hours.weekday`. */
export const WEEKDAYS: readonly { value: number; label: string }[] = [
  { value: 1, label: "Lunes" },
  { value: 2, label: "Martes" },
  { value: 3, label: "Miércoles" },
  { value: 4, label: "Jueves" },
  { value: 5, label: "Viernes" },
  { value: 6, label: "Sábado" },
  { value: 7, label: "Domingo" },
];

export function weekdayLabel(weekday: number): string {
  return WEEKDAYS.find((d) => d.value === weekday)?.label ?? String(weekday);
}

/** `"09:00:00"` (lo que devuelve la API) → `"09:00"`. */
export function shortTime(value: string): string {
  return value.slice(0, 5);
}

/** Puntos básicos → "3,5 %". */
export function bpsAsPercent(bps: number): string {
  return `${String(bps / 100).replace(".", ",")} %`;
}
