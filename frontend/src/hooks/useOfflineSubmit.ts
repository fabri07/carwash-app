"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import axios from "axios";
import { useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";

import { useAuthStore } from "@/stores/authStore";
import {
  itemsOwnedBy,
  useOfflineQueueStore,
  type FailedItem,
  type QueuedKind,
} from "@/stores/offlineQueueStore";
import type { ApiError, ApiErrorCode } from "@/types/api";

/**
 * Cómo se manda una carga de un tipo dado. Reemplaza al `postByKind` y al
 * `INVALIDATE_KEYS` cableados al dominio de Véktor: el hook no conoce ningún
 * servicio; el consumidor (Fase 6) registra los suyos.
 *
 * `post` DEBE mandar `idempotencyKey` como header `Idempotency-Key`: es lo que
 * vuelve seguro reintentar el mismo item.
 */
export interface OfflineHandler {
  post: (payload: unknown, idempotencyKey: string) => Promise<unknown>;
  invalidate?: QueryKey[];
}

export type OfflineHandlers = Record<QueuedKind, OfflineHandler>;

export interface SubmitCallbacks {
  onSuccess?: () => void; // entró al servidor (o ya estaba: idempotente)
  onQueued?: () => void; // sin conexión → encolado, se sincroniza al volver online
  onError?: () => void; // rechazo del servidor (4xx) o sin sesión: la carga NO quedó guardada
}

/**
 * Tope de rechazos PERMANENTES (4xx) antes de sacar un ítem de la cola (evita
 * el ítem veneno que bloquea para siempre). Las fallas transitorias no cuentan:
 * con Railway caído y señal intermitente, 50 flush seguidos con 5xx no pueden
 * costar un lavado que el usuario cree guardado. Y al agotarse, el ítem no se
 * borra: pasa a `failed`, visible.
 */
export const MAX_FLUSH_ATTEMPTS = 5;

/** Estados que dicen "ahora no", no "nunca": 408 timeout, 425 too early, 429 rate limit. */
const TRANSIENT_4XX = new Set([408, 425, 429]);

/**
 * Candado de flush a nivel de módulo, no por instancia: con dos componentes
 * montados (o un flush manual junto al listener `online`) dos flush en paralelo
 * mandarían el mismo ítem dos veces y gastarían dos intentos por un solo 4xx.
 */
let flushing = false;

export type FlushErrorClass = "duplicate" | "transient" | "unauthenticated" | "permanent";

/**
 * Qué hacer con un error de flush:
 * - `duplicate`: 409 DUPLICATE_IDEMPOTENT → ya estaba guardado, éxito.
 * - `transient`: red/timeout (sin respuesta), 5xx, 408/425/429 → corta el
 *   flush, el ítem queda y no gasta intento.
 * - `unauthenticated`: 401 (su refresh ya falló en el interceptor) → corta,
 *   queda hasta que haya sesión válida del mismo usuario y tenant.
 * - `permanent`: el resto de los 4xx (400, 403, 404, 409 no idempotente, 422…)
 *   → gasta un intento; al tope pasa a `failed`.
 * Un error que no es HTTP (bug del handler) se trata como transitorio: ante
 * la duda, no se pierde la carga.
 */
export function classifyFlushError(e: unknown): FlushErrorClass {
  if (isDuplicate(e)) return "duplicate";
  if (!axios.isAxiosError(e) || !e.response) return "transient";
  const { status } = e.response;
  if (status === 401) return "unauthenticated";
  if (status >= 500 || TRANSIENT_4XX.has(status)) return "transient";
  if (status >= 400) return "permanent";
  return "transient";
}

// Error de red real: axios sin `response` (timeout/abort/DNS/CORS/offline).
export function isNetworkError(e: unknown): boolean {
  return axios.isAxiosError(e) && !e.response;
}

// Error de cliente (4xx): permanente — reintentar el mismo payload no lo va a arreglar.
export function isClientError(e: unknown): boolean {
  const status = axios.isAxiosError(e) ? e.response?.status : undefined;
  return status !== undefined && status >= 400 && status < 500;
}

/**
 * Replay idempotente: el backend ya tiene el registro → 409 con el código del
 * contrato. Se trata como ÉXITO: la carga está guardada, que es lo único que
 * le importa a quien la hizo. La forma sale de `ErrorResponse` del OpenAPI
 * (`{detail: {code}}`) y el literal está tipado contra `ErrorCode`.
 */
const DUPLICATE: ApiErrorCode = "DUPLICATE_IDEMPOTENT";

export function isDuplicate(e: unknown): boolean {
  if (!axios.isAxiosError(e) || e.response?.status !== 409) return false;
  const data = e.response.data as Partial<ApiError> | undefined;
  return data?.detail?.code === DUPLICATE;
}

// Mensaje legible del error para guardarlo en el item de la cola (`lastError`).
export function errorMessage(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const status = e.response?.status;
    return status ? `Error ${status} del servidor` : "Sin conexión al sincronizar";
  }
  return "Error desconocido";
}

