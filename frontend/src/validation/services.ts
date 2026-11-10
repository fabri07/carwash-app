import { z } from "zod";

import { parsePercent, percentToBps } from "@/lib/percent";
import type { ServicePriceItem } from "@/types/api";

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

/**
 * Seña propia del servicio (D4-17), como texto: `""` = usa la general del negocio. Mismo rango que
 * la general (0,01 % a 100 %).
 */
const ownDeposit = z
  .string()
  .trim()
  .refine((v) => v === "" || parsePercent(v) !== null, "Poné un porcentaje, por ejemplo 30 o 12,5")
  .refine((v) => {
    const p = v === "" ? null : parsePercent(v);
    return p === null || (p >= 0.01 && p <= 100);
  }, "Entre 0,01 % y 100 %");

/** `""` → `null` (usa la del negocio); `"30"` → `3000`. */
export function toDepositBps(value: string): number | null {
  const percent = value.trim() === "" ? null : parsePercent(value);
  return percent === null ? null : percentToBps(percent);
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

/** Nombre, categoría y notas: lo que edita quien tiene `CATALOGO_EDITAR`; los precios son otro permiso. */
export const serviceEditSchema = z.object({
  name: searchableName(MAX_SERVICE_NAME),
  category_id: categoryId,
  notes,
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

/** Filas del formulario → payload de la API (minutos de texto a número). */
export function toPriceItems(rows: PriceRowInput[]): ServicePriceItem[] {
  return rows.map((r) => ({
    vehicle_size_id: r.vehicle_size_id,
    price_cents: r.price_cents,
    duration_min: Number(r.duration_min),
  }));
}
