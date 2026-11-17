"use client";

import { useCallback, useMemo, useRef } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";

import { useOfflineSubmit, type OfflineHandlers } from "@/hooks/useOfflineSubmit";
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

type Payload = { jobId?: string; paymentId?: string; body: never };

/** Resultado de un envío: guardado, encolado sin conexión, o rechazado (con el error original). */
export type Outcome =
  { status: "ok"; data?: unknown } | { status: "queued" } | { status: "error"; error: unknown };

const INVALIDATE = [operacionKeys.all, turnosKeys.all];

/** Quiénes son un `Job` completo: se guarda en el caché para no pedirlo de nuevo. */
function looksLikeJob(data: unknown): data is Job {
  return (
    typeof data === "object" && data !== null && "status" in data && "balance_due_cents" in data
  );
}

function buildHandlers(
  qc: QueryClient,
  onResult: (data: unknown) => void,
  onError: (e: unknown) => void,
): OfflineHandlers {
  const wrap =
    (call: (p: Payload, key: string) => Promise<unknown>) => async (raw: unknown, key: string) => {
      try {
        const data = await call(raw as Payload, key);
        onResult(data);
        if (looksLikeJob(data)) qc.setQueryData(operacionKeys.job(data.id), data);
        return data;
      } catch (e) {
        onError(e);
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
      { post: wrap(call), invalidate: INVALIDATE },
    ]),
  );
}

/**
 * `run(kind, payload)` manda una acción por la cola offline y resuelve con el resultado. Montarlo
 * con `autoSync` en la pantalla hace el flush al volver la señal. Un envío a la vez por pantalla:
 * los botones se deshabilitan mientras tanto (`busy` lo lleva quien llama).
 */
export function useOperacionSubmit() {
  const qc = useQueryClient();
  const lastError = useRef<unknown>(null);
  const lastData = useRef<unknown>(undefined);
  const handlers = useMemo(
    () =>
      buildHandlers(
        qc,
        (data) => {
          lastData.current = data;
        },
        (e) => {
          lastError.current = e;
        },
      ),
    [qc],
  );
  const { submit, flush } = useOfflineSubmit({ handlers, autoSync: true });

  const run = useCallback(
    (
      kind: OperacionKind,
      payload: { jobId?: string; paymentId?: string; body: unknown },
    ): Promise<Outcome> =>
      new Promise((resolve) => {
        lastError.current = null;
        lastData.current = undefined;
        void submit(kind, payload, {
          onSuccess: () => resolve({ status: "ok", data: lastData.current }),
          onQueued: () => resolve({ status: "queued" }),
          onError: () => resolve({ status: "error", error: lastError.current }),
        });
      }),
    [submit],
  );

  return { run, flush };
}