function invalidate(qc: QueryClient, handler: OfflineHandler) {
  for (const queryKey of handler.invalidate ?? []) {
    void qc.invalidateQueries({ queryKey });
  }
}

/** Dueño de la sesión actual, o `null` si no hay usuario. */
export function currentOwner(): { userId: string; tenantId: string } | null {
  const user = useAuthStore.getState().user;
  return user ? { userId: user.id, tenantId: user.tenant_id } : null;
}

/** Cargas pendientes del usuario y tenant actuales. */
export function pendingForCurrentUser() {
  return itemsOwnedBy(useOfflineQueueStore.getState().items, currentOwner());
}

/** Cargas que agotaron los intentos por 4xx permanente, del usuario actual. */
export function useFailedOfflineItems(): FailedItem[] {
  const user = useAuthStore((s) => s.user);
  const failed = useOfflineQueueStore((s) => s.failed);
  return itemsOwnedBy(failed, user ? { userId: user.id, tenantId: user.tenant_id } : null);
}

/** Cantidad de cargas pendientes del usuario actual (para un badge). */
export function useOfflineQueueCount(): number {
  const user = useAuthStore((s) => s.user);
  return useOfflineQueueStore(
    (s) =>
      itemsOwnedBy(s.items, user ? { userId: user.id, tenantId: user.tenant_id } : null).length,
  );
}

/** Estado de conexión reactivo (SSR-safe: arranca online y se corrige al montar). */
export function useOnlineStatus(): boolean {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    setOnline(navigator.onLine);
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);
  return online;
}

/**
 * Submit resiliente a cortes de internet. `submit` intenta el POST con una
 * Idempotency-Key (el id del item); si falla por red, encola y sincroniza al
 * volver online. `autoSync: true` (montado una vez por página) registra el
 * listener `online` y dispara el flush inicial.
 */
