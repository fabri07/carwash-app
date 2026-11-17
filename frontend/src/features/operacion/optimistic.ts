import type { Job } from "./types";

/**
 * Cómo se ve el job mientras una acción espera en la cola offline: el estado que tendrá cuando se
 * sincronice, para poder seguir con el paso siguiente sin señal. Es solo de pantalla: la verdad
 * es la del servidor y reemplaza esto apenas se vuelve a leer el job.
 */
export type OptimisticAction =
  | { kind: "start"; at: string }
  | { kind: "finish"; at: string }
  | { kind: "pickUp"; at: string }
  | { kind: "cancelDelay"; at: string }
  | { kind: "pay"; at: string; amountCents: number };

export function applyOptimistic(job: Job, action: OptimisticAction): Job {
  switch (action.kind) {
    case "start":
      return { ...job, status: "EN_PROCESO", started_at: action.at };
    case "finish":
      return { ...job, status: "FINALIZADO", finished_at: action.at };
    case "pickUp":
      return { ...job, status: "RETIRADO", picked_up_at: action.at };
    case "cancelDelay":
      return { ...job, status: "CANCELADO_DEMORA" };
    case "pay": {
      const balance = Math.max(0, job.balance_due_cents - action.amountCents);
      return {
        ...job,
        paid_cents: job.paid_cents + action.amountCents,
        balance_due_cents: balance,
        status: balance === 0 && job.status === "FINALIZADO" ? "COBRADO" : job.status,
      };
    }
  }
}
