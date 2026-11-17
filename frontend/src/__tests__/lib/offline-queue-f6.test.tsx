import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  AxiosError,
  AxiosHeaders,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";

import {
  classifyFlushError,
  isInvalidTransition,
  useConflictOfflineItems,
  useOfflineSubmit,
  type OfflineHandlers,
  type OfflinePostMeta,
} from "@/hooks/useOfflineSubmit";
import { useOfflineQueueStatus } from "@/lib/useOfflineQueueStatus";
import { useAuthStore } from "@/stores/authStore";
import { useOfflineQueueStore } from "@/stores/offlineQueueStore";

const user = {
  id: "u1",
  email: "a@x.com",
  role: "OWNER" as const,
  tenant_id: "t1",
  username: null,
  permission_profile_id: null,
};

const config = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
function httpError(status: number, code?: string): AxiosError {
  const data = code ? { detail: { code, message: "x" } } : {};
  const response = { status, data, statusText: "", headers: {}, config } as AxiosResponse;
  return new AxiosError(`HTTP ${status}`, "ERR_BAD_RESPONSE", config, null, response);
}
const networkError = () => new AxiosError("Network Error", "ERR_NETWORK", config);

function setOnline(value: boolean) {
  Object.defineProperty(navigator, "onLine", { configurable: true, get: () => value });
}

const STORAGE_KEY = "carwash_offline_queue";

/** Simula cerrar y reabrir la pestaña: la memoria se pierde, el storage no. */
async function reloadPage() {
  const raw = localStorage.getItem(STORAGE_KEY) as string;
  await act(async () => {
    useOfflineQueueStore.setState({ items: [], failed: [], conflicts: [] });
    localStorage.setItem(STORAGE_KEY, raw);
    await useOfflineQueueStore.persist.rehydrate();
  });
}

function setup(
  handlers: OfflineHandlers,
  extra: Parameters<typeof useOfflineSubmit>[0] | object = {},
) {
  const qc = new QueryClient();
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return renderHook(() => useOfflineSubmit({ handlers, ...extra }), { wrapper });
}

beforeEach(() => {
  jest.useRealTimers();
  localStorage.clear();
  useAuthStore.setState({ user, tenant: { id: "t1", name: "L" } });
  useOfflineQueueStore.setState({ items: [], failed: [], conflicts: [] });
  setOnline(true);
});

describe("idempotencia y hora del toque", () => {
  it("el id de la cola ES la Idempotency-Key y occurredAt se conserva en reintentos y recarga", async () => {
    setOnline(false);
    const seen: Array<{ key: string; occurredAt?: string }> = [];
    let up = false;
    const post = jest.fn(async (_p: unknown, key: string, meta?: OfflinePostMeta) => {
      seen.push({ key, occurredAt: meta?.occurredAt });
      if (!up) throw networkError();
      return {};
    });
    const { result } = setup({ iniciar: { post } });

    jest.useFakeTimers().setSystemTime(new Date("2026-11-17T10:00:00Z"));
    await act(() => result.current.submit("iniciar", { job: "j1" }));
    jest.setSystemTime(new Date("2026-11-17T10:45:00Z"));

    const queued = useOfflineQueueStore.getState().items[0]!;
    expect(queued.id).toBe(seen[0]?.key);
    expect(queued.occurredAt).toBe("2026-11-17T10:00:00.000Z");

    await reloadPage();
    expect(useOfflineQueueStore.getState().items[0]).toMatchObject({
      id: queued.id,
      occurredAt: "2026-11-17T10:00:00.000Z",
    });

    // Primer vaciado: la red sigue caída. Segundo: vuelve.
    setOnline(true);
    await act(() => result.current.flush());
    up = true;
    await act(() => result.current.flush());

    expect(seen).toHaveLength(3);
    for (const call of seen) {
      expect(call).toEqual({ key: queued.id, occurredAt: "2026-11-17T10:00:00.000Z" });
    }
    expect(useOfflineQueueStore.getState().items).toEqual([]);
  });

  it("un ítem viejo sin occurredAt usa createdAt como respaldo", async () => {
    useOfflineQueueStore.setState({
      items: [
        {
          id: "viejo",
          userId: "u1",
          tenantId: "t1",
          kind: "k",
          payload: {},
          createdAt: "2026-01-01T00:00:00.000Z",
          attempts: 0,
        },
      ],
    });
    const post = jest.fn(async () => ({}));
    const { result } = setup({ k: { post } });
    await act(() => result.current.flush());
    expect(post).toHaveBeenCalledWith({}, "viejo", { occurredAt: "2026-01-01T00:00:00.000Z" });
  });
});