export function useOfflineSubmit(opts: {
  handlers: OfflineHandlers;
  autoSync?: boolean;
  /** Un ítem salió de la cola por rechazos permanentes (queda en `failed`). */
  onItemFailed?: (item: FailedItem) => void;
}) {
  const queryClient = useQueryClient();
  const handlersRef = useRef(opts.handlers);
  handlersRef.current = opts.handlers;
  const onItemFailedRef = useRef(opts.onItemFailed);
  onItemFailedRef.current = opts.onItemFailed;

  const flush = useCallback(async () => {
    if (flushing) return;
    if (typeof navigator !== "undefined" && !navigator.onLine) return;
    // Solo lo del usuario y tenant de la sesión actual (tablet compartida).
    const items = pendingForCurrentUser();
    if (items.length === 0) return;
    flushing = true;
    const queue = useOfflineQueueStore.getState;
    try {
      for (const item of [...items]) {
        const handler = handlersRef.current[item.kind];
        // Sin handler en este montaje (otra pantalla lo encoló): se deja para
        // quien sí sepa mandarlo. No se descarta ni se gasta intento.
        if (!handler) continue;
        try {
          await handler.post(item.payload, item.id);
          queue().remove(item.id);
          invalidate(queryClient, handler);
        } catch (e) {
          const kind = classifyFlushError(e);
          if (kind === "duplicate") {
            queue().remove(item.id);
            invalidate(queryClient, handler);
          } else if (kind === "transient") {
            // Red caída, backend caído o sobrecargado: los ítems siguientes
            // fallarían igual. Se anota el motivo, no se gasta intento y se
            // corta; el próximo flush (online, montaje) reintenta.
            queue().noteTransient(item.id, errorMessage(e));
            if (axios.isAxiosError(e)) break;
          } else if (kind === "unauthenticated") {
            // El interceptor ya intentó el refresh y falló: sin sesión no
            // tiene sentido seguir. El ítem queda atado a su dueño.
            queue().noteTransient(item.id, "Sesión vencida: se enviará al volver a entrar");
            break;
          } else if (item.attempts + 1 >= MAX_FLUSH_ATTEMPTS) {
            const status = axios.isAxiosError(e) ? e.response?.status : undefined;
            queue().moveToFailed(item.id, errorMessage(e), status);
            const failed = queue().failed.find((f) => f.id === item.id);
            if (failed) onItemFailedRef.current?.(failed);
          } else {
            queue().markFailed(item.id, errorMessage(e));
          }
        }
      }
    } finally {
      flushing = false;
    }
  }, [queryClient]);

  const submit = useCallback(
    async (kind: QueuedKind, payload: unknown, cb?: SubmitCallbacks): Promise<void> => {
      const handler = handlersRef.current[kind];
      if (!handler) throw new Error(`useOfflineSubmit: no handler registered for kind "${kind}"`);
      const owner = currentOwner();
      // Sin sesión no hay a quién atribuirle la carga si hubiera que encolarla.
      if (!owner) {
        cb?.onError?.();
        return;
      }
      const id = crypto.randomUUID();
      try {
        await handler.post(payload, id);
        invalidate(queryClient, handler);
        cb?.onSuccess?.();
      } catch (e) {
        if (isDuplicate(e)) {
          invalidate(queryClient, handler);
          cb?.onSuccess?.();
        } else if (
          isNetworkError(e) ||
          (axios.isAxiosError(e) && classifyFlushError(e) === "transient")
        ) {
          // Red caída o backend caído/sobrecargado (5xx, 408, 425, 429): la
          // carga se encola con la misma Idempotency-Key, así que si el
          // servidor llegó a guardarla, el reintento vuelve como duplicado.
          // Persistencia best-effort: si encolar falla (storage lleno o purgado
          // por iOS), NO se confirma como guardado → onError, el form no se
          // resetea y la carga no se pierde.
          try {
            useOfflineQueueStore.getState().enqueue({
              id,
              ...owner,
              kind,
              payload,
              createdAt: new Date().toISOString(),
              attempts: 0,
            });
            cb?.onQueued?.();
          } catch {
            // zustand actualiza la memoria ANTES de escribir el storage: si el
            // storage tiró, el ítem quedó en la cola en memoria. Hay que sacarlo,
            // o el flush lo mandaría con esta key mientras el usuario reintenta
            // con otra, y el lavado se registraría dos veces.
            try {
              useOfflineQueueStore.getState().remove(id);
            } catch {
              // El remove también escribe el storage; la memoria ya quedó limpia.
            }
            cb?.onError?.();
          }
        } else {
          cb?.onError?.();
        }
      }
    },
    [queryClient],
  );

  useEffect(() => {
    if (!opts.autoSync) return;
    void flush();
    function onOnline() {
      void flush();
    }
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  }, [opts.autoSync, flush]);

  return { submit, flush };
}
