/**
 * Exportación CSV (FASE-4-CONTRATO A14). Portado de Véktor (`lib/csv.ts`) con
 * tres cambios:
 *
 * 1. **Separador `;` por defecto.** Excel con configuración regional argentina
 *    usa la coma como separador decimal y espera `;` entre columnas: un CSV con
 *    `,` se abre todo en la columna A.
 * 2. **Escape completo.** Véktor entrecomillaba solo ante `,`, `"` o `\n`; un
 *    `;` o un `\r` sueltos rompían la fila. Acá se entrecomilla ante cualquier
 *    separador (`;` y `,`, para que el archivo sirva con los dos), comilla o
 *    salto de línea.
 * 3. **Inyección de fórmulas neutralizada.** Una celda que empieza con
 *    `= + - @` (o tab / retorno de carro) Excel la evalúa como fórmula: un
 *    cliente que se llame `=HYPERLINK(...)` sería código al abrir el archivo
 *    (OWASP "CSV Injection"). Se antepone `'`, que Excel muestra como texto. Un
 *    número negativo puro (`-1500`, `-20,5`) no es fórmula y queda intacto.
 */

/** Separador por defecto: el de Excel es-AR. */
export const CSV_SEPARATOR = ";";

/** BOM UTF-8: sin él, Excel abre el archivo como ANSI y rompe los acentos. */
export const UTF8_BOM = "﻿";

const FORMULA_START = /^[=+\-@\t\r]/;
const PLAIN_NUMBER = /^-?\d+(?:[.,]\d+)*$/;

/** Neutraliza una celda que Excel interpretaría como fórmula. */
export function neutralizeFormula(s: string): string {
  if (FORMULA_START.test(s) && !PLAIN_NUMBER.test(s)) return `'${s}`;
  return s;
}

/** Escapa un valor para una celda CSV. `null`/`undefined` → celda vacía. */
export function toCSVValue(val: unknown): string {
  const s = neutralizeFormula(val == null ? "" : String(val));
  if (/[";,\n\r]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

/** Arma el texto del CSV (sin BOM). Filas separadas por CRLF, como pide RFC 4180. */
export function buildCSV(
  headers: string[],
  rows: unknown[][],
  separator: string = CSV_SEPARATOR,
): string {
  return [headers, ...rows].map((r) => r.map(toCSVValue).join(separator)).join("\r\n");
}

/** `-YYYY-MM-DD` del día de hoy, para que dos exportaciones de días distintos no se pisen. */
function dateSuffix(now: Date): string {
  return now.toISOString().slice(0, 10);
}

/**
 * Descarga un CSV con BOM. `filename` va SIN fecha ni extensión: se le agrega
 * `-YYYY-MM-DD.csv` (si ya trae `.csv`, se lo saca para no duplicarlo).
 */
export function downloadCSV(
  filename: string,
  headers: string[],
  rows: unknown[][],
  now: Date = new Date(),
): void {
  const blob = new Blob([UTF8_BOM + buildCSV(headers, rows)], {
    type: "text/csv;charset=utf-8;",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${filename.replace(/\.csv$/i, "")}-${dateSuffix(now)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
