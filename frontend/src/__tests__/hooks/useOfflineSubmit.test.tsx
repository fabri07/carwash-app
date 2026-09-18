import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  AxiosError,
  AxiosHeaders,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";

import {
  MAX_FLUSH_ATTEMPTS,
  errorMessage,
  isClientError,
  isDuplicate,
  isNetworkError,
  classifyFlushError,
  useFailedOfflineItems,
  useOfflineQueueCount,
  useOfflineSubmit,
  useOnlineStatus,
  type OfflineHandlers,
} from "@/hooks/useOfflineSubmit";
import { useAuthStore } from "@/stores/authStore";
import { useOfflineQueueStore, type QueuedItem } from "@/stores/offlineQueueStore";

const userA = { id: "u1", email: "a@x.com", role: "OWNER" as const, tenant_id: "t1" };
const userB = { id: "u2", email: "b@x.com", role: "STAFF" as const, tenant_id: "t1" };

const config = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;

function httpError(status: number, data: unknown = {}): AxiosError {
  const response = { status, data, statusText: "", headers: {}, config } as AxiosResponse;
  return new AxiosError(`HTTP ${status}`, "ERR_BAD_RESPONSE", config, null, response);
}
const networkError = () => new AxiosError("Network Error", "ERR_NETWORK", config);
const duplicate = () =>
  httpError(409, { detail: { code: "DUPLICATE_IDEMPOTENT", message: "already used" } });

function item(id: string, over: Partial<QueuedItem> = {}): QueuedItem {
  return {
    id,
    userId: "u1",
    tenantId: "t1",
    kind: "dummy",
    payload: { n: id },
    createdAt: "2026-01-01T00:00:00Z",
    attempts: 0,
    ...over,
  };
}

function setup(handlers: OfflineHandlers, autoSync = false) {
  const qc = new QueryClient();
  const invalidate = jest.spyOn(qc, "invalidateQueries");
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  const hook = renderHook(() => useOfflineSubmit({ handlers, autoSync }), { wrapper });
  return { ...hook, invalidate };
}

beforeEach(() => {
  useAuthStore.setState({ user: userA, tenant: { id: "t1", name: "L" } });
});

function setOnline(value: boolean) {
  Object.defineProperty(navigator, "onLine", { configurable: true, get: () => value });
}

describe("clasificación de errores", () => {
  it("red vs 4xx vs duplicado", () => {
    expect(isNetworkError(networkError())).toBe(true);
    expect(isNetworkError(httpError(500))).toBe(false);
    expect(isNetworkError(new Error("x"))).toBe(false);
    expect(isClientError(httpError(422))).toBe(true);
    expect(isClientError(httpError(503))).toBe(false);
    expect(isClientError(networkError())).toBe(false);
    expect(isDuplicate(duplicate())).toBe(true);
    // Un 409 que no es el replay idempotente NO es éxito.
    expect(isDuplicate(httpError(409, { detail: { code: "CONFLICT", message: "x" } }))).toBe(false);
    expect(isDuplicate(httpError(409))).toBe(false);
    expect(isDuplicate(new Error("x"))).toBe(false);
  });

  it("classifyFlushError", () => {
    expect(classifyFlushError(duplicate())).toBe("duplicate");
    expect(classifyFlushError(networkError())).toBe("transient");
    expect(classifyFlushError(httpError(503))).toBe("transient");
    expect(classifyFlushError(httpError(429))).toBe("transient");
    expect(classifyFlushError(httpError(401))).toBe("unauthenticated");
    expect(classifyFlushError(httpError(422))).toBe("permanent");
    expect(classifyFlushError(httpError(302))).toBe("transient");
    expect(classifyFlushError(new Error("x"))).toBe("transient");
  });

  it("mensajes legibles", () => {
    expect(errorMessage(httpError(500))).toBe("Error 500 del servidor");
    expect(errorMessage(networkError())).toBe("Sin conexión al sincronizar");
    expect(errorMessage("x")).toBe("Error desconocido");
  });
});

