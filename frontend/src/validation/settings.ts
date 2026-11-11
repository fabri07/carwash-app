import { z } from "zod";

import { percentText } from "@/validation/percent";

/**
 * Schemas de la configuración del negocio (PR 4.2b). Límites = los de
 * `schemas/settings.py` del backend; el servidor vuelve a validar todo.
 *
 * Los números se editan como texto y se convierten al enviar: así el formulario
 * nunca guarda un `NaN` ni un `""` convertido a `0` en silencio.
 */

const MAX_MINUTES = 60 * 24 * 30; // un mes: cualquier cosa mayor es un typo del backend

function wholeNumber(min: number, max: number, what: string) {
  return z
    .string()
    .trim()
    .regex(/^\d+$/, `${what}: poné un número entero`)
    .refine((v) => Number(v) >= min && Number(v) <= max, `${what}: entre ${min} y ${max}`);
}

/** `^[a-z0-9-]{3,40}$` (CHECK de `tenants.slug`). */
export const SLUG_PATTERN = /^[a-z0-9-]{3,40}$/;
/** E.164: `+` y de 8 a 15 dígitos, sin ceros a la izquierda. */
const E164 = /^\+[1-9][0-9]{7,14}$/;
const INSTAGRAM = /^https:\/\/(www\.)?instagram\.com\/[^\s/?#][^\s]*$/i;

export const businessSchema = z.object({
  slug: z
    .string()
    .trim()
    .toLowerCase()
    .regex(SLUG_PATTERN, "De 3 a 40 caracteres: letras sin tilde, números o guiones"),
  address: z.string().trim().max(300, "Máximo 300 caracteres"),
  whatsapp_e164: z
    .string()
    .trim()
    .refine((v) => v === "" || E164.test(v), "Formato internacional, por ejemplo +5493515551234"),
  instagram_url: z
    .string()
    .trim()
    .max(200, "Máximo 200 caracteres")
    .refine((v) => v === "" || INSTAGRAM.test(v), "Un link https://instagram.com/tu-negocio"),
  welcome_text: z.string().trim().max(280, "Máximo 280 caracteres"),
  cancellation_policy: z.string().trim().max(2000, "Máximo 2000 caracteres"),
});
export type BusinessInput = z.infer<typeof businessSchema>;

export const depositSchema = z.object({
  deposit_enabled: z.boolean(),
  deposit_percent: percentText({ min: 0.01, example: "20 o 12,5" }),
  hold_min: wholeNumber(1, MAX_MINUTES, "Retención de la seña"),
  tolerance_min: wholeNumber(1, MAX_MINUTES, "Tolerancia"),
  late_cancel_threshold_min: wholeNumber(1, MAX_MINUTES, "Cancelación tardía"),
  min_advance_min: wholeNumber(0, MAX_MINUTES, "Anticipación mínima"),
  quote_validity_hours: wholeNumber(1, 24 * 30, "Vigencia de la cotización"),
});
export type DepositInput = z.infer<typeof depositSchema>;

/** Personalización (PR 4.5): los códigos los valida el backend contra su catálogo. */
export const brandingSchema = z.object({
  palette: z.string().min(1, "Elegí una paleta"),
  font: z.string().min(1, "Elegí una tipografía"),
});
export type BrandingInput = z.infer<typeof brandingSchema>;
