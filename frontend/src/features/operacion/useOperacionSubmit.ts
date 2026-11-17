"use client";

import axios from "axios";
import { useCallback, useMemo } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";

import {
  useOfflineSubmit,
  type OfflineHandlers,
  type OfflinePostMeta,
} from "@/hooks/useOfflineSubmit";
import { turnosKeys } from "@/features/turnos/queries";
import { operacionKeys } from "./queries";
import {
  cancelDelay,
  createManualBooking,
  finishJob,
  pickUpJob,
  receiveJob,
  recordInspection,
  recordPayment,
  recordRetrospective,
  startJob,
  voidPayment,
} from "./service";
import type { Job } from "./types";

/**
 * Cada acción operativa va por `useOfflineSubmit` (FASE-6-CONTRATO §4): el id del ítem de la cola ES
 * la `Idempotency-Key`, y un 409 duplicado cuenta como éxito. Este módulo solo registra los
 * handlers; la cola, el reintento y el flush son del hook, que no se toca.
 *
 * El `occurred_at` de cada acción va DENTRO del payload, puesto por quien llama en el momento del
 * toque: si la acción espera en la cola, sale con esa hora y no con la de la sincronización.
 */
export const KINDS = {
  receive: "operacion.receive",
  start: "operacion.start",
  finish: "operacion.finish",
  pickUp: "operacion.pick-up",
  cancelDelay: "operacion.cancel-delay",
  pay: "operacion.pay",
  void: "operacion.void",
  inspection: "operacion.inspection",
  retrospective: "operacion.retrospective",
  manualBooking: "operacion.manual-booking",
} as const;

export type OperacionKind = (typeof KINDS)[keyof typeof KINDS];

/**
 * Lo mínimo para reenviar (la cola guarda esto en claro en localStorage: nada extra). `occurred_at`
 * NO va acá: lo agrega el handler con la hora del toque que fija la cola (`meta.occurredAt`).
 */
type Payload = { jobId?: string; paymentId?: string; body: never };

/**
 * Resultado y error de CADA run, por la identidad del objeto `payload` que se le pasó a la cola (no
 * viaja ni se guarda nada extra). Un handler que ejecuta un flush en segundo plano trabaja con OTRO
 * payload y no puede pisar lo de un run en curso: LLEGÓ nunca redirige a un job ajeno.
 */
const runResults = new WeakMap<object, { data?: unknown; error?: unknown }>();

/** Resultado de un envío: guardado, encolado sin conexión, o rechazado (con el error original). */
export type Outcome =
  | { status: "ok"; data?: unknown }
  | { status: "queued"; httpError?: boolean }
  | { status: "error"; error: unknown };

const INVALIDATE = [operacionKeys.all, turnosKeys.all];

/** Quiénes son un `Job` completo: se guarda en el caché para no pedirlo de nuevo. */
function looksLikeJob(data: unknown): data is Job {
  return (
    typeof data === "object" && data !== null && "status" in data && "balance_due_cents" in data
  );
}

function buildHandlers(qc: QueryClient): OfflineHandlers {
  const wrap =
    (call: (p: Payload, key: string) => Promise<unknown>, withTime: boolean) =>
    async (raw: unknown, key: string, meta?: OfflinePostMeta) => {
      try {
        const p = raw as Payload;
        const at = meta?.occurredAt ?? new Date().toISOString();
        const body = (withTime ? { ...(p.body as object), occurred_at: at } : p.body) as never;
        const data = await call({ ...p, body }, key);
        const result = runResults.get(raw as object);
        if (result) result.data = data;
        if (looksLikeJob(data)) qc.setQueryData(operacionKeys.job(data.id), data);
        return data;
      } catch (e) {
        const result = runResults.get(raw as object);
        if (result) result.error = e;
        throw e;
      }
    };
  const job = (p: Payload) => p.jobId as string;
  const defs: Record<OperacionKind, (p: Payload, key: string) => Promise<unknown>> = {
    [KINDS.receive]: (p, k) => receiveJob(p.body, k),
    [KINDS.start]: (p, k) => startJob(job(p), p.body, k),
    [KINDS.finish]: (p, k) => finishJob(job(p), p.body, k),
    [KINDS.pickUp]: (p, k) => pickUpJob(job(p), p.body, k),
    [KINDS.cancelDelay]: (p, k) => cancelDelay(job(p), p.body, k),
    [KINDS.pay]: (p, k) => recordPayment(job(p), p.body, k),
    [KINDS.void]: (p, k) => voidPayment(job(p), p.paymentId as string, p.body, k),
    [KINDS.inspection]: (p, k) => recordInspection(job(p), p.body, k),
    [KINDS.retrospective]: (p, k) => recordRetrospective(p.body, k),
    [KINDS.manualBooking]: (p, k) => createManualBooking(p.body, k),
  };
  return Object.fromEntries(
    Object.entries(defs).map(([kind, call]) => [
      kind,
      {
        post: wrap(call, kind !== KINDS.retrospective && kind !== KINDS.manualBooking),
        invalidate: INVALIDATE,
      },
    ]),
  );
}

/**
 * `run(kind, payload, dedupeKey)` manda una acción por la cola offline y resuelve con el resultado. Montarlo
 * con `autoSync` en la pantalla hace el flush al volver la señal. Un envío a la vez por pantalla:
 * los botones se deshabilitan mientras tanto. `dedupeKey` es `acción:destino` (`iniciar:<jobId>`): un
 * toque repetido no se manda dos veces y las acciones de un mismo job salen en orden. Un toque
 * repetido en vuelo resuelve como `queued` (no deja el botón colgado).
 */
export function useOperacionSubmit() {
  const qc = useQueryClient();
  const handlers = useMemo(() => buildHandlers(qc), [qc]);
  const { submit, flush } = useOfflineSubmit({ handlers, autoSync: true });

  const run = useCallback(
    (
      kind: OperacionKind,
      payload: { jobId?: string; paymentId?: string; body: unknown },
      dedupeKey: string,
    ): Promise<Outcome> =>
      new Promise((resolve) => {
        const result: { data?: unknown; error?: unknown } = {};
        const owned = { ...payload };
        runResults.set(owned, result);
        const done = (outcome: Outcome) => {
          runResults.delete(owned);
          resolve(outcome);
        };
        void submit(
          kind,
          owned,
          {
            onSuccess: () => done({ status: "ok", data: result.data }),
            onQueued: () =>
              done({
                status: "queued",
                // Un 5xx guardó el toque en la cola, pero NO es falta de señal: se dice distinto.
                httpError: axios.isAxiosError(result.error) && Boolean(result.error.response),
              }),
            onError: () => done({ status: "error", error: result.error }),
            // 409 INVALID_TRANSITION en línea: se muestra como error (el estado cambió).
            onConflict: () => done({ status: "error", error: result.error }),
            dedupeKey,
          },
        );
      }),
    [submit],
  );

  return { run, flush };
}
