import { z } from "zod";

/**
 * Schemas de configuración y catálogos (PR 4.2). Límites = los de
 * `backend/app/schemas/configuracion.py`; el servidor vuelve a validar todo.
 *
 * Los números se tipean en `<input>` y viajan como texto dentro del
 * formulario: así un campo vacío es "" (y no `NaN`) y se acepta la coma
 * decimal ("2,5"). La conversión a lo que pide la API (minutos, puntos
 * básicos) la hace cada formulario al enviar, con los helpers de abajo.
 */

/** `^[a-z0-9-]{3,40}$` (`domain/slug.py`). El backend pasa a minúsculas. */
export const SLUG_PATTERN = /^[a-z0-9-]{3,40}$/;
/** `^[A-Z0-9_]{1,30}$` (`models/catalog.py`). El backend pasa a mayúsculas. */
export const CODE_PATTERN = /^[A-Z0-9_]{1,30}$/;
/** Tope del texto de bienvenida (`tenant.WELCOME_TEXT_MAX`). */
export const WELCOME_TEXT_MAX = 280;

const INTEGER = /^\d+$/;
const DECIMAL = /^\d+([.,]\d{1,2})?$/;
const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

/** "2,5" o "2.5" → 2.5. Solo para valores que ya pasaron el schema. */
export function parseDecimal(value: string): number {
  return Number(value.trim().replace(",", "."));
}

/** Número para mostrar en un input, con coma decimal y sin ceros de más. */
export function formatDecimal(value: number): string {
  return String(Math.round(value * 100) / 100).replace(".", ",");
}

/** Entero tipeado dentro de `[min, max]`. */
function intField(min: number, max: number, empty: string) {
  return z
    .string()
    .trim()
    .min(1, empty)
    .regex(INTEGER, "Tiene que ser un número entero")
    .refine((v) => Number(v) >= min && Number(v) <= max, `Entre ${min} y ${max}`);
}

/** Texto opcional: vacío es "sin dato" y se manda como `null`. */
function optionalText(max: number) {
  return z.string().trim().max(max, `Máximo ${max} caracteres`);
}

function label(empty: string) {
  return z.string().trim().min(1, empty).max(60, "Máximo 60 caracteres");
}

const code = z
  .string()
  .trim()
  .toUpperCase()
  .min(1, "Poné un código")
  .regex(CODE_PATTERN, "Hasta 30 caracteres: letras sin tilde, números o guion bajo");

const sortOrder = intField(0, 1000, "Poné un número (0 va primero)");

// ── Mi negocio ──────────────────────────────────────────────────────────────

export const businessSchema = z.object({
  slug: z
    .string()
    .trim()
    .toLowerCase()
    .min(1, "Elegí una dirección")
    .regex(SLUG_PATTERN, "De 3 a 40 caracteres: letras sin tilde, números o guiones"),
  address: optionalText(200),
  /** Lo tipeado (`11 2345-6789`); el backend lo pasa a E.164 o lo rechaza. */
  whatsapp: z
    .string()
    .trim()
    .max(30, "Máximo 30 caracteres")
    .regex(/^[\d\s()+-]*$/, "Solo números, espacios, guiones o +"),
  instagram_url: optionalText(200).refine(
    (v) => v === "" || /^https?:\/\/\S+$/.test(v),
    "Pegá el link completo, que empiece con https://",
  ),
  welcome_text: optionalText(WELCOME_TEXT_MAX),
  cancellation_policy: optionalText(2000),
});
export type BusinessInput = z.infer<typeof businessSchema>;

export const depositSchema = z.object({
  deposit_enabled: z.boolean(),
  /** Porcentaje entero; la API lo guarda en puntos básicos (×100). */
  /** En % con hasta 2 decimales (la API guarda puntos básicos, 1..10000): 25,5 % es válido. */
  deposit_percent: z
    .string()
    .trim()
    .min(1, "Poné el porcentaje de la seña")
    .regex(DECIMAL, "Tiene que ser un número, como 20 o 25,5")
    .refine((v) => parseDecimal(v) >= 0.01 && parseDecimal(v) <= 100, "Entre 0,01 y 100"),
});
export type DepositInput = z.infer<typeof depositSchema>;

export const bookingRulesSchema = z.object({
  tolerance_min: intField(1, 240, "Poné los minutos de tolerancia"),
  /** En horas en la pantalla (acepta "1,5"); en minutos en la API, hasta 7 días. */
  late_cancel_hours: z
    .string()
    .trim()
    .min(1, "Poné las horas")
    .regex(DECIMAL, "Tiene que ser un número, como 12 o 1,5")
    .refine((v) => {
      const min = Math.round(parseDecimal(v) * 60);
      return min >= 1 && min <= 10_080;
    }, "Entre 1 minuto y 168 horas (7 días)"),
  hold_min: intField(1, 1440, "Poné los minutos"),
  min_advance_min: intField(0, 10_080, "Poné los minutos (0 si no hace falta)"),
});
export type BookingRulesInput = z.infer<typeof bookingRulesSchema>;

// ── Catálogos ───────────────────────────────────────────────────────────────

export const vehicleSizeSchema = z.object({
  code,
  label: label("Poné un nombre"),
  sort_order: sortOrder,
});
export type VehicleSizeInput = z.infer<typeof vehicleSizeSchema>;

export const resourceSchema = z.object({
  name: label("Poné un nombre"),
  sort_order: sortOrder,
});
export type ResourceInput = z.infer<typeof resourceSchema>;

export const businessHourSchema = z
  .object({
    weekday: z.string().regex(/^[1-7]$/, "Elegí el día"),
    opens_at: z.string().regex(TIME, "Poné la hora de apertura"),
    closes_at: z.string().regex(TIME, "Poné la hora de cierre"),
  })
  // "HH:MM" con ceros a la izquierda: la comparación de texto ordena bien.
  .refine((v) => !TIME.test(v.opens_at) || !TIME.test(v.closes_at) || v.closes_at > v.opens_at, {
    path: ["closes_at"],
    message: "Tiene que cerrar después de abrir",
  });
export type BusinessHourInput = z.infer<typeof businessHourSchema>;

export const paymentMethodSchema = z.object({
  code,
  label: label("Poné un nombre"),
  /** En % con hasta 2 decimales; la API lo guarda en puntos básicos (0..10000). */
  commission_percent: z
    .string()
    .trim()
    .min(1, "Poné la comisión (0 si no cobra)")
    .regex(DECIMAL, "Tiene que ser un número, como 0 o 3,5")
    .refine((v) => parseDecimal(v) <= 100, "Entre 0 y 100"),
  settlement_account: optionalText(60),
  for_income: z.boolean(),
  for_expense: z.boolean(),
});
export type PaymentMethodInput = z.infer<typeof paymentMethodSchema>;