describe("dedupe por acción + job", () => {
  it("dos toques del mismo botón en modo avión dejan UNA entrada", async () => {
    const post = jest.fn(async () => {
      throw networkError();
    });
    const { result } = setup({ iniciar: { post } });
    const onQueued = jest.fn();
    await act(async () => {
      await result.current.submit("iniciar", { job: "j1" }, { dedupeKey: "iniciar:j1", onQueued });
      await result.current.submit("iniciar", { job: "j1" }, { dedupeKey: "iniciar:j1", onQueued });
    });
    expect(useOfflineQueueStore.getState().items).toHaveLength(1);
    expect(post).toHaveBeenCalledTimes(1); // el segundo toque ni intenta
    expect(onQueued).toHaveBeenCalledTimes(2);
  });

  it("toques legítimos distintos (otro job u otra acción) se conservan todos", async () => {
    const post = jest.fn(async () => {
      throw networkError();
    });
    const { result } = setup({ a: { post } });
    await act(async () => {
      await result.current.submit("a", {}, { dedupeKey: "iniciar:j1" });
      await result.current.submit("a", {}, { dedupeKey: "iniciar:j2" });
      await result.current.submit("a", {}, { dedupeKey: "finalizar:j1" });
      await result.current.submit("a", {}); // sin clave: único
    });
    expect(useOfflineQueueStore.getState().items).toHaveLength(4);
  });

  it("doble toque con la red lenta: el segundo se ignora mientras el primero está en vuelo", async () => {
    let release: () => void = () => undefined;
    const post = jest.fn(
      () =>
        new Promise((resolve) => {
          release = () => resolve({});
        }),
    );
    const { result } = setup({ a: { post } });
    const onSuccess = jest.fn();
    let first!: Promise<void>;
    await act(async () => {
      first = result.current.submit("a", {}, { dedupeKey: "cobrar:j1", onSuccess });
      await result.current.submit("a", {}, { dedupeKey: "cobrar:j1", onSuccess });
    });
    expect(post).toHaveBeenCalledTimes(1);
    await act(async () => {
      release();
      await first;
    });
    expect(onSuccess).toHaveBeenCalledTimes(1);
  });

  it("el store no duplica por dedupeKey pero sí entre dueños distintos", () => {
    const base = { kind: "k", payload: {}, createdAt: "x", attempts: 0, dedupeKey: "d" };
    const { enqueue } = useOfflineQueueStore.getState();
    enqueue({ ...base, id: "1", userId: "u1", tenantId: "t1" });
    enqueue({ ...base, id: "2", userId: "u1", tenantId: "t1" });
    enqueue({ ...base, id: "3", userId: "u2", tenantId: "t1" });
    expect(useOfflineQueueStore.getState().items.map((i) => i.id)).toEqual(["1", "3"]);
  });
});

