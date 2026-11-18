import type { Job } from "./types";

/**
 * Cómo se ve el job mientras una acción espera en la cola offline: el estado que tendrá cuando se
 * sincronice, para poder seguir con el paso siguiente sin señal. Es solo de pantalla: la verdad
 * es la del servidor, y esto se deriva de la cola (`applyPending`), no de un estado local.
 */

/** Los cobros que todavía esperan en la cola llevan este prefijo de id: no se pueden anular. */
export const PENDING_PREFIX = "pending-";

export type OptimisticAction =
  | { kind: "start"; at: string }
  | { kind: "finish"; at: string }
  | { kind: "pickUp"; at: string }
  | { kind: "cancelDelay"; at: string }
  | { kind: "pay"; at: string; amountCents: number; id?: string };

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
        // El cobro pendiente cuenta para la clave del siguiente (`cobrar#<n>`): dos cobros
        // legítimos sin señal no se pisan, pero un doble toque sobre la misma vista sí se deduplica.
        payments: [
          ...job.payments,
          {
            id: `${PENDING_PREFIX}${action.id ?? action.at}`,
            kind: "SALDO" as const,
            amount_cents: action.amountCents,
            occurred_at: action.at,
            payment_method_id: "",
            voided: false,
          },
        ],
        status: balance === 0 && job.status === "FINALIZADO" ? "COBRADO" : job.status,
      };
    }
  }
}

/** Ítem de la cola, en lo que importa para mostrar el estado pendiente. */
export interface PendingItem {
  id: string;
  kind: string;
  occurredAt?: string;
  createdAt: string;
  payload: unknown;
}

/**
 * El job tal como quedará cuando se sincronice la cola: el del servidor más cada acción pendiente
 * de ESTE job, en orden. Mientras la acción siga pendiente se sigue viendo, aunque un refetch traiga
 * datos viejos, y desaparece sola cuando el ítem sale de la cola. Una acción que ya no corresponde
 * al estado (otro dispositivo se adelantó) no cambia nada. `voidPending` son los cobros con
 * anulación pendiente.
 */
export function applyPending(
  server: Job,
  items: PendingItem[],
): { job: Job; voidPending: Set<string> } {
  let job = server;
  const voidPending = new Set<string>();
  for (const item of items) {
    const at = item.occurredAt ?? item.createdAt;
    const payload = item.payload as { body?: { amount_cents?: number }; paymentId?: string };
    switch (item.kind) {
      case "operacion.start":
        if (job.status === "PRESENTE") job = applyOptimistic(job, { kind: "start", at });
        break;
      case "operacion.finish":
        if (job.status === "EN_PROCESO") job = applyOptimistic(job, { kind: "finish", at });
        break;
      case "operacion.pick-up":
        if (job.status === "FINALIZADO" || job.status === "COBRADO") {
          job = applyOptimistic(job, { kind: "pickUp", at });
        }
        break;
      case "operacion.cancel-delay":
        if (job.status === "PRESENTE") job = applyOptimistic(job, { kind: "cancelDelay", at });
        break;
      case "operacion.pay":
        if (job.balance_due_cents > 0) {
          const amountCents = payload.body?.amount_cents ?? job.balance_due_cents;
          job = applyOptimistic(job, { kind: "pay", at, amountCents, id: item.id });
        }
        break;
      case "operacion.void":
        if (payload.paymentId) voidPending.add(payload.paymentId);
        break;
    }
  }
  return { job, voidPending };
}
