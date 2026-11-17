/**
 * T3 6.3 — ataque 5 (401 con refresh de sesión) con el cliente axios REAL
 * (`lib/api`), un adaptador simulado y el hook de la cola. También verifica que
 * el vencimiento de sesión limpie las cachés del SW.
 */
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError, AxiosHeaders } from "axios";
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";
import { createElement, type ReactNode } from "react";

import { useOfflineSubmit } from "@/hooks/useOfflineSubmit";
import { api, authClient, browserNavigation, onSessionExpired } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { useOfflineQueueStore } from "@/stores/offlineQueueStore";
import { queued, userA } from "./helpers";

function respond(config: InternalAxiosRequestConfig, status: number, data: unknown = {}) {
  const response = { data, status, statusText: "", headers: {}, config } as AxiosResponse;
  if (status >= 400) {
    return Promise.reject(new AxiosError(`HTTP ${status}`, "ERR", config, null, response));
  }
  return Promise.resolve(response);
}

const seen: Array<{ url?: string; key?: string }> = [];
let apiStatuses: number[] = [];
let refreshStatus: number | "network" = 200;
let assignSpy: jest.SpyInstance;

beforeEach(() => {
  localStorage.clear();
  seen.length = 0;
  apiStatuses = [];
  refreshStatus = 200;
  useOfflineQueueStore.setState({ items: [], failed: [], conflicts: [] });
  useAuthStore.setState({ user: userA, tenant: { id: "t1", name: "L" } });
  api.defaults.adapter = (config) => {
    const h = AxiosHeaders.from(config.headers);
    seen.push({ url: config.url, key: h.get("Idempotency-Key") as string | undefined });
    return respond(config, apiStatuses.shift() ?? 200);
  };
  authClient.defaults.adapter = (config) =>
    refreshStatus === "network"
      ? Promise.reject(new AxiosError("Network Error", "ERR_NETWORK", config))
      : respond(config, refreshStatus);
  assignSpy = jest.spyOn(browserNavigation, "assign").mockImplementation(() => undefined);
});
afterEach(() => jest.restoreAllMocks());

function setup() {
  const qc = new QueryClient();
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children);
  const post = (_p: unknown, key: string) =>
    api.post(`/jobs/${key}/start`, {}, { headers: { "Idempotency-Key": key } });
  return renderHook(() => useOfflineSubmit({ handlers: { k: { post } } }), { wrapper });
}

describe("401 con refresh", () => {
  it("401 → refresh OK → reintento: el ítem se envía con la MISMA Idempotency-Key y sale de la cola", async () => {
    useOfflineQueueStore.getState().enqueue(queued("job-a"));
    apiStatuses = [401, 200];
    const { result } = setup();
    await act(() => result.current.flush());
    expect(seen.map((s) => s.key)).toEqual(["job-a", "job-a"]);
    expect(useOfflineQueueStore.getState().items).toEqual([]);
    expect(assignSpy).not.toHaveBeenCalled();
  });

  it("401 en el 1ro (refresh OK) no frena a los siguientes: se mandan los 3 en orden", async () => {
    for (const id of ["job-a", "job-b", "job-c"])
      useOfflineQueueStore.getState().enqueue(queued(id));
    apiStatuses = [401, 200, 200, 200];
    const { result } = setup();
    await act(() => result.current.flush());
    expect(seen.map((s) => s.key)).toEqual(["job-a", "job-a", "job-b", "job-c"]);
    expect(useOfflineQueueStore.getState().items).toEqual([]);
  });

  it.each([[401], ["network"] as const])(
    "refresh fallido (%s): sesión vencida, el vaciado corta, nada se pierde ni gasta intentos; al reloguear el mismo usuario se envía",
    async (refresh) => {
      for (const id of ["job-a", "job-b"]) useOfflineQueueStore.getState().enqueue(queued(id));
      apiStatuses = [401];
      refreshStatus = refresh as 401 | "network";
      const { result } = setup();
      await act(() => result.current.flush());
      expect(seen.map((s) => s.key)).toEqual(["job-a"]); // corta: job-b no se intenta
      const st = useOfflineQueueStore.getState();
      expect(st.items.map((i) => [i.id, i.attempts])).toEqual([
        ["job-a", 0],
        ["job-b", 0],
      ]);
      expect(st.failed).toEqual([]);
      expect(useAuthStore.getState().user).toBeNull(); // onSessionExpired limpió la sesión
      expect(assignSpy).toHaveBeenCalled();

      // sin usuario no se manda nada
      await act(() => result.current.flush());
      expect(seen).toHaveLength(1);

      // vuelve a entrar OTRO usuario: no se manda lo de A
      useAuthStore.setState({ user: { ...userA, id: "u9" }, tenant: { id: "t1", name: "L" } });
      await act(() => result.current.flush());
      expect(seen).toHaveLength(1);

      // vuelve a entrar A
      refreshStatus = 200;
      useAuthStore.setState({ user: userA, tenant: { id: "t1", name: "L" } });
      await act(() => result.current.flush());
      expect(seen.map((s) => s.key).slice(1)).toEqual(["job-a", "job-b"]);
      expect(useOfflineQueueStore.getState().items).toEqual([]);
    },
  );

  it("401 persistente tras un refresh 'exitoso' (cookie rota): un solo reintento, sin bucle", async () => {
    useOfflineQueueStore.getState().enqueue(queued("job-a"));
    apiStatuses = [401, 401, 401, 401];
    const { result } = setup();
    await act(() => result.current.flush());
    expect(seen).toHaveLength(2); // original + 1 reintento
    expect(useOfflineQueueStore.getState().items).toHaveLength(1);
  });

  it("submit en línea con 401 y refresh fallido: no se encola (onError) — el toque se pierde pero el usuario es redirigido", async () => {
    apiStatuses = [401];
    refreshStatus = 401;
    const { result } = setup();
    const cb = { onError: jest.fn(), onQueued: jest.fn() };
    await act(() => result.current.submit("k", {}, cb));
    expect(cb.onError).toHaveBeenCalled();
    expect(cb.onQueued).not.toHaveBeenCalled();
  });
});

describe("vencimiento de sesión y cachés del SW", () => {
  it("HALLAZGO: onSessionExpired limpia las cachés carwash-* del SW (como el logout), para que otro usuario no vea HTML del anterior", async () => {
    const deleted: string[] = [];
    (globalThis as unknown as { caches: unknown }).caches = {
      keys: async () => ["carwash-pages-v1", "carwash-static-v1", "otra-app"],
      delete: async (k: string) => {
        deleted.push(k);
        return true;
      },
    };
    onSessionExpired();
    await new Promise((r) => setTimeout(r, 0));
    expect(deleted).toContain("carwash-pages-v1");
    expect(deleted).not.toContain("otra-app");
    delete (globalThis as { caches?: unknown }).caches;
  });
});
