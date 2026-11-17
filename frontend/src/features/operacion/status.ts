import { STATUS_LABELS } from "@/features/turnos/queries";
import type { BookingStatusCode } from "@/types/api";
import type { DayEntry, JobStatus } from "./types";

/** Estados del job con texto y color: nunca solo color (se lee con sol y con daltonismo). */
export const JOB_STATUS: Record<JobStatus, { label: string; tone: string }> = {
  PRESENTE: { label: "En el local", tone: "border-amber-300 bg-amber-100 text-amber-900" },
  EN_PROCESO: { label: "En proceso", tone: "border-blue-300 bg-blue-100 text-blue-900" },
  FINALIZADO: { label: "Finalizado", tone: "border-violet-300 bg-violet-100 text-violet-900" },
  COBRADO: { label: "Cobrado", tone: "border-green-300 bg-green-100 text-green-900" },
  RETIRADO: { label: "Retirado", tone: "border-slate-300 bg-slate-100 text-slate-700" },
  CANCELADO_DEMORA: {
    label: "Cancelado por demora",
    tone: "border-red-300 bg-red-100 text-red-900",
  },
};

const BOOKING_TONE: Partial<Record<BookingStatusCode, string>> = {
  CONFIRMADO: "border-sky-300 bg-sky-100 text-sky-900",
  PENDIENTE_SEÑA: "border-orange-300 bg-orange-100 text-orange-900",
  PENDIENTE_COTIZACION: "border-orange-300 bg-orange-100 text-orange-900",
  NO_ASISTIO: "border-red-300 bg-red-100 text-red-900",
};

/** Etiqueta y color de un renglón de la agenda: el estado del job, o el del turno si aún no hay job. */
export function entryStatus(entry: DayEntry): { label: string; tone: string } {
  if (entry.job_status) return JOB_STATUS[entry.job_status];
  const code = entry.booking_status;
  return {
    label: code ? STATUS_LABELS[code] : "Turno",
    tone: (code && BOOKING_TONE[code]) || "border-slate-300 bg-slate-100 text-slate-700",
  };
}

/** ¿Debe plata? Solo un trabajo ya hecho (finalizado en adelante) con saldo; un turno futuro no "debe". */
export function owes(entry: DayEntry): boolean {
  return (
    entry.kind === "JOB" &&
    (entry.job_status === "FINALIZADO" ||
      entry.job_status === "COBRADO" ||
      entry.job_status === "RETIRADO") &&
    (entry.balance_due_cents ?? 0) > 0
  );
}