describe("respuestas del servidor al vaciar", () => {
  function seed(...ids: string[]) {
    useOfflineQueueStore.setState({
      items: ids.map((id) => ({
        id,
        userId: "u1",
        tenantId: "t1",
        kind: "k",
        payload: { id },
        createdAt: "2026-11-17T10:00:00.000Z",
        attempts: 0,
      })),
    });
  }

  it("clasifica", () => {
    expect(classifyFlushError(httpError(409, "INVALID_TRANSITION"))).toBe("conflict");
    expect(classifyFlushError(httpError(409, "DUPLICATE_IDEMPOTENT"))).toBe("duplicate");
    expect(classifyFlushError(httpError(409, "CONFLICT"))).toBe("permanent");
    expect(isInvalidTransition(httpError(422, "INVALID_TRANSITION"))).toBe(false);
    expect(isInvalidTransition(new Error("x"))).toBe(false);
  });

  it("INVALID_TRANSITION: sale de la cola sin reintento y queda como conflicto visible", async () => {
    seed("a", "b");
    const post = jest.fn(async (_p: unknown, key: string) => {
      if (key === "a") throw httpError(409, "INVALID_TRANSITION");
      return {};
    });
    const onItemConflict = jest.fn();
    const qc = new QueryClient();
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(
      () => ({
        offline: useOfflineSubmit({ handlers: { k: { post } }, onItemConflict }),
        conflicts: useConflictOfflineItems(),
        status: useOfflineQueueStatus(),
      }),
      { wrapper },
    );
    await act(() => result.current.offline.flush());
    await act(() => result.current.offline.flush());

    expect(post).toHaveBeenCalledTimes(2); // a una vez, b una vez: no se reintenta a
    const state = useOfflineQueueStore.getState();
    expect(state.items).toEqual([]);
    expect(state.failed).toEqual([]);
    expect(state.conflicts).toEqual([expect.objectContaining({ id: "a", status: 409 })]);
    expect(onItemConflict).toHaveBeenCalledWith(expect.objectContaining({ id: "a" }));
    expect(result.current.conflicts.map((c) => c.id)).toEqual(["a"]);
    expect(result.current.status).toMatchObject({ pending: 0, needsAttention: true });

    // Sobrevive a una recarga hasta que el usuario lo descarta.
    await reloadPage();
    expect(useOfflineQueueStore.getState().conflicts.map((c) => c.id)).toEqual(["a"]);
    act(() => useOfflineQueueStore.getState().dismissConflict("a"));
    expect(useOfflineQueueStore.getState().conflicts).toEqual([]);
  });

  it("INVALID_TRANSITION en línea: onConflict si está, si no onError (y nunca se encola)", async () => {
    const post = jest.fn(async () => {
      throw httpError(409, "INVALID_TRANSITION");
    });
    const { result } = setup({ k: { post } });
    const onConflict = jest.fn();
    const onError = jest.fn();
    await act(() => result.current.submit("k", {}, { onConflict, onError }));
    expect(onConflict).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
    await act(() => result.current.submit("k", {}, { onError }));
    expect(onError).toHaveBeenCalledTimes(1);
    expect(useOfflineQueueStore.getState().items).toEqual([]);
  });

  it("409 CONFLICT, 422, 403 y 404 son fallo permanente visible y no se reintentan", async () => {
    seed("c", "d", "e", "f");
    const codes: Record<string, AxiosError> = {
      c: httpError(409, "CONFLICT"),
      d: httpError(422, "VALIDATION_ERROR"),
      e: httpError(403, "FORBIDDEN"),
      f: httpError(404, "NOT_FOUND"),
    };
    const post = jest.fn(async (_p: unknown, key: string) => {
      throw codes[key];
    });
    const { result } = setup({ k: { post } });
    await act(() => result.current.flush());
    await act(() => result.current.flush());
    expect(post).toHaveBeenCalledTimes(4);
    const { items, failed } = useOfflineQueueStore.getState();
    expect(items).toEqual([]);
    expect(failed.map((x) => [x.id, x.status])).toEqual([
      ["c", 409],
      ["d", 422],
      ["e", 403],
      ["f", 404],
    ]);
  });

  it("5xx/red/timeout: reintenta sin gastar intentos ni perder el ítem", async () => {
    seed("a");
    const post = jest.fn(async () => {
      throw httpError(503);
    });
    const { result } = setup({ k: { post } });
    for (let i = 0; i < 8; i += 1) await act(() => result.current.flush());
    expect(post).toHaveBeenCalledTimes(8);
    expect(useOfflineQueueStore.getState().items).toEqual([
      expect.objectContaining({ id: "a", attempts: 0 }),
    ]);
  });

  it("200 y 409 DUPLICATE_IDEMPOTENT son éxito", async () => {
    seed("a", "b");
    const post = jest.fn(async (_p: unknown, key: string) => {
      if (key === "b") throw httpError(409, "DUPLICATE_IDEMPOTENT");
      return {};
    });
    const { result } = setup({ k: { post } });
    await act(() => result.current.flush());
    const state = useOfflineQueueStore.getState();
    expect([state.items, state.failed, state.conflicts]).toEqual([[], [], []]);
  });
});

