/**
 * T3 6.3 — ataques 4 y 5: vaciado concurrente/FIFO/corte a mitad y clasificación
 * de respuestas del servidor.
 */
import { AxiosError } from "axios";

import {
  STORAGE_KEY,
  codeError,
  deferred,
  httpError,
  loadApp,
  networkError,
  queued,
  resetBrowser,
  setOnline,
  unmountAll,
  userA,
} from "./helpers";

beforeEach(() => {
  resetBrowser();
});
afterEach(() => {
  unmountAll();
  jest.useRealTimers();
  resetBrowser();
});

function seed(app: ReturnType<typeof loadApp>, ...ids: string[]) {
  ids.forEach((id) => app.useStore.getState().enqueue(queued(id, { payload: { id } })));
}

/** Mini administrador de Web Locks con semántica `ifAvailable` (compartido entre "pestañas"). */
function installLocks() {
  let held = false;
  const request = jest.fn(
    async (
      _n: string,
      opts: { ifAvailable?: boolean },
      cb: (l: object | null) => Promise<void>,
    ) => {
      if (opts.ifAvailable && held) return cb(null);
      held = true;
      try {
        return await cb({});
      } finally {
        held = false;
      }
    },
  );
  Object.defineProperty(navigator, "locks", { configurable: true, value: { request } });
  return request;
}

