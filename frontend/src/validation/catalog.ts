import { z } from "zod";

import { percentText } from "@/validation/percent";

/**
 * Schemas del catálogo configurable (PR 4.2b): tamaños, puestos, franjas y medios
 * de pago. Límites = los de `schemas/catalog.py` del backend.
 */

const label = z.string().trim().min(1, "Poné un nombre").max(60, "Máximo 60 caracteres");

/** `CODE_PATTERN` del backend: mayúsculas, números y guion bajo. El backend pasa a mayúsculas. */
export const CODE_PATTERN = /^[A-Z0-9_]{1,30}$/;
const code = z
  .string()
  .trim()
  .toUpperCase()
  .regex(CODE_PATTERN, "De 1 a 30 caracteres: mayúsculas, números o guion bajo");

const sortOrder = z
  .string()
  .trim()
  .regex(/^\d+$/, "Poné un número entero")
  .refine((v) => Number(v) <= 10000, "Máximo 10000");

export const vehicleSizeSchema = z.object({ code, label, sort_order: sortOrder });
export type VehicleSizeInput = z.infer<typeof vehicleSizeSchema>;

export const resourceSchema = z.object({ name: label, sort_order: sortOrder });
export type ResourceInput = z.infer<typeof resourceSchema>;

const time = z.string().regex(/^\d{2}:\d{2}$/, "Poné la hora");

export const businessHoursSchema = z
  .object({
    weekday: z.string().regex(/^[1-7]$/, "Elegí el día"),
    opens_at: time,
    closes_at: time,
  })
  .refine((v) => v.closes_at > v.opens_at, {
    path: ["closes_at"],
    message: "Tiene que cerrar después de abrir",
  });
export type BusinessHoursInput = z.infer<typeof businessHoursSchema>;

export const paymentMethodSchema = z
  .object({
    code,
    label,
    commission_percent: percentText({ min: 0, example: "3 o 1,5" }),
    settlement_account: z.string().trim().max(60, "Máximo 60 caracteres"),
    for_income: z.boolean(),
    for_expense: z.boolean(),
  })
  .refine((v) => v.for_income || v.for_expense, {
    path: ["for_income"],
    message: "Tiene que servir para cobrar o para pagar",
  });
export type PaymentMethodInput = z.infer<typeof paymentMethodSchema>;
