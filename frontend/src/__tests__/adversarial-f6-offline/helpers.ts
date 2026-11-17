/* eslint-disable @typescript-eslint/no-require-imports */
/**
 * Utilidades del tester T3 (6.3). `loadApp()` carga una COPIA aislada de la app
 * offline (store + hook + React + RTL): cada copia tiene su propio candado de
 * flush a nivel de módulo y su propia memoria de zustand, pero comparten el
 * `localStorage` de jsdom. Sirve para simular "recargar la página" y "dos
 * pestañas".
 */
import { AxiosError, AxiosHeaders } from "axios";
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";
import type { OfflineHandlers } from "@/hooks/useOfflineSubmit";
import type { QueuedItem } from "@/stores/offlineQueueStore";

export const STORAGE_KEY = "carwash_offline_queue";

export const userA = {
  id: "u1",
  email: "a@example.com",
  role: "OWNER" as const,
  tenant_id: "t1",
  username: null,
  permission_profile_id: null,
};
export const userB = { ...userA, id: "u2", email: "b@example.com", role: "STAFF" as const };
export const userC = { ...userA, tenant_id: "t2" }; // mismo usuario, otro negocio

const cfg = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;

export function httpError(status: number, data: unknown = {}): AxiosError {
  const response = { status, data, statusText: "", headers: {}, config: cfg } as AxiosResponse;
  return new AxiosError(`HTTP ${status}`, "ERR_BAD_RESPONSE", cfg, null, response);
}
export const codeError = (status: number, code: string) =>
  httpError(status, { detail: { code, message: "x" } });
export const networkError = () => new AxiosError("Network Error", "ERR_NETWORK", cfg);

export function setOnline(value: boolean) {
  Object.defineProperty(navigator, "onLine", { configurable: true, get: () => value });
}

export function queued(id: string, over: Partial<QueuedItem> = {}): QueuedItem {
  return {
    id,
    userId: "u1",
    tenantId: "t1",
    kind: "k",
    payload: { n: id },
    createdAt: "2026-11-17T10:00:00.000Z",
    attempts: 0,
    ...over,
  };
}

export function deferred<T = unknown>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

// Jest toma todo lo que vive en `__tests__/` por suite; este archivo es solo utilidades.
it.skip("helpers.ts: utilidades, no es una suite", () => undefined);

const mounted: Array<{ unmount: () => void }> = [];
export function unmountAll() {
  while (mounted.length) mounted.pop()!.unmount();
}

export function loadApp() {
  process.env.RTL_SKIP_AUTO_CLEANUP = "true";
  let out!: {
    rtl: typeof import("@testing-library/react");
    hook: typeof import("@/hooks/useOfflineSubmit");
    store: typeof import("@/stores/offlineQueueStore");
    auth: typeof import("@/stores/authStore");
    status: typeof import("@/lib/useOfflineQueueStatus");
    React: typeof import("react");
    rq: typeof import("@tanstack/react-query");
  };
  jest.isolateModules(() => {
    out = {
      React: require("react"),
      rq: require("@tanstack/react-query"),
      rtl: require("@testing-library/react"),
      hook: require("@/hooks/useOfflineSubmit"),
      store: require("@/stores/offlineQueueStore"),
      auth: require("@/stores/authStore"),
      status: require("@/lib/useOfflineQueueStatus"),
    };
  });
  const { React, rq, rtl, hook, store, auth } = out;
  const qc = new rq.QueryClient();
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    React.createElement(rq.QueryClientProvider, { client: qc }, children);

  return {
    ...out,
    qc,
    useStore: store.useOfflineQueueStore,
    login(user: typeof userA | typeof userB | null) {
      auth.useAuthStore.setState({ user, tenant: user ? { id: user.tenant_id, name: "L" } : null });
    },
    mount(handlers: OfflineHandlers, autoSync = false) {
      const r = rtl.renderHook(() => hook.useOfflineSubmit({ handlers, autoSync }), { wrapper });
      mounted.push(r);
      return r;
    },
    mountStatus() {
      const r = rtl.renderHook(() => out.status.useOfflineQueueStatus(), { wrapper });
      mounted.push(r);
      return r;
    },
  };
}

/** Hora del toque con la que el hook invocó a `post` (3er argumento). */
export function occurredAtOf(call: readonly unknown[] | undefined): string | undefined {
  return (call?.[2] as { occurredAt?: string } | undefined)?.occurredAt;
}

export function resetBrowser() {
  localStorage.clear();
  setOnline(true);
  Reflect.deleteProperty(navigator, "locks");
}
