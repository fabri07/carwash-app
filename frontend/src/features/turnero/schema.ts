import { z } from "zod";

/**
 * Formularios del turnero público (FASE-5-CONTRATO §4.2). El servidor vuelve a validar todo:
 * acá solo se evita mandar lo que seguro vuelve con 422. El WhatsApp lo normaliza el backend
 * (`normalize_phone_ar`): se piden al menos 10 dígitos (área + número).
 */
const phone = z
  .string()
  .trim()
  .min(1, "Ingresá tu WhatsApp.")
  .max(40, "Demasiado largo.")
  .refine((v) => v.replace(/\D/g, "").length >= 10, "Ingresá el número con código de área.");

export const bookingDetailsSchema = z.object({
  name: z.string().trim().min(1, "Ingresá tu nombre.").max(80, "Hasta 80 caracteres."),
  phone,
  plate: z.string().trim().max(10, "Hasta 10 caracteres."),
  acceptPolicy: z.boolean().refine((v) => v, "Tenés que aceptar la política de cancelación."),
});

export type BookingDetailsValues = z.output<typeof bookingDetailsSchema>;

export const lookupSchema = z.object({
  code: z.string().trim().min(6, "Ingresá el código del turno.").max(20, "Código inválido."),
  phone,
});

export type LookupValues = z.output<typeof lookupSchema>;

export const cancelSchema = z.object({
  reason: z.string().trim().max(160, "Hasta 160 caracteres."),
});

export type CancelValues = z.output<typeof cancelSchema>;
