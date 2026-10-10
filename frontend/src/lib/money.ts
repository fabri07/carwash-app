/**
 * Plata en centavos enteros, siempre. Un `number` de centavos nunca pasa por
 * float con decimales: `0.1 + 0.2` no existe en este archivo.
 *
 * Trampa del legacy que NO se reproduce (`parseMoney_` en el Apps Script):
 * `"20.000"` daba `20` (error de factor 1000 con el formato argentino) y
 * `"abc"` daba `0` en silencio. Acá `"20.000"` son veinte mil pesos y lo que no
 * se puede leer devuelve `null`, que el formulario muestra como error.
 */

/** Separador de miles del formato argentino. */
const THOUSANDS = ".";
/** Separador decimal del formato argentino. */
const DECIMAL = ",";

function groupThousands(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, THOUSANDS);
}

/**
 * `2000000` → `"$ 20.000,00"`. Armado a mano y no con `Intl`: el formato de
 * `Intl` cambia entre motores (espacio duro, posición del signo) y un total en
 * pantalla no puede depender del navegador.
 */
export function formatARS(cents: number): string {
  if (!Number.isSafeInteger(cents)) {
    throw new RangeError(`formatARS espera centavos enteros, recibió ${cents}`);
  }
  const negative = cents < 0;
  const abs = Math.abs(cents);
  const pesos = Math.floor(abs / 100);
  const rest = abs % 100;
  const body = `$ ${groupThousands(String(pesos))}${DECIMAL}${String(rest).padStart(2, "0")}`;
  return negative ? `-${body}` : body;
}

/** Igual que `formatARS` pero sin el `$`: lo que muestra un campo de importe. */
export function formatAmount(cents: number): string {
  return formatARS(cents).replace("$ ", "");
}

/** Miles con punto en grupos de tres, sin ceros a la izquierda salvo el "0" solo. */
const INTEGER_PART = /^(?:\d{1,3}(?:\.\d{3})+|\d+)$/;

/**
 * Lee un importe escrito en formato argentino y devuelve centavos, o `null` si
 * no se puede leer sin adivinar.
 *
 * - `"20.000"` → `2000000` · `"20.000,50"` → `2000050` · `"20000"` → `2000000`
 * - `"1,5"` → `150` · `"$ 1.234,56"` → `123456`
 * - `"abc"`, `""`, `"1,234"` (tres decimales), `"20.5"` (punto que no es de
 *   miles), `"-5"` → `null`.
 *
 * `"20.5"` se rechaza en vez de leerse como 20,50: con el punto como separador
 * de miles, adivinar ahí es exactamente el error de `parseMoney_`.
 */
export function parseARS(input: string): number | null {
  const s = input.replace(/\$/g, "").replace(/\s/g, "");
  if (s === "") return null;
  const parts = s.split(DECIMAL);
  if (parts.length > 2) return null;
  const [intPart = "", decPart] = parts;
  if (!INTEGER_PART.test(intPart)) return null;
  if (decPart !== undefined && !/^\d{1,2}$/.test(decPart)) return null;
  const pesos = Number(intPart.replaceAll(THOUSANDS, ""));
  const cents = decPart === undefined ? 0 : Number(decPart.padEnd(2, "0"));
  const total = pesos * 100 + cents;
  return Number.isSafeInteger(total) ? total : null;
}