describe("un solo vaciado a la vez", () => {
  it("con Web Locks: si otra pestaña tiene el lock no manda nada; si lo tiene, relee el storage", async () => {
    let available = false;
    const request = jest.fn(
      async (_name: string, _opts: unknown, cb: (lock: object | null) => Promise<void>) =>
        cb(available ? {} : null),
    );
    Object.defineProperty(navigator, "locks", { configurable: true, value: { request } });
    try {
      useOfflineQueueStore.setState({
        items: [
          {
            id: "a",
            userId: "u1",
            tenantId: "t1",
            kind: "k",
            payload: {},
            createdAt: "2026-11-17T10:00:00.000Z",
            attempts: 0,
          },
        ],
      });
      const post = jest.fn(async () => ({}));
      const { result } = setup({ k: { post } });
      await act(() => result.current.flush());
      expect(post).not.toHaveBeenCalled();

      // Otra pestaña ya sincronizó "a": al tomar el lock se relee y no se reenvía.
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ state: { items: [], failed: [], conflicts: [] }, version: 4 }),
      );
      available = true;
      await act(() => result.current.flush());
      expect(post).not.toHaveBeenCalled();
      expect(request).toHaveBeenCalledWith(
        "carwash_offline_flush",
        { ifAvailable: true },
        expect.any(Function),
      );
    } finally {
      Reflect.deleteProperty(navigator, "locks");
    }
  });

  it("con Web Locks y el lock libre, rehydrate roto no frena el vaciado", async () => {
    Object.defineProperty(navigator, "locks", {
      configurable: true,
      value: {
        request: async (_n: string, _o: unknown, cb: (l: object) => Promise<void>) => cb({}),
      },
    });
    const rehydrate = jest
      .spyOn(useOfflineQueueStore.persist, "rehydrate")
      .mockRejectedValue(new Error("storage"));
    try {
      useOfflineQueueStore.setState({
        items: [
          {
            id: "a",
            userId: "u1",
            tenantId: "t1",
            kind: "k",
            payload: {},
            createdAt: "2026-11-17T10:00:00.000Z",
            attempts: 0,
          },
        ],
      });
      const post = jest.fn(async () => ({}));
      const { result } = setup({ k: { post } });
      await act(() => result.current.flush());
      expect(post).toHaveBeenCalledTimes(1);
    } finally {
      rehydrate.mockRestore();
      Reflect.deleteProperty(navigator, "locks");
    }
  });

  it("`online` repetido y dos montajes: cada ítem sale una sola vez", async () => {
    useOfflineQueueStore.setState({
      items: ["a", "b"].map((id) => ({
        id,
        userId: "u1",
        tenantId: "t1",
        kind: "k",
        payload: {},
        createdAt: "2026-11-17T10:00:00.000Z",
        attempts: 0,
      })),
    });
    const post = jest.fn(async () => {
      await new Promise((r) => setTimeout(r, 5));
      return {};
    });
    setup({ k: { post } }, { autoSync: true });
    setup({ k: { post } }, { autoSync: true });
    await act(async () => {
      window.dispatchEvent(new Event("online"));
      window.dispatchEvent(new Event("online"));
      await new Promise((r) => setTimeout(r, 40));
    });
    expect(post).toHaveBeenCalledTimes(2);
  });
});

