import { z } from "zod";

import { parseARS } from "@/lib/money";
import { addDays } from "@/lib/period";

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

/**
 * Un importe opcional escrito a mano: vacío es válido (el servidor usa el total o la seña del
 * negocio), pero un texto ilegible ("abc", "20.5", "-5", "1,234", "1e3") es un error del campo y
 * nunca se manda como si estuviera vacío (la trampa `parseMoney_` del legacy).
 */
function moneyIssue(text: string, path: string, ctx: z.RefinementCtx) {
  if (text.trim() === "") return;
  const cents = parseARS(text);
  if (cents === null) {
    ctx.addIssue({
      code: "custom",
      path: [path],
      message: "Importe no válido: escribilo como 20.000 o 20.000,50 (o dejalo vacío)",
    });
  } else if (cents <= 0) {
    ctx.addIssue({
      code: "custom",
      path: [path],
      message: "El importe tiene que ser mayor a cero",
    });
  }
}

const toMin = (t: string) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5));
/** Una carga que cruza la medianoche dura, como mucho, esto (si no, se pide el día de fin). */
const MAX_OVERNIGHT_MIN = 12 * 60;

/**
 * Día en que terminó el trabajo: el que se escribió, o el de inicio; y si la hora de fin es menor a
 * la de inicio y la duración resultante es razonable (hasta 12 h) se asume el día siguiente (la
 * pantalla lo avisa). Más allá de eso hay que elegir el día de fin: no se adivina.
 */
export function resolveEndDay(v: {
  date: string;
  start_time: string;
  end_time: string;
  end_date: string;
}): string {
  if (v.end_date) return v.end_date;
  if (
    v.end_time < v.start_time &&
    1440 - (toMin(v.start_time) - toMin(v.end_time)) <= MAX_OVERNIGHT_MIN
  ) {
    return addDays(v.date, 1);
  }
  return v.date;
}

/** Centavos de un importe ya validado; `undefined` si está vacío. */
export function moneyCents(text: string): number | undefined {
  return text.trim() === "" ? undefined : (parseARS(text) ?? undefined);
}

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
    /** Texto tal cual lo escribió: vacío = la seña que pide el negocio; ilegible = error (no se adivina). */
    deposit_text: z.string(),
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
    moneyIssue(v.deposit_text, "deposit_text", ctx);
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
    /** Día en que terminó, solo si no es el de inicio (el detailing puede durar días). Vacío = se infiere. */
    end_date: z.string(),
    paid: z.boolean(),
    payment_method_id: z.string(),
    /** `null` con cobro = el total del trabajo. */
    /** Texto tal cual lo escribió: vacío = el total; ilegible = error (nunca se manda como "el total"). */
    amount_text: z.string(),
  })
  .superRefine((v, ctx) => {
    if (v.end_date !== "" && !/^\d{4}-\d{2}-\d{2}$/.test(v.end_date)) {
      ctx.addIssue({ code: "custom", path: ["end_date"], message: "Elegí el día de fin" });
    } else if (
      /^\d{4}-\d{2}-\d{2}$/.test(v.date) &&
      /^\d{2}:\d{2}$/.test(v.start_time) &&
      /^\d{2}:\d{2}$/.test(v.end_time) &&
      `${resolveEndDay(v)}T${v.end_time}` < `${v.date}T${v.start_time}`
    ) {
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
    moneyIssue(v.amount_text, "amount_text", ctx);
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