describe("useOfflineSubmit.submit", () => {
  beforeEach(() => {
    useOfflineQueueStore.setState({ items: [] });
    setOnline(true);
  });

  it("éxito: usa un UUID como Idempotency-Key, invalida y avisa onSuccess", async () => {
    const post = jest.fn(async () => ({}));
    const { result, invalidate } = setup({ dummy: { post, invalidate: [["dummy"]] } });
    const cb = { onSuccess: jest.fn(), onQueued: jest.fn(), onError: jest.fn() };
    await act(() => result.current.submit("dummy", { a: 1 }, cb));
    expect(post).toHaveBeenCalledWith({ a: 1 }, expect.stringMatching(/^[0-9a-f-]{36}$/));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["dummy"] });
    expect(cb.onSuccess).toHaveBeenCalled();
    expect(useOfflineQueueStore.getState().items).toHaveLength(0);
  });

  it("409 DUPLICATE_IDEMPOTENT se trata como éxito", async () => {
    const post = jest.fn(async () => Promise.reject(duplicate()));
    const { result } = setup({ dummy: { post } });
    const cb = { onSuccess: jest.fn(), onError: jest.fn() };
    await act(() => result.current.submit("dummy", {}, cb));
    expect(cb.onSuccess).toHaveBeenCalled();
    expect(cb.onError).not.toHaveBeenCalled();
  });

  it("sin red: encola con el MISMO id que se usó como Idempotency-Key", async () => {
    const post = jest.fn(async () => Promise.reject(networkError()));
    const { result } = setup({ dummy: { post } });
    const cb = { onQueued: jest.fn() };
    await act(() => result.current.submit("dummy", { a: 1 }, cb));
    const [queued] = useOfflineQueueStore.getState().items;
    expect(cb.onQueued).toHaveBeenCalled();
    expect(queued).toMatchObject({
      kind: "dummy",
      payload: { a: 1 },
      attempts: 0,
      userId: "u1",
      tenantId: "t1",
    });
    expect(queued!.id).toBe((post.mock.calls[0] as unknown[])[1]);
  });

  it("si encolar falla (storage lleno), NO confirma: onError", async () => {
    const post = jest.fn(async () => Promise.reject(networkError()));
    const { result } = setup({ dummy: { post } });
    const enqueue = useOfflineQueueStore.getState().enqueue;
    useOfflineQueueStore.setState({
      enqueue: () => {
        throw new Error("QuotaExceeded");
      },
    });
    const cb = { onQueued: jest.fn(), onError: jest.fn() };
    await act(() => result.current.submit("dummy", {}, cb));
    expect(cb.onError).toHaveBeenCalled();
    expect(cb.onQueued).not.toHaveBeenCalled();
    useOfflineQueueStore.setState({ enqueue });
  });

  it("si el storage tira después de actualizar la memoria, el ítem no queda en la cola", async () => {
    const post = jest.fn(async () => Promise.reject(networkError()));
    const { result } = setup({ dummy: { post } });
    const setItem = jest.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("QuotaExceededError");
    });
    const cb = { onQueued: jest.fn(), onError: jest.fn() };
    try {
      await act(() => result.current.submit("dummy", {}, cb));
    } finally {
      setItem.mockRestore();
    }
    expect(cb.onError).toHaveBeenCalled();
    expect(cb.onQueued).not.toHaveBeenCalled();
    // Si quedara, el flush lo mandaría con una key distinta a la del reintento.
    expect(useOfflineQueueStore.getState().items).toHaveLength(0);
  });

  it("un 4xx en submit es error (no se encola)", async () => {
    for (const status of [400, 401, 404, 422]) {
      const post = jest.fn(async () => Promise.reject(httpError(status)));
      const { result, unmount } = setup({ dummy: { post } });
      const cb = { onError: jest.fn(), onQueued: jest.fn() };
      await act(() => result.current.submit("dummy", {}, cb));
      expect(cb.onError).toHaveBeenCalled();
      expect(cb.onQueued).not.toHaveBeenCalled();
      unmount();
    }
    expect(useOfflineQueueStore.getState().items).toHaveLength(0);
  });

  it("un 5xx, 408 o 429 en submit encola (backend caído no pierde la carga)", async () => {
    for (const status of [500, 503, 408, 429]) {
      const post = jest.fn(async () => Promise.reject(httpError(status)));
      const { result, unmount } = setup({ dummy: { post } });
      const cb = { onQueued: jest.fn(), onError: jest.fn() };
      await act(() => result.current.submit("dummy", {}, cb));
      expect(cb.onQueued).toHaveBeenCalled();
      expect(cb.onError).not.toHaveBeenCalled();
      unmount();
    }
    expect(useOfflineQueueStore.getState().items).toHaveLength(4);
  });

  it("un error que no es HTTP en submit es onError y no se encola", async () => {
    const post = jest.fn(async () => Promise.reject(new TypeError("boom")));
    const { result } = setup({ dummy: { post } });
    const cb = { onError: jest.fn(), onQueued: jest.fn(), onSuccess: jest.fn() };
    await act(() => result.current.submit("dummy", {}, cb));
    expect(cb.onError).toHaveBeenCalled();
    expect(cb.onQueued).not.toHaveBeenCalled();
    expect(useOfflineQueueStore.getState().items).toHaveLength(0);
  });

  it("sin sesión no manda ni encola: onError", async () => {
    useAuthStore.setState({ user: null });
    const post = jest.fn(async () => Promise.reject(networkError()));
    const { result } = setup({ dummy: { post } });
    const cb = { onError: jest.fn(), onQueued: jest.fn() };
    await act(() => result.current.submit("dummy", {}, cb));
    expect(post).not.toHaveBeenCalled();
    expect(cb.onError).toHaveBeenCalled();
    expect(useOfflineQueueStore.getState().items).toHaveLength(0);
  });

  it("un kind sin handler es un error de programación", async () => {
    const { result } = setup({});
    await expect(result.current.submit("nada", {})).rejects.toThrow(/no handler/);
  });
});

