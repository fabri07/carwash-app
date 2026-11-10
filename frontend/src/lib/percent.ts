/**
 * Porcentajes ↔ puntos básicos (bps). El backend guarda la seña y las comisiones en
 * bps enteros (100 bps = 1 %); la pantalla habla en porcentaje. Nunca un float a la
 * red: la conversión redondea al bps.
 */

/** `2000` → `20`; `1250` → `12.5`. */
export function bpsToPercent(bps: number): number {
  return bps / 100;
}

/** `20` → `2000`; `12.5` → `1250`. Redondea al punto básico más cercano. */
export function percentToBps(percent: number): number {
  return Math.round(percent * 100);
}

/** `1250` → `"12,5"`: el valor inicial de un campo de porcentaje (coma decimal, sin `%`). */
export function bpsToInput(bps: number): string {
  return String(bpsToPercent(bps)).replace(".", ",");
}

/** `1250` → `"12,5 %"`, `2000` → `"20 %"` (formato argentino). */
export function formatPercent(bps: number): string {
  const text = bpsToPercent(bps).toLocaleString("es-AR", { maximumFractionDigits: 2 });
  return `${text} %`;
}

/**
 * Lee lo que escribe el usuario: `"20"`, `"12,5"`, `"12.5"`, hasta 2 decimales.
 * `null` si no es un número que se pueda leer sin adivinar.
 */
export function parsePercent(input: string): number | null {
  const text = input.trim().replace("%", "").trim();
  if (!/^\d{1,3}([.,]\d{1,2})?$/.test(text)) return null;
  return Number(text.replace(",", "."));
}

/** Lo que escribe el usuario → bps: `"12,5"` → `1250`; `null` si está vacío o no se puede leer. */
export function inputToBps(input: string): number | null {
  const percent = parsePercent(input);
  return percent === null ? null : percentToBps(percent);
}