describe("4. un solo vaciado concurrente", () => {
  it("5 eventos `online` seguidos y dos montajes con autoSync: cada ítem sale UNA vez", async () => {
    const app = loadApp();
    app.login(userA);
    seed(app, "a", "b");
    const gate = deferred();
    const post = jest.fn(async (_p: unknown, _k: string) => {
      await gate.promise;
      return {};
    });
    app.mount({ k: { post } }, true);
    app.mount({ k: { post } }, true);
    await app.rtl.act(async () => {
      for (let i = 0; i < 5; i += 1) window.dispatchEvent(new Event("online"));
    });
    gate.resolve(undefined);
    await app.rtl.act(async () => {
      await Promise.resolve();
    });
    await app.rtl.act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(post.mock.calls.map((c) => c[1])).toEqual(["a", "b"]);
    expect(app.useStore.getState().items).toEqual([]);
  });

  it("flush manual mientras corre el del evento `online` no duplica", async () => {
    const app = loadApp();
    app.login(userA);
    seed(app, "a");
    const gate = deferred();
    const post = jest.fn(async () => gate.promise);
    const { result } = app.mount({ k: { post } }, true);
    await app.rtl.act(async () => {
      window.dispatchEvent(new Event("online"));
      void result.current.flush();
      void result.current.flush();
    });
    gate.resolve({});
    await app.rtl.act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("dos pestañas CON Web Locks: la segunda no manda nada mientras la primera vacía; luego relee y no reenvía", async () => {
    const request = installLocks();
    const tab1 = loadApp();
    tab1.login(userA);
    seed(tab1, "a", "b");
    const tab2 = loadApp(); // lee el mismo storage: ve a y b
    tab2.login(userA);
    expect(tab2.useStore.getState().items).toHaveLength(2);

    const gate = deferred();
    const post1 = jest.fn(async (_p: unknown, _k: string) => {
      await gate.promise;
      return {};
    });
    const post2 = jest.fn(async (_p: unknown, _k: string) => ({}));
    const m1 = tab1.mount({ k: { post: post1 } });
    const m2 = tab2.mount({ k: { post: post2 } });

    let p1!: Promise<void>;
    await tab1.rtl.act(async () => {
      p1 = m1.result.current.flush();
    });
    await tab2.rtl.act(() => m2.result.current.flush()); // lock tomado: no hace nada
    expect(post2).not.toHaveBeenCalled();
    gate.resolve({});
    await tab1.rtl.act(() => p1);
    expect(post1.mock.calls.map((c) => c[1])).toEqual(["a", "b"]);

    await tab2.rtl.act(() => m2.result.current.flush()); // relee: ya no hay nada
    expect(post2).not.toHaveBeenCalled();
    expect(request).toHaveBeenCalled();
  });

  it("dos pestañas SIN Web Locks: ambas mandan, pero SIEMPRE con la misma Idempotency-Key (el servidor deduplica) — documenta", async () => {
    const tab1 = loadApp();
    tab1.login(userA);
    seed(tab1, "a");
    const tab2 = loadApp();
    tab2.login(userA);
    const gate = deferred();
    const keys: string[] = [];
    const mk = () =>
      jest.fn(async (_p: unknown, k: string) => {
        keys.push(k);
        await gate.promise;
        return {};
      });
    const m1 = tab1.mount({ k: { post: mk() } });
    const m2 = tab2.mount({ k: { post: mk() } });
    await tab1.rtl.act(async () => {
      void m1.result.current.flush();
      void m2.result.current.flush();
    });
    gate.resolve({});
    await tab1.rtl.act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(keys).toEqual(["a", "a"]);
  });

  it("FIFO: se manda en el orden de encolado aunque las respuestas lleguen desordenadas", async () => {
    const app = loadApp();
    app.login(userA);
    seed(app, "iniciar", "finalizar", "cobrar");
    const order: string[] = [];
    const post = jest.fn(async (_p: unknown, k: string) => {
      order.push(`start:${k}`);
      await new Promise((r) => setTimeout(r, k === "iniciar" ? 30 : 1));
      order.push(`end:${k}`);
      return {};
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.flush());
    expect(order).toEqual([
      "start:iniciar",
      "end:iniciar",
      "start:finalizar",
      "end:finalizar",
      "start:cobrar",
      "end:cobrar",
    ]);
  });

  it("corte a mitad del vaciado (se pierde la respuesta del 1ro): corta, no pasa al 2do, reenvía el 1ro con la MISMA key y luego sigue", async () => {
    const app = loadApp();
    app.login(userA);
    seed(app, "a", "b", "c");
    const server = new Set<string>();
    let drop = true;
    const post = jest.fn(async (_p: unknown, k: string) => {
      const dup = server.has(k);
      server.add(k); // el servidor lo guardó
      if (drop && k === "a") throw networkError(); // pero la respuesta se perdió
      if (dup) throw codeError(409, "DUPLICATE_IDEMPOTENT");
      return {};
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.flush());
    expect(post.mock.calls.map((c) => c[1])).toEqual(["a"]);
    expect(app.useStore.getState().items.map((i) => i.id)).toEqual(["a", "b", "c"]);
    expect(app.useStore.getState().items[0]!.attempts).toBe(0);

    drop = false;
    await app.rtl.act(() => result.current.flush());
    expect(post.mock.calls.map((c) => c[1])).toEqual(["a", "a", "b", "c"]);
    expect(app.useStore.getState().items).toEqual([]);
    expect(app.useStore.getState().failed).toEqual([]);
    expect([...server].sort()).toEqual(["a", "b", "c"]); // nada se guardó dos veces con otra key
  });

  it("el vaciado se reanuda tras recargar: solo queda lo no enviado", async () => {
    const app = loadApp();
    app.login(userA);
    seed(app, "a", "b", "c");
    const post = jest.fn(async (_p: unknown, k: string) => {
      if (k === "b") throw httpError(503);
      return {};
    });
    const m = app.mount({ k: { post } });
    await app.rtl.act(() => m.result.current.flush());
    expect(app.useStore.getState().items.map((i) => i.id)).toEqual(["b", "c"]);
    unmountAll();

    const app2 = loadApp(); // recarga
    app2.login(userA);
    expect(app2.useStore.getState().items.map((i) => i.id)).toEqual(["b", "c"]);
    const post2 = jest.fn(async () => ({}));
    app2.mount({ k: { post: post2 } }, true); // autoSync: flush inicial al montar
    await app2.rtl.act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(post2.mock.calls.map((c) => (c as unknown[])[1])).toEqual(["b", "c"]);
  });

  it("recarga con un POST en vuelo: la copia nueva reenvía con la misma key (no se pierde)", async () => {
    const app = loadApp();
    app.login(userA);
    seed(app, "a");
    const gate = deferred();
    const m = app.mount({ k: { post: jest.fn(async () => gate.promise) } });
    await app.rtl.act(async () => {
      void m.result.current.flush();
    });
    // se "cierra la pestaña" con el POST en vuelo
    const app2 = loadApp();
    app2.login(userA);
    const post2 = jest.fn(async (_p: unknown, _k: string) => ({}));
    const m2 = app2.mount({ k: { post: post2 } });
    await app2.rtl.act(() => m2.result.current.flush());
    expect(post2.mock.calls.map((c) => c[1])).toEqual(["a"]);
  });

  it("sin conexión (navigator.onLine=false) el flush no manda ni gasta nada", async () => {
    const app = loadApp();
    app.login(userA);
    seed(app, "a");
    setOnline(false);
    const post = jest.fn(async () => ({}));
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.flush());
    expect(post).not.toHaveBeenCalled();
  });

  it("un ítem de un kind sin handler en este montaje no bloquea ni descarta a los demás", async () => {
    const app = loadApp();
    app.login(userA);
    app.useStore.getState().enqueue(queued("x", { kind: "otro" }));
    seed(app, "a");
    const post = jest.fn(async () => ({}));
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.flush());
    expect(post).toHaveBeenCalledTimes(1);
    expect(app.useStore.getState().items.map((i) => i.id)).toEqual(["x"]);
  });
});

describe("5. clasificación de respuestas en el vaciado", () => {
  async function flushWith(err: unknown | null) {
    const app = loadApp();
    app.login(userA);
    seed(app, "a");
    const post = jest.fn(async () => {
      if (err) throw err;
      return {};
    });
    const onItemFailed = jest.fn();
    const onItemConflict = jest.fn();
    const m2 = app.rtl.renderHook(
      () => app.hook.useOfflineSubmit({ handlers: { k: { post } }, onItemFailed, onItemConflict }),
      {
        wrapper: ({ children }: { children: React.ReactNode }) =>
          app.React.createElement(app.rq.QueryClientProvider, { client: app.qc }, children),
      },
    );
    await app.rtl.act(() => m2.result.current.flush());
    const s = app.useStore.getState();
    const second = async () => app.rtl.act(() => m2.result.current.flush());
    return { app, post, s, onItemFailed, onItemConflict, second, unmount: m2.unmount };
  }

  it.each([[200], [201], [204]])("%i resuelto => éxito, sale de la cola", async () => {
    const { s } = await flushWith(null);
    expect([s.items, s.failed, s.conflicts]).toEqual([[], [], []]);
  });

  it("409 DUPLICATE_IDEMPOTENT => éxito", async () => {
    const { s } = await flushWith(codeError(409, "DUPLICATE_IDEMPOTENT"));
    expect([s.items, s.failed, s.conflicts]).toEqual([[], [], []]);
  });

  it("409 INVALID_TRANSITION => sale de la cola, queda en conflicts, avisa y se recupera con dismissConflict", async () => {
    const { app, s, post, onItemConflict, second } = await flushWith(
      codeError(409, "INVALID_TRANSITION"),
    );
    expect(s.items).toEqual([]);
    expect(s.failed).toEqual([]);
    expect(s.conflicts.map((c) => c.id)).toEqual(["a"]);
    expect(s.conflicts[0]!.status).toBe(409);
    expect(onItemConflict).toHaveBeenCalledTimes(1);
    expect(onItemConflict.mock.calls[0][0].payload).toEqual({ id: "a" });
    await second();
    expect(post).toHaveBeenCalledTimes(1); // sin reintento
    // sobrevive a recargar (no se pierde en silencio)
    const app2 = loadApp();
    expect(app2.useStore.getState().conflicts.map((c) => c.id)).toEqual(["a"]);
    app.useStore.getState().dismissConflict("a");
    expect(app.useStore.getState().conflicts).toEqual([]);
    expect(app.useStore.getState().failed).toEqual([]);
  });

  it.each([
    [409, { detail: { code: "CONFLICT", message: "x" } }],
    [422, { detail: [{ loc: ["body"], msg: "bad" }] }],
    [403, { detail: { code: "FORBIDDEN", message: "x" } }],
    [404, { detail: { code: "NOT_FOUND", message: "x" } }],
    [400, {}],
    [409, "<html>conflict</html>"],
    [409, null],
    [409, { detail: "DUPLICATE_IDEMPOTENT" }],
    [422, undefined],
  ])("%i (%j) => fallo permanente, visible, sin reintento", async (status, body) => {
    const { s, post, onItemFailed, second } = await flushWith(httpError(status, body));
    expect(s.items).toEqual([]);
    expect(s.conflicts).toEqual([]);
    expect(s.failed).toHaveLength(1);
    expect(s.failed[0]).toMatchObject({ id: "a", status });
    expect(onItemFailed).toHaveBeenCalledTimes(1);
    await second();
    await second();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it.each([[500], [502], [503], [504], [408], [425], [429], [401]])(
    "%i => queda en cola, no gasta intentos, 50 flush seguidos no lo descartan",
    async (status) => {
      const { app, post, second, s } = await flushWith(httpError(status, "<html>gateway</html>"));
      expect(s.items).toHaveLength(1);
      for (let i = 0; i < 50; i += 1) await second();
      const st = app.useStore.getState();
      expect(st.items).toHaveLength(1);
      expect(st.items[0]!.attempts).toBe(0);
      expect(st.failed).toEqual([]);
      expect(post).toHaveBeenCalledTimes(51);
    },
  );

  it.each([
    ["red", () => networkError()],
    ["timeout", () => new AxiosError("timeout", "ECONNABORTED")],
    ["abort", () => new AxiosError("canceled", "ERR_CANCELED")],
  ])("%s => queda en cola sin gastar intentos", async (_n, mk) => {
    const { s } = await flushWith(mk());
    expect(s.items).toHaveLength(1);
    expect(s.items[0]!.attempts).toBe(0);
  });

  it("403 PASSWORD_CHANGE_REQUIRED => no gasta intentos ni va a failed", async () => {
    const { s } = await flushWith(codeError(403, "PASSWORD_CHANGE_REQUIRED"));
    expect(s.items).toHaveLength(1);
    expect(s.failed).toEqual([]);
  });

  it("HALLAZGO: error NO http del handler (bug/TypeError) queda 'pendiente' para siempre sin aparecer en failed", async () => {
    const app = loadApp();
    app.login(userA);
    seed(app, "a");
    const post = jest.fn(async () => {
      throw new TypeError("x is undefined");
    });
    const { result } = app.mount({ k: { post } });
    for (let i = 0; i < 20; i += 1) await app.rtl.act(() => result.current.flush());
    const s = app.useStore.getState();
    // un ítem veneno debería terminar visible (failed) y no bloquear el contador eternamente
    expect(s.items.length + s.failed.length).toBe(1);
    expect(s.failed).toHaveLength(1);
  });

  it("un transitorio en el 1ro NO frena a un ítem de otro kind? (corta todo: documenta)", async () => {
    const app = loadApp();
    app.login(userA);
    seed(app, "a", "b");
    const post = jest.fn(async (_p: unknown, k: string) => {
      if (k === "a") throw httpError(503);
      return {};
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.flush());
    expect(post.mock.calls.map((c) => c[1])).toEqual(["a"]);
  });

  it("un 4xx permanente del 1ro no impide que se manden los siguientes", async () => {
    const app = loadApp();
    app.login(userA);
    seed(app, "a", "b");
    const post = jest.fn(async (_p: unknown, k: string) => {
      if (k === "a") throw httpError(422);
      return {};
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.flush());
    expect(post.mock.calls.map((c) => c[1])).toEqual(["a", "b"]);
    expect(app.useStore.getState().failed.map((f) => f.id)).toEqual(["a"]);
  });

  it("retryFailed devuelve el fallido a la cola con intentos en cero y se reenvía con la misma key", async () => {
    const app = loadApp();
    app.login(userA);
    seed(app, "a");
    let fail = true;
    const post = jest.fn(async (_p: unknown, _k: string) => {
      if (fail) throw httpError(422);
      return {};
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.flush());
    fail = false;
    app.useStore.getState().retryFailed("a");
    await app.rtl.act(() => result.current.flush());
    expect(post.mock.calls.map((c) => c[1])).toEqual(["a", "a"]);
    expect(app.useStore.getState().failed).toEqual([]);
  });

  it("submit en línea: 409 INVALID_TRANSITION va a onConflict (o a onError si no hay), 409 duplicado es éxito, 4xx no encola", async () => {
    const app = loadApp();
    app.login(userA);
    const errs: unknown[] = [
      codeError(409, "INVALID_TRANSITION"),
      codeError(409, "DUPLICATE_IDEMPOTENT"),
      httpError(422),
    ];
    const post = jest.fn(async () => {
      throw errs.shift();
    });
    const { result } = app.mount({ k: { post } });
    const cb = () => ({
      onSuccess: jest.fn(),
      onQueued: jest.fn(),
      onError: jest.fn(),
      onConflict: jest.fn(),
    });
    const c1 = cb();
    const c2 = cb();
    const c3 = cb();
    await app.rtl.act(() => result.current.submit("k", {}, c1));
    await app.rtl.act(() => result.current.submit("k", {}, c2));
    await app.rtl.act(() => result.current.submit("k", {}, c3));
    expect(c1.onConflict).toHaveBeenCalled();
    expect(c2.onSuccess).toHaveBeenCalled();
    expect(c3.onError).toHaveBeenCalled();
    expect(app.useStore.getState().items).toEqual([]);
  });

  it("submit en línea: 5xx/429/408 se encolan (no se pierde el toque)", async () => {
    const app = loadApp();
    app.login(userA);
    const errs = [httpError(503), httpError(429), httpError(408), httpError(502, "<html>")];
    const post = jest.fn(async () => {
      throw errs.shift();
    });
    const { result } = app.mount({ k: { post } });
    for (let i = 0; i < 4; i += 1) await app.rtl.act(() => result.current.submit("k", { i }));
    expect(app.useStore.getState().items).toHaveLength(4);
  });
});

describe("5b. reintento: backoff y bucles", () => {
  it("tras un 503 NO hay bucle: pasadas 24 h sin eventos el ítem se intentó solo una vez (no satura)", async () => {
    jest.useFakeTimers();
    const app = loadApp();
    app.login(userA);
    seed(app, "a");
    const post = jest.fn(async () => {
      throw httpError(503);
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.flush());
    await app.rtl.act(async () => {
      jest.advanceTimersByTime(24 * 3600_000);
    });
    expect(post.mock.calls.length).toBeLessThanOrEqual(5);
  });

  it("HALLAZGO: tras un 503 con la app abierta y en línea, el ítem se reintenta SOLO más tarde (backoff programado)", async () => {
    jest.useFakeTimers();
    const app = loadApp();
    app.login(userA);
    seed(app, "a");
    let up = false;
    const post = jest.fn(async () => {
      if (!up) throw httpError(503);
      return {};
    });
    const { result } = app.mount({ k: { post } }, true);
    await app.rtl.act(() => result.current.flush());
    up = true; // el backend se recupera; no hay evento `online` (nunca se cayó la red)
    await app.rtl.act(async () => {
      jest.advanceTimersByTime(10 * 60_000);
    });
    await app.rtl.act(async () => {
      await Promise.resolve();
    });
    expect(app.useStore.getState().items).toEqual([]); // quedó pendiente "para siempre" hasta reload/online
  });

  it("HALLAZGO: ráfaga de eventos `online` tras un 503/429 no tiene backoff: cada evento dispara un POST", async () => {
    jest.useFakeTimers();
    const app = loadApp();
    app.login(userA);
    seed(app, "a");
    const post = jest.fn(async () => {
      throw httpError(429);
    });
    app.mount({ k: { post } }, true);
    for (let i = 0; i < 30; i += 1) {
      await app.rtl.act(async () => {
        window.dispatchEvent(new Event("online"));
        await Promise.resolve();
        await Promise.resolve();
      });
      await app.rtl.act(async () => {
        jest.advanceTimersByTime(500); // 30 eventos en 15 s: señal que flapea
      });
    }
    expect(post.mock.calls.length).toBeLessThanOrEqual(5);
  });

  it("el estado persistido nunca guarda attempts>0 por transitorios (no se gasta el ítem)", async () => {
    const app = loadApp();
    app.login(userA);
    seed(app, "a");
    const post = jest.fn(async () => {
      throw httpError(503);
    });
    const { result } = app.mount({ k: { post } });
    for (let i = 0; i < 10; i += 1) await app.rtl.act(() => result.current.flush());
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
    expect(raw.state.items[0].attempts).toBe(0);
    expect(raw.version).toBe(4);
  });
});
