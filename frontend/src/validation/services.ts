import { z } from "zod";

import { inputToBps } from "@/lib/percent";
import { percentText } from "@/validation/percent";
import { hhmm } from "@/lib/time";
import type { ScheduleRule, ScheduleRuleItem, ServicePriceItem } from "@/types/api";

/**
 * Schemas del catálogo de servicios (PR 4.3). Límites = los de `schemas/services.py`:
 * precio entero > 0 hasta $ 100.000.000 (en centavos), duración entera > 0 hasta 14 días,
 * nombre buscable (la búsqueda ignora tildes y símbolos: un nombre sin letras ni números
 * quedaría inencontrable).
 */

/** Tope de plata: $ 100.000.000,00 en centavos. */
export const MAX_PRICE_CENTS = 10_000_000_000;
/** 14 días en minutos: el detailing con curado puede dejar el auto un tiempo. */
export const MAX_DURATION_MIN = 24 * 60 * 14;
export const MAX_SERVICE_NAME = 80;
export const MAX_CATEGORY_NAME = 60;
export const MAX_NOTES = 500;

/** Sin acentos ni símbolos queda al menos una letra (a-z) o un número, como `normalize` del backend. */
export function isSearchable(name: string): boolean {
  const folded = name.normalize("NFKD").replace(/[^\x00-\x7f]/g, "");
  return /[a-z0-9]/i.test(folded);
}

const searchableName = (max: number) =>
  z
    .string()
    .trim()
    .min(1, "Poné un nombre")
    .max(max, `Máximo ${max} caracteres`)
    .refine(
      isSearchable,
      "Usá al menos una letra (sin tilde) o un número: si no, no se puede buscar",
    );

const sortOrder = z
  .string()
  .trim()
  .regex(/^\d+$/, "Poné un número entero")
  .refine((v) => Number(v) <= 10000, "Máximo 10000");

export const categorySchema = z.object({
  name: searchableName(MAX_CATEGORY_NAME),
  sort_order: sortOrder,
});
export type CategoryInput = z.infer<typeof categorySchema>;

const priceCents = z
  .number({
    required_error: "Poné un precio",
    invalid_type_error: "Poné un precio, por ejemplo 20.000",
  })
  .int("Poné un precio válido")
  .min(1, "El precio tiene que ser mayor a cero")
  .max(MAX_PRICE_CENTS, "Máximo $ 100.000.000");

const durationMin = z
  .string()
  .trim()
  .regex(/^\d+$/, "Poné los minutos, en número entero")
  .refine((v) => Number(v) >= 1, "Tiene que durar al menos 1 minuto")
  .refine((v) => Number(v) <= MAX_DURATION_MIN, "Máximo 14 días (20160 minutos)");

const pricingMode = z.enum(["PRECIO_FIJO", "A_COTIZAR"]);

/** Precio y duración de un tamaño. Todos son obligatorios: sin precio no hay servicio (A8). */
export const priceRowSchema = z.object({
  vehicle_size_id: z.string(),
  price_cents: priceCents,
  duration_min: durationMin,
});
export type PriceRowInput = z.infer<typeof priceRowSchema>;

const notes = z.string().trim().max(MAX_NOTES, `Máximo ${MAX_NOTES} caracteres`);
/** `""` = sin categoría. */
const categoryId = z.string();

const prices = z.array(priceRowSchema).min(1, "Primero cargá al menos un tamaño de vehículo");

/** Seña propia del servicio (D4-17): `""` = usa la general del negocio. */
const ownDeposit = percentText({ min: 0.01, example: "30 o 12,5", optional: true });

/** `""` → `null` (usa la del negocio); `"30"` → `3000`. */
export function toDepositBps(value: string): number | null {
  return inputToBps(value);
}

export const serviceCreateSchema = z.object({
  name: searchableName(MAX_SERVICE_NAME),
  category_id: categoryId,
  notes,
  pricing_mode: pricingMode,
  deposit_percent: ownDeposit,
  prices,
});
export type ServiceCreateInput = z.infer<typeof serviceCreateSchema>;

/** Tope de filas de regla horaria por servicio (`MAX_RULE_ROWS` del backend). */
export const MAX_RULE_ROWS = 50;
const clock = z.string().regex(/^\d{2}:\d{2}$/, "Poné la hora");

/**
 * Una ventana de la regla horaria (FASE-5-CONTRATO §3.2): el servicio puede arrancar
 * entre `window_start` y `last_start` (inclusive) ese día. Hora local del negocio.
 */
export const scheduleRuleRowSchema = z
  .object({
    weekday: z.string().regex(/^[1-7]$/, "Elegí el día"),
    window_start: clock,
    last_start: clock,
  })
  .refine((v) => v.last_start >= v.window_start, {
    path: ["last_start"],
    message: "No puede ser antes de «desde»",
  });
export type ScheduleRuleRowInput = z.infer<typeof scheduleRuleRowSchema>;

/**
 * Nombre, categoría y notas: lo que edita quien tiene `CATALOGO_EDITAR`; los precios son otro
 * permiso. F5 suma si el servicio aparece en el turnero y sus reglas horarias.
 */
export const serviceEditSchema = z.object({
  name: searchableName(MAX_SERVICE_NAME),
  category_id: categoryId,
  notes,
  solo_a_mano: z.boolean(),
  schedule_rules: z
    .array(scheduleRuleRowSchema)
    .max(MAX_RULE_ROWS, `Máximo ${MAX_RULE_ROWS} ventanas`),
});
export type ServiceEditInput = z.infer<typeof serviceEditSchema>;

export const servicePricesSchema = z.object({
  pricing_mode: pricingMode,
  deposit_percent: ownDeposit,
  prices,
});
export type ServicePricesInput = z.infer<typeof servicePricesSchema>;

/** Una fila por servicio elegido de la plantilla; `name` es solo para mostrarlo. */
export const templateApplySchema = z.object({
  items: z
    .array(
      z.object({
        glossary_code: z.string(),
        name: z.string(),
        pricing_mode: pricingMode,
        prices,
      }),
    )
    .min(1, "Elegí al menos un servicio"),
});
export type TemplateApplyInput = z.infer<typeof templateApplySchema>;

/** Reglas del servicio → filas del formulario (horas `HH:MM`, día como texto). */
export function toRuleRows(rules: ScheduleRule[]): ScheduleRuleRowInput[] {
  return rules.map((r) => ({
    weekday: String(r.weekday),
    window_start: hhmm(r.window_start),
    last_start: hhmm(r.last_start),
  }));
}

/** Filas del formulario → payload de la API. */
export function toScheduleRules(rows: ScheduleRuleRowInput[]): ScheduleRuleItem[] {
  return rows.map((r) => ({
    weekday: Number(r.weekday),
    window_start: r.window_start,
    last_start: r.last_start,
  }));
}

/** Filas del formulario → payload de la API (minutos de texto a número). */
export function toPriceItems(rows: PriceRowInput[]): ServicePriceItem[] {
  return rows.map((r) => ({
    vehicle_size_id: r.vehicle_size_id,
    price_cents: r.price_cents,
    duration_min: Number(r.duration_min),
  }));
}