describe("useOfflineSubmit.flush", () => {
  beforeEach(() => {
    useOfflineQueueStore.setState({ items: [] });
    setOnline(true);
  });

  it("manda cada item con su id como Idempotency-Key y lo saca de la cola", async () => {
    useOfflineQueueStore.setState({ items: [item("a"), item("b")] });
    const post = jest.fn(async () => ({}));
    const { result } = setup({ dummy: { post } });
    await act(() => result.current.flush());
    expect(post.mock.calls.map((c) => (c as unknown[])[1])).toEqual(["a", "b"]);
    expect(useOfflineQueueStore.getState().items).toEqual([]);
  });

  it("409 duplicado en flush = éxito: se saca de la cola", async () => {
    useOfflineQueueStore.setState({ items: [item("a")] });
    const { result } = setup({ dummy: { post: async () => Promise.reject(duplicate()) } });
    await act(() => result.current.flush());
    expect(useOfflineQueueStore.getState().items).toEqual([]);
  });

  it("error de red corta el loop sin gastar intento", async () => {
    useOfflineQueueStore.setState({ items: [item("a"), item("b")] });
    const post = jest.fn(async () => Promise.reject(networkError()));
    const { result } = setup({ dummy: { post } });
    await act(() => result.current.flush());
    expect(post).toHaveBeenCalledTimes(1);
    const [a, b] = useOfflineQueueStore.getState().items;
    expect(a).toMatchObject({ attempts: 0, lastError: "Sin conexión al sincronizar" });
    expect(b).toMatchObject({ attempts: 0 });
  });

  it.each([500, 502, 503, 504, 408, 425, 429])(
    "un %i es transitorio: corta el flush y no gasta intento",
    async (status) => {
      useOfflineQueueStore.setState({ items: [item("a"), item("b")] });
      const post = jest.fn(async () => Promise.reject(httpError(status)));
      const { result } = setup({ dummy: { post } });
      await act(() => result.current.flush());
      expect(post).toHaveBeenCalledTimes(1);
      expect(useOfflineQueueStore.getState().items).toEqual([
        expect.objectContaining({
          id: "a",
          attempts: 0,
          lastError: `Error ${status} del servidor`,
        }),
        expect.objectContaining({ id: "b", attempts: 0 }),
      ]);
    },
  );

  it("10 caídas 5xx seguidas no borran el ítem (ni con los intentos ya al borde)", async () => {
    useOfflineQueueStore.setState({
      items: [item("lavado", { attempts: MAX_FLUSH_ATTEMPTS - 1 })],
    });
    const post = jest.fn(async () => Promise.reject(httpError(503)));
    const { result } = setup({ dummy: { post } });
    for (let i = 0; i < 10; i++) {
      await act(() => result.current.flush());
    }
    expect(post).toHaveBeenCalledTimes(10);
    expect(useOfflineQueueStore.getState().items).toEqual([
      expect.objectContaining({ id: "lavado", attempts: MAX_FLUSH_ATTEMPTS - 1 }),
    ]);
    expect(useOfflineQueueStore.getState().failed).toEqual([]);
  });

  it("20 timeouts seguidos no borran el ítem", async () => {
    useOfflineQueueStore.setState({ items: [item("lavado")] });
    const timeout = () => new AxiosError("timeout", "ECONNABORTED", config);
    const { result } = setup({ dummy: { post: async () => Promise.reject(timeout()) } });
    for (let i = 0; i < 20; i++) {
      await act(() => result.current.flush());
    }
    expect(useOfflineQueueStore.getState().items).toEqual([
      expect.objectContaining({ id: "lavado", attempts: 0 }),
    ]);
  });

  it("401 (refresh fallido) corta, no gasta intento y el ítem espera a su dueño", async () => {
    useOfflineQueueStore.setState({ items: [item("a"), item("b")] });
    const post = jest.fn(async (): Promise<unknown> => Promise.reject(httpError(401)));
    const { result } = setup({ dummy: { post } });
    for (let i = 0; i < 6; i++) {
      await act(() => result.current.flush());
    }
    expect(post).toHaveBeenCalledTimes(6);
    expect(useOfflineQueueStore.getState().items).toEqual([
      expect.objectContaining({ id: "a", attempts: 0, lastError: expect.stringMatching(/Sesión/) }),
      expect.objectContaining({ id: "b", attempts: 0 }),
    ]);
    // Sin sesión no se intenta; con otro usuario tampoco; vuelve A y se manda.
    useAuthStore.setState({ user: null });
    post.mockClear();
    await act(() => result.current.flush());
    useAuthStore.setState({ user: userB });
    await act(() => result.current.flush());
    expect(post).not.toHaveBeenCalled();
    useAuthStore.setState({ user: userA });
    post.mockImplementation(async () => ({}));
    await act(() => result.current.flush());
    expect(useOfflineQueueStore.getState().items).toEqual([]);
  });

  it.each([400, 403, 404, 422])(
    "un %i es permanente: gasta intento y no corta el loop",
    async (status) => {
      useOfflineQueueStore.setState({ items: [item("a"), item("b")] });
      const post = jest.fn(async () => Promise.reject(httpError(status)));
      const { result } = setup({ dummy: { post } });
      await act(() => result.current.flush());
      expect(post).toHaveBeenCalledTimes(2);
      expect(useOfflineQueueStore.getState().items.map((i) => i.attempts)).toEqual([1, 1]);
    },
  );

  it("un 409 que NO es el replay idempotente es permanente", async () => {
    useOfflineQueueStore.setState({ items: [item("a")] });
    const conflict = httpError(409, { detail: { code: "CONFLICT", message: "x" } });
    const { result } = setup({ dummy: { post: async () => Promise.reject(conflict) } });
    await act(() => result.current.flush());
    expect(useOfflineQueueStore.getState().items[0]).toMatchObject({ attempts: 1 });
  });

  it("al agotar intentos por 4xx permanente, el ítem pasa a fallidos y se avisa (no se borra)", async () => {
    useOfflineQueueStore.setState({
      items: [item("casi", { attempts: MAX_FLUSH_ATTEMPTS - 1 }), item("nuevo")],
      failed: [],
    });
    const onItemFailed = jest.fn();
    const post = jest.fn(async () => Promise.reject(httpError(422)));
    const qc = new QueryClient();
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(
      () => ({
        offline: useOfflineSubmit({ handlers: { dummy: { post } }, onItemFailed }),
        failed: useFailedOfflineItems(),
      }),
      { wrapper },
    );
    await act(() => result.current.offline.flush());
    const { items, failed } = useOfflineQueueStore.getState();
    expect(items).toEqual([expect.objectContaining({ id: "nuevo", attempts: 1 })]);
    expect(failed).toEqual([
      expect.objectContaining({
        id: "casi",
        status: 422,
        attempts: MAX_FLUSH_ATTEMPTS,
        lastError: "Error 422 del servidor",
        payload: { n: "casi" },
        failedAt: expect.any(String),
      }),
    ]);
    expect(onItemFailed).toHaveBeenCalledWith(expect.objectContaining({ id: "casi" }));
    expect(result.current.failed.map((f) => f.id)).toEqual(["casi"]);
  });

  it("un error que no es HTTP (bug del handler) no gasta intento ni corta a los demás", async () => {
    useOfflineQueueStore.setState({ items: [item("a"), item("b")] });
    const post = jest
      .fn()
      .mockRejectedValueOnce(new TypeError("undefined is not a function"))
      .mockResolvedValueOnce({});
    const { result } = setup({ dummy: { post } });
    await act(() => result.current.flush());
    expect(useOfflineQueueStore.getState().items).toEqual([
      expect.objectContaining({ id: "a", attempts: 0, lastError: "Error desconocido" }),
    ]);
  });

  it("offline, no intenta nada; sin handler para el kind, deja el item intacto", async () => {
    useOfflineQueueStore.setState({ items: [item("a", { kind: "otro" })] });
    const post = jest.fn(async () => ({}));
    const { result } = setup({ dummy: { post } });
    setOnline(false);
    await act(() => result.current.flush());
    setOnline(true);
    await act(() => result.current.flush());
    expect(post).not.toHaveBeenCalled();
    expect(useOfflineQueueStore.getState().items).toHaveLength(1);
  });

  it("tablet compartida: con la sesión de B no manda las cargas de A, ni las de otro tenant", async () => {
    useOfflineQueueStore.setState({
      items: [
        item("de-a"),
        item("de-b", { userId: "u2" }),
        item("b-otro-tenant", { userId: "u2", tenantId: "t9" }),
      ],
    });
    useAuthStore.setState({ user: userB });
    const post = jest.fn(async () => ({}));
    const { result } = setup({ dummy: { post } });
    await act(() => result.current.flush());
    expect(post.mock.calls.map((c) => (c as unknown[])[1])).toEqual(["de-b"]);
    // Las de A quedan intactas para cuando A vuelva a entrar.
    expect(useOfflineQueueStore.getState().items.map((i) => i.id)).toEqual([
      "de-a",
      "b-otro-tenant",
    ]);
  });

  it("sin sesión, el flush no manda nada", async () => {
    useOfflineQueueStore.setState({ items: [item("a")] });
    useAuthStore.setState({ user: null });
    const post = jest.fn(async () => ({}));
    const { result } = setup({ dummy: { post } });
    await act(() => result.current.flush());
    expect(post).not.toHaveBeenCalled();
  });

  it("no corre dos flush a la vez", async () => {
    useOfflineQueueStore.setState({ items: [item("a")] });
    let release!: () => void;
    const post = jest.fn(() => new Promise((r) => (release = () => r({}))));
    const { result } = setup({ dummy: { post } });
    await act(async () => {
      const first = result.current.flush();
      await result.current.flush();
      release();
      await first;
    });
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("dos instancias montadas no hacen flush en paralelo del mismo ítem", async () => {
    useOfflineQueueStore.setState({ items: [item("a")] });
    let release!: () => void;
    const post = jest.fn(() => new Promise((r) => (release = () => r({}))));
    const one = setup({ dummy: { post } });
    const two = setup({ dummy: { post } });
    await act(async () => {
      const first = one.result.current.flush();
      await two.result.current.flush();
      release();
      await first;
    });
    expect(post).toHaveBeenCalledTimes(1);
    expect(useOfflineQueueStore.getState().items).toEqual([]);
  });

  it("autoSync: sincroniza al montar y al volver online", async () => {
    useOfflineQueueStore.setState({ items: [item("a")] });
    const post = jest.fn(async () => ({}));
    const { unmount } = setup({ dummy: { post } }, true);
    await act(async () => {});
    expect(post).toHaveBeenCalledTimes(1);
    useOfflineQueueStore.setState({ items: [item("b")] });
    await act(async () => {
      window.dispatchEvent(new Event("online"));
    });
    expect(post).toHaveBeenCalledTimes(2);
    unmount();
  });
});

describe("hooks auxiliares", () => {
  it("useOfflineQueueCount cuenta solo las del usuario actual", () => {
    useOfflineQueueStore.setState({ items: [item("a"), item("b"), item("c", { userId: "u2" })] });
    const { result } = renderHook(() => useOfflineQueueCount());
    expect(result.current).toBe(2);
  });

  it("useOnlineStatus sigue los eventos online/offline", () => {
    setOnline(true);
    const { result, unmount } = renderHook(() => useOnlineStatus());
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    expect(result.current).toBe(false);
    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    expect(result.current).toBe(true);
    unmount();
  });
});
