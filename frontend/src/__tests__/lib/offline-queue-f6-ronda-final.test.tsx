import { act, render, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  AxiosError,
  AxiosHeaders,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";

import {
  MAX_TRANSIENT_FAILURES,
  useOfflineSubmit,
  type OfflineHandlers,
} from "@/hooks/useOfflineSubmit";
import { findSmallTouchTargets } from "@/lib/touchTargets";
import { useAuthStore } from "@/stores/authStore";
import { useOfflineQueueStore, type QueuedItem } from "@/stores/offlineQueueStore";

const userA = {
  id: "u1",
  email: "a@x.com",
  role: "OWNER" as const,
  tenant_id: "t1",
  username: null,
  permission_profile_id: null,
};
const userB = { ...userA, id: "u2", email: "b@x.com" };

const config = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
function httpError(status: number): AxiosError {
  const response = { status, data: {}, statusText: "", headers: {}, config } as AxiosResponse;
  return new AxiosError(`HTTP ${status}`, "ERR_BAD_RESPONSE", config, null, response);
}
const networkError = () => new AxiosError("Network Error", "ERR_NETWORK", config);

function setOnline(value: boolean) {
  Object.defineProperty(navigator, "onLine", { configurable: true, get: () => value });
}

function qItem(id: string, dedupeKey?: string, over: Partial<QueuedItem> = {}): QueuedItem {
  return {
    id,
    userId: "u1",
    tenantId: "t1",
    kind: "k",
    payload: { id },
    createdAt: "2026-11-17T10:00:00.000Z",
    attempts: 0,
    dedupeKey,
    ...over,
  };
}

function mount(handlers: OfflineHandlers, autoSync = false) {
  const qc = new QueryClient();
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return renderHook(() => useOfflineSubmit({ handlers, autoSync }), { wrapper });
}

/** Un vaciado exitoso reinicia el backoff de módulo que dejó un test anterior. */
async function resetBackoff() {
  useOfflineQueueStore.setState({ items: [], failed: [], conflicts: [] });
  const { result, unmount } = mount({ k: { post: async () => ({}) } });
  await act(() => result.current.flush());
  unmount();
}

beforeEach(async () => {
  jest.useRealTimers();
  localStorage.clear();
  setOnline(true);
  useAuthStore.setState({ user: userA, tenant: { id: "t1", name: "L" } });
  await resetBackoff();
});

afterEach(() => {
  jest.useRealTimers();
});

describe("reintento programado (backoff)", () => {
  it("1. desmontar el componente NO cancela el reintento: la cola se vacía igual", async () => {
    jest.useFakeTimers();
    useOfflineQueueStore.setState({ items: [qItem("a")] });
    let up = false;
    const post = jest.fn(async () => {
      if (!up) throw httpError(503);
      return {};
    });
    const { unmount } = mount({ k: { post } }, true);
    await act(async () => {
      await Promise.resolve();
    });
    expect(post).toHaveBeenCalledTimes(1);
    unmount(); // se navegó a otra pantalla dentro del backoff
    up = true;
    await act(async () => {
      jest.advanceTimersByTime(10 * 60_000);
    });
    expect(post).toHaveBeenCalledTimes(2);
    expect(useOfflineQueueStore.getState().items).toEqual([]);
  });

  it("2. el timer no pasa por la compuerta: si el reloj quedó 1 s atrás igual reintenta", async () => {
    jest.useFakeTimers().setSystemTime(new Date("2026-11-17T10:00:00Z"));
    useOfflineQueueStore.setState({ items: [qItem("a")] });
    let up = false;
    const post = jest.fn(async () => {
      if (!up) throw httpError(503);
      return {};
    });
    mount({ k: { post } }, true);
    await act(async () => {
      await Promise.resolve();
    });
    expect(post).toHaveBeenCalledTimes(1);
    jest.setSystemTime(new Date("2026-11-17T09:59:55Z")); // el reloj retrocede 5 s
    up = true;
    await act(async () => {
      jest.advanceTimersByTime(10 * 60_000);
    });
    expect(post).toHaveBeenCalledTimes(2);
    expect(useOfflineQueueStore.getState().items).toEqual([]);
  });

  it("6. un vaciado que no corrió (offline) NO reinicia el backoff", async () => {
    jest.useFakeTimers();
    useOfflineQueueStore.setState({ items: [qItem("a")] });
    const post = jest.fn(async () => {
      throw httpError(503);
    });
    mount({ k: { post } }, true);
    await act(async () => {
      await Promise.resolve();
    }); // falla 1
    await act(async () => {
      jest.advanceTimersByTime(10 * 60_000);
    }); // falla 2
    setOnline(false);
    await act(async () => {
      jest.advanceTimersByTime(10 * 60_000);
    }); // el timer dispara offline: no corre
    expect(post).toHaveBeenCalledTimes(2);
    setOnline(true);
    await act(async () => {
      window.dispatchEvent(new Event("online"));
      await Promise.resolve();
    }); // falla 3
    for (let i = 0; i < 12; i += 1) {
      await act(async () => {
        jest.advanceTimersByTime(10 * 60_000);
      });
    }
    // 8 reintentos programados como máximo en total: con el contador reiniciado
    // por el vaciado offline serían más.
    expect(post).toHaveBeenCalledTimes(9);
  });
});

describe("ítems que no avanzan", () => {
  it("3. un 503 determinista pasa a fallidos tras el tope, sigue con otros jobs y deja detrás al mismo job", async () => {
    useOfflineQueueStore.setState({
      items: [qItem("p", "iniciar:j1"), qItem("f", "finalizar:j1"), qItem("o", "iniciar:j2")],
    });
    const post = jest.fn(async (_p: unknown, key: string) => {
      if (key === "p") throw httpError(503);
      return {};
    });
    const { result } = mount({ k: { post } });
    for (let i = 0; i < MAX_TRANSIENT_FAILURES - 1; i += 1) {
      await act(() => result.current.flush());
    }
    expect(useOfflineQueueStore.getState().failed).toEqual([]);
    expect(post.mock.calls.every((c) => c[1] === "p")).toBe(true);
    await act(() => result.current.flush());
    const state = useOfflineQueueStore.getState();
    expect(state.failed).toEqual([expect.objectContaining({ id: "p", status: 503 })]);
    expect(post.mock.calls.map((c) => c[1])).toContain("o"); // otro job siguió
    expect(post.mock.calls.map((c) => c[1])).not.toContain("f"); // el mismo job quedó detrás
    expect(state.items.map((i) => i.id)).toEqual(["f"]);
    act(() => useOfflineQueueStore.getState().retryFailed("p")); // recuperable
    expect(useOfflineQueueStore.getState().items.map((i) => i.id)).toEqual(["f", "p"]);
  });

  it("4. un bug del handler en un ítem no deja pasar adelantadas las acciones del mismo job", async () => {
    useOfflineQueueStore.setState({
      items: [qItem("i1", "iniciar:j1"), qItem("f1", "finalizar:j1"), qItem("i2", "iniciar:j2")],
    });
    const post = jest.fn(async (_p: unknown, key: string) => {
      if (key === "i1") throw new TypeError("bug");
      return {};
    });
    const { result } = mount({ k: { post } });
    await act(() => result.current.flush());
    expect(post.mock.calls.map((c) => c[1])).toEqual(["i1", "i2"]);
    expect(useOfflineQueueStore.getState().items.map((i) => [i.id, i.attempts])).toEqual([
      ["i1", 1],
      ["f1", 0],
    ]);
  });
});

describe("claves por dueño", () => {
  it("5. la clave en vuelo es por usuario: B no queda ignorado por un toque de A", async () => {
    const releases: Array<() => void> = [];
    const post = jest.fn(
      () =>
        new Promise((resolve) => {
          releases.push(() => resolve({}));
        }),
    );
    const { result } = mount({ k: { post } });
    let first!: Promise<void>;
    let second!: Promise<void>;
    await act(async () => {
      first = result.current.submit("k", {}, { dedupeKey: "cobrar:j1" });
    });
    useAuthStore.setState({ user: userB });
    await act(async () => {
      second = result.current.submit("k", {}, { dedupeKey: "cobrar:j1" });
    });
    expect(post).toHaveBeenCalledTimes(2);
    await act(async () => {
      releases.forEach((r) => r());
      await Promise.all([first, second]);
    });
  });

  it("5. la reserva de clave (storage lleno) no la hereda otro usuario ni vence tarde", async () => {
    jest.useFakeTimers().setSystemTime(new Date("2026-11-17T10:00:00Z"));
    const post = jest.fn(async (_p: unknown, _k: string) => {
      throw networkError();
    });
    const spy = jest.spyOn(useOfflineQueueStore.getState(), "enqueue").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    try {
      const { result } = mount({ k: { post } });
      const submit = () => result.current.submit("k", {}, { dedupeKey: "cobrar:j9" });
      await act(submit);
      await act(submit); // mismo usuario, enseguida: reusa la clave
      expect(post.mock.calls[1]?.[1]).toBe(post.mock.calls[0]?.[1]);
      useAuthStore.setState({ user: userB });
      await act(submit); // otro usuario: clave propia
      expect(post.mock.calls[2]?.[1]).not.toBe(post.mock.calls[0]?.[1]);
      useAuthStore.setState({ user: userA });
      jest.setSystemTime(new Date("2026-11-17T10:11:00Z")); // pasó la vigencia
      await act(submit);
      expect(post.mock.calls[3]?.[1]).not.toBe(post.mock.calls[0]?.[1]);
    } finally {
      spy.mockRestore();
    }
  });

  it("7. el toque repetido mientras el primero está en vuelo avisa onQueued (la UI no se cuelga)", async () => {
    let release: () => void = () => undefined;
    const post = jest.fn(
      () =>
        new Promise((resolve) => {
          release = () => resolve({});
        }),
    );
    const { result } = mount({ k: { post } });
    const onQueued = jest.fn();
    let first!: Promise<void>;
    await act(async () => {
      first = result.current.submit("k", {}, { dedupeKey: "cobrar:j1" });
      await result.current.submit("k", {}, { dedupeKey: "cobrar:j1", onQueued });
    });
    expect(onQueued).toHaveBeenCalledTimes(1);
    await act(async () => {
      release();
      await first;
    });
  });
});

describe("touchTargets: ocultos y toggles", () => {
  it("10. un ancestro oculto exime; email/number/submit dentro de un label NO heredan el tamaño", () => {
    const { container } = render(
      <div>
        <div hidden>
          <button className="h-4 w-4">oculto por el padre</button>
        </div>
        <div style={{ display: "none" }}>
          <button className="h-4 w-4">display none</button>
        </div>
        <div aria-hidden="true">
          <button className="h-4 w-4">aria-hidden</button>
        </div>
        <label className="min-h-11 min-w-11">
          <input type="checkbox" />
        </label>
        <label className="min-h-11 min-w-11">
          <input type="email" />
        </label>
        <label className="min-h-11 min-w-11">
          <input type="number" />
        </label>
        <label className="min-h-11 min-w-11">
          <input type="submit" />
        </label>
      </div>,
    );
    expect(findSmallTouchTargets(container).map((e) => e.getAttribute("type"))).toEqual([
      "email",
      "number",
      "submit",
    ]);
  });
});
