"use client";

import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { apiErrorCode } from "@/lib/errors";
import { operacionErrorMessage, type OperacionAction } from "./errors";
import { operacionKeys } from "./queries";
import { useOperacionSubmit, type OperacionKind, type Outcome } from "./useOperacionSubmit";

export interface Notice {
  tone: "error" | "info";
  text: string;
}

export const QUEUED_NOTICE =
  "Sin conexión: quedó guardado en el teléfono y se envía solo al volver la señal, con la hora de este toque.";

/**
 * Corre una acción operativa de a una: mientras hay una en vuelo los botones se deshabilitan (un
 * doble toque no manda dos claves). Traduce el resultado a un aviso en castellano; un 409 de estado
 * (`INVALID_TRANSITION`, `CONFLICT`) vuelve a leer el job y la agenda sin perder nada.
 *
 * El `occurred_at` NO se pone acá: lo pone quien llama, primero en su manejador de toque.
 */
export function useActionRunner() {
  const { run } = useOperacionSubmit();
  const qc = useQueryClient();
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);

  const exec = useCallback(
    async (
      kind: OperacionKind,
      action: OperacionAction,
      payload: { jobId?: string; paymentId?: string; body: unknown },
    ): Promise<Outcome | null> => {
      if (inFlight.current) return null;
      inFlight.current = true;
      setBusy(true);
      setNotice(null);
      try {
        const out = await run(kind, payload);
        if (out.status === "queued") setNotice({ tone: "info", text: QUEUED_NOTICE });
        if (out.status === "error") {
          setNotice({ tone: "error", text: operacionErrorMessage(out.error, action) });
          const code = apiErrorCode(out.error);
          if (code === "INVALID_TRANSITION" || code === "CONFLICT" || code === "NOT_FOUND") {
            void qc.invalidateQueries({ queryKey: operacionKeys.all });
          }
        }
        return out;
      } finally {
        inFlight.current = false;
        setBusy(false);
      }
    },
    [run, qc],
  );

  return { exec, busy, notice, setNotice };
}
