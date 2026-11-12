import { z } from "zod";

import type { AgendaBlockPayload } from "@/types/api";

/**
 * Bloqueos y aperturas especiales (FASE-5-CONTRATO §3.3, PR 5.1). Se cargan en hora
 * local del negocio: un día completo o una franja de ese día, de un puesto o de
 * todos. Límites = los de `schemas/agenda.py` del backend.
 */
export const AGENDA_REASON_MAX = 160;

const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Elegí el día");
const clock = z.string().regex(/^(\d{2}:\d{2})?$/, "Poné la hora");

export const agendaRangeSchema = z
  .object({
    date: day,
    full_day: z.boolean(),
    start_time: clock,
    end_time: clock,
    /** `""` = todos los puestos. */
    resource_id: z.string(),
    reason: z.string().trim().max(AGENDA_REASON_MAX, `Máximo ${AGENDA_REASON_MAX} caracteres`),
  })
  .superRefine((v, ctx) => {
    if (v.full_day) return;
    if (!v.start_time) {
      ctx.addIssue({ code: "custom", path: ["start_time"], message: "Poné desde qué hora" });
    }
    if (!v.end_time) {
      ctx.addIssue({ code: "custom", path: ["end_time"], message: "Poné hasta qué hora" });
    }
    if (v.start_time && v.end_time && v.end_time <= v.start_time) {
      ctx.addIssue({
        code: "custom",
        path: ["end_time"],
        message: "Tiene que terminar después de empezar",
      });
    }
  });
export type AgendaRangeInput = z.infer<typeof agendaRangeSchema>;

/** Formulario → cuerpo de `POST /agenda/blocks` u `/agenda/openings`. */
export function toAgendaPayload(v: AgendaRangeInput): AgendaBlockPayload {
  return {
    date: v.date,
    ...(v.full_day ? {} : { start_time: v.start_time, end_time: v.end_time }),
    resource_id: v.resource_id === "" ? null : v.resource_id,
    ...(v.reason === "" ? {} : { reason: v.reason }),
  };
}