describe("lavado completo sin señal", () => {
  /**
   * Servidor simulado, fiel a la adenda §12.3/§12.4: idempotencia por clave
   * (reenvío = 200 con el mismo resultado), máquina de estados del job y
   * `occurred_at` registrado tal cual llega.
   */
  function fakeServer() {
    const byKey = new Map<string, unknown>();
    const events: Array<{ action: string; key: string; occurred_at: string }> = [];
    let state = "AGENDADO";
    let cutAfter = Infinity; // cuántos pedidos más aguantan antes del corte
    let lostReply: string | null = null; // acción cuyo 200 se pierde en el camino
    const next: Record<string, [string, string]> = {
      llego: ["AGENDADO", "RECIBIDO"],
      iniciar: ["RECIBIDO", "EN_CURSO"],
      finalizar: ["EN_CURSO", "FINALIZADO"],
      cobrar: ["FINALIZADO", "PAGADO"],
    };
    const post = async (action: string, key: string, occurredAt: string) => {
      if (cutAfter <= 0) throw networkError();
      cutAfter -= 1;
      if (!byKey.has(key)) {
        const [from, to] = next[action]!;
        if (state !== from) throw httpError(409, "INVALID_TRANSITION");
        state = to;
        events.push({ action, key, occurred_at: occurredAt });
        byKey.set(key, { state });
      }
      if (lostReply === action) {
        lostReply = null;
        throw networkError(); // el servidor guardó, la respuesta no llegó
      }
      return byKey.get(key);
    };
    return {
      post,
      events,
      cutAfter: (n: number) => {
        cutAfter = n;
      },
      restore: () => {
        cutAfter = Infinity;
      },
      loseReplyOf: (a: string) => {
        lostReply = a;
      },
      get state() {
        return state;
      },
    };
  }

  function handlers(server: ReturnType<typeof fakeServer>): OfflineHandlers {
    return {
      accion: {
        post: (payload, key, meta) =>
          server.post((payload as { action: string }).action, key, meta?.occurredAt ?? "SIN-HORA"),
      },
    };
  }

  async function tapAll(result: { current: ReturnType<typeof useOfflineSubmit> }) {
    const taps: Array<[string, string]> = [
      ["llego", "2026-11-17T09:00:00.000Z"],
      ["iniciar", "2026-11-17T09:05:00.000Z"],
      ["finalizar", "2026-11-17T09:50:00.000Z"],
      ["cobrar", "2026-11-17T09:52:00.000Z"],
    ];
    for (const [action, at] of taps) {
      jest.setSystemTime(new Date(at));
      await act(() => result.current.submit("accion", { action }, { dedupeKey: `${action}:j1` }));
    }
    // Un toque repetido del último botón, también sin señal.
    await act(() =>
      result.current.submit("accion", { action: "cobrar" }, { dedupeKey: "cobrar:j1" }),
    );
    return taps;
  }

  it("se sincroniza UNA vez, en orden y con la hora real de cada toque", async () => {
    jest.useFakeTimers({ doNotFake: ["setTimeout", "setImmediate", "nextTick"] });
    const server = fakeServer();
    server.cutAfter(0);
    setOnline(false);
    const { result } = setup(handlers(server));
    const taps = await tapAll(result);
    expect(useOfflineQueueStore.getState().items).toHaveLength(4);

    await reloadPage(); // la pestaña se cierra y se reabre antes de volver la señal
    setOnline(true);
    server.restore();
    await act(() => result.current.flush());
    await act(() => result.current.flush()); // un `online` de más no duplica nada

    expect(server.events.map((e) => [e.action, e.occurred_at])).toEqual(taps);
    expect(server.state).toBe("PAGADO");
    const state = useOfflineQueueStore.getState();
    expect([state.items, state.failed, state.conflicts]).toEqual([[], [], []]);
  });

  it("corte a mitad del vaciado: sigue donde quedó y el reenvío no duplica ni cambia la hora", async () => {
    jest.useFakeTimers({ doNotFake: ["setTimeout", "setImmediate", "nextTick"] });
    const server = fakeServer();
    server.cutAfter(0);
    setOnline(false);
    const { result } = setup(handlers(server));
    const taps = await tapAll(result);

    setOnline(true);
    server.cutAfter(2); // pasan "llegó" e "iniciar" y se corta
    server.loseReplyOf("iniciar"); // y la respuesta de "iniciar" se pierde: llega duplicado
    await act(() => result.current.flush());
    expect(useOfflineQueueStore.getState().items.map((i) => i.payload)).toEqual([
      { action: "iniciar" },
      { action: "finalizar" },
      { action: "cobrar" },
    ]);

    server.restore();
    await act(() => result.current.flush());

    expect(server.events.map((e) => [e.action, e.occurred_at])).toEqual(taps);
    expect(server.state).toBe("PAGADO");
    expect(useOfflineQueueStore.getState().items).toEqual([]);
    expect(useOfflineQueueStore.getState().conflicts).toEqual([]);
  });
});
