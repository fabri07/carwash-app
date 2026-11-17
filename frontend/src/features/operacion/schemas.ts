import { z } from "zod";

/**
 * Schemas de las cargas a mano de la Fase 6 (ADR-0010). Los textos de error son los que ve quien
 * carga; los límites razonables, no los del backend (que igual valida y responde 422).
 */
const optionalText = (max: number, message: string) => z.string().trim().max(max, message);

const person = {
  /** Vacío = "sin nombre": el job queda sin cliente y el turno a nombre de "Sin nombre". */
  customer_name: optionalText(120, "Máximo 120 caracteres"),
  /** Vacío = sin teléfono. Con teléfono, el cliente se reconoce la próxima vez. */
  customer_phone: optionalText(30, "Máximo 30 caracteres"),
  /** Vacío = sin patente (el trabajo crea un auto anónimo). */
  plate: optionalText(12, "Máximo 12 caracteres"),
};

const vehicle = {
  vehicle_size_id: z.string().min(1, "Elegí el tamaño del vehículo"),
  service_ids: z.array(z.string()).min(1, "Elegí al menos un servicio"),
};

const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Elegí el día");
const clock = z.string().regex(/^\d{2}:\d{2}$/, "Elegí la hora");

/** Ingreso sin turno (LLEGÓ de un cliente que no reservó). El puesto es opcional. */
export const walkInSchema = z.object({ ...person, ...vehicle, resource_id: z.string() });
export type WalkInInput = z.infer<typeof walkInSchema>;

/** Turno "solo a mano": con puesto, día y hora, y opcionalmente la seña ya cobrada. */
export const manualBookingSchema = z
  .object({
    ...person,
    ...vehicle,
    resource_id: z.string().min(1, "Elegí el puesto"),
    date: day,
    time: clock,
    deposit_enabled: z.boolean(),
    deposit_method_id: z.string(),
    deposit_cents: z.number().nullable(),
  })
  .superRefine((v, ctx) => {
    if (!v.deposit_enabled) return;
    if (!v.deposit_method_id) {
      ctx.addIssue({
        code: "custom",
        path: ["deposit_method_id"],
        message: "Elegí el medio de pago",
      });
    }
    if (v.deposit_cents !== null && v.deposit_cents <= 0) {
      ctx.addIssue({
        code: "custom",
        path: ["deposit_cents"],
        message: "El importe tiene que ser mayor a cero",
      });
    }
  });
export type ManualBookingInput = z.infer<typeof manualBookingSchema>;

/** Carga retrospectiva: un trabajo ya hecho, con inicio, fin y cobro (cobrado o pendiente). */
export const retrospectiveSchema = z
  .object({
    ...person,
    ...vehicle,
    resource_id: z.string(),
    date: day,
    start_time: clock,
    end_time: clock,
    paid: z.boolean(),
    payment_method_id: z.string(),
    /** `null` con cobro = el total del trabajo. */
    amount_cents: z.number().nullable(),
  })
  .superRefine((v, ctx) => {
    if (v.end_time < v.start_time) {
      ctx.addIssue({
        code: "custom",
        path: ["end_time"],
        message: "El fin no puede ser anterior al inicio",
      });
    }
    if (!v.paid) return;
    if (!v.payment_method_id) {
      ctx.addIssue({
        code: "custom",
        path: ["payment_method_id"],
        message: "Elegí el medio de pago",
      });
    }
    if (v.amount_cents !== null && v.amount_cents <= 0) {
      ctx.addIssue({
        code: "custom",
        path: ["amount_cents"],
        message: "El importe tiene que ser mayor a cero",
      });
    }
  });
export type RetrospectiveInput = z.infer<typeof retrospectiveSchema>;

/** Recepción completa (opcional, D-007): todo es opcional. */
export const inspectionSchema = z.object({
  dirt_level: z.string(),
  valuables: optionalText(500, "Máximo 500 caracteres"),
  pre_existing_damage: optionalText(500, "Máximo 500 caracteres"),
});
export type InspectionInput = z.infer<typeof inspectionSchema>;

/** `""` → `null`: el backend distingue "sin dato" de texto vacío. */
export const orNull = (s: string): string | null => (s.trim() === "" ? null : s.trim());
