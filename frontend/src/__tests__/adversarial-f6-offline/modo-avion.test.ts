/**
 * T3 6.3 — ataques 1, 2 y 3: modo avión (doble/triple toque), `occurred_at` e
 * Idempotency-Key. Cada test carga una copia aislada de la app (`loadApp`).
 */
import {
  STORAGE_KEY,
  deferred,
  loadApp,
  networkError,
  occurredAtOf,
  queued,
  resetBrowser,
  setOnline,
  unmountAll,
  userA,
  userB,
  userC,
} from "./helpers";

type App = ReturnType<typeof loadApp>;

beforeEach(() => {
  resetBrowser();
});
afterEach(() => {
  unmountAll();
  jest.useRealTimers();
  resetBrowser();
});

/** Simula "recargar la página": nueva copia de la app leyendo el mismo localStorage. */
function reload(): App {
  return loadApp();
}

describe("1. modo avión: doble y triple toque", () => {
  it("triple toque SIMULTÁNEO del mismo botón => un solo envío y una sola entrada", async () => {
    const app = loadApp();
    app.login(userA);
    setOnline(false);
    const post = jest.fn(async () => {
      throw networkError();
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(async () => {
      await Promise.all([
        result.current.submit("k", { a: 1 }, { dedupeKey: "iniciar:j1" }),
        result.current.submit("k", { a: 1 }, { dedupeKey: "iniciar:j1" }),
        result.current.submit("k", { a: 1 }, { dedupeKey: "iniciar:j1" }),
      ]);
    });
    expect(post).toHaveBeenCalledTimes(1);
    expect(app.useStore.getState().items).toHaveLength(1);
  });

  it("triple toque SECUENCIAL ya encolado => una entrada y un solo POST (los otros ni se intentan)", async () => {
    const app = loadApp();
    app.login(userA);
    const post = jest.fn(async () => {
      throw networkError();
    });
    const { result } = app.mount({ k: { post } });
    for (let i = 0; i < 3; i += 1) {
      await app.rtl.act(() => result.current.submit("k", {}, { dedupeKey: "iniciar:j1" }));
    }
    expect(post).toHaveBeenCalledTimes(1);
    expect(app.useStore.getState().items).toHaveLength(1);
  });

  it("el segundo toque repetido avisa onQueued (el usuario ve que quedó guardado)", async () => {
    const app = loadApp();
    app.login(userA);
    const post = jest.fn(async () => {
      throw networkError();
    });
    const { result } = app.mount({ k: { post } });
    const onQueued = jest.fn();
    const onError = jest.fn();
    await app.rtl.act(() =>
      result.current.submit("k", {}, { dedupeKey: "iniciar:j1", onQueued, onError }),
    );
    await app.rtl.act(() =>
      result.current.submit("k", {}, { dedupeKey: "iniciar:j1", onQueued, onError }),
    );
    expect(onQueued).toHaveBeenCalledTimes(2);
    expect(onError).not.toHaveBeenCalled();
  });

  it("toques distintos (iniciar/finalizar del mismo job, mismo botón en otro job) se conservan todos", async () => {
    const app = loadApp();
    app.login(userA);
    const post = jest.fn(async () => {
      throw networkError();
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(async () => {
      await result.current.submit("k", { t: "i1" }, { dedupeKey: "iniciar:j1" });
      await result.current.submit("k", { t: "f1" }, { dedupeKey: "finalizar:j1" });
      await result.current.submit("k", { t: "i2" }, { dedupeKey: "iniciar:j2" });
    });
    expect(app.useStore.getState().items.map((i) => i.dedupeKey)).toEqual([
      "iniciar:j1",
      "finalizar:j1",
      "iniciar:j2",
    ]);
  });

  it("al volver la señal: un envío por toque, en orden FIFO, cada uno con su key", async () => {
    const app = loadApp();
    app.login(userA);
    setOnline(false);
    const sent: string[] = [];
    let offline = true;
    const post = jest.fn(async (p: unknown, key: string) => {
      if (offline) throw networkError();
      sent.push(`${(p as { t: string }).t}:${key}`);
      return {};
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(async () => {
      for (const t of ["i1", "i1", "f1", "f1", "f1", "i2"]) {
        await result.current.submit("k", { t }, { dedupeKey: `${t}` });
      }
    });
    const ids = app.useStore.getState().items.map((i) => i.id);
    expect(ids).toHaveLength(3);
    offline = false;
    setOnline(true);
    post.mockClear();
    await app.rtl.act(() => result.current.flush());
    expect(sent).toEqual([`i1:${ids[0]}`, `f1:${ids[1]}`, `i2:${ids[2]}`]);
    expect(app.useStore.getState().items).toEqual([]);
  });

  it("dedupe: la clave igual de otro usuario/tenant NO bloquea mi toque", async () => {
    const app = loadApp();
    app.login(userB);
    const post = jest.fn(async () => {
      throw networkError();
    });
    app.useStore.getState().enqueue(queued("a1", { dedupeKey: "iniciar:j1" })); // de userA
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.submit("k", {}, { dedupeKey: "iniciar:j1" }));
    expect(app.useStore.getState().items.map((i) => i.userId)).toEqual(["u1", "u2"]);
    // y a nivel store, mismo userId pero otro tenant tampoco se dedupe
    app.useStore.getState().enqueue(queued("c1", { tenantId: "t2", dedupeKey: "iniciar:j1" }));
    expect(app.useStore.getState().items).toHaveLength(3);
  });

  it("ítem encolado sin dedupeKey: toques repetidos no se deduplican (por diseño) — documenta", async () => {
    const app = loadApp();
    app.login(userA);
    const post = jest.fn(async () => {
      throw networkError();
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(async () => {
      await result.current.submit("k", {});
      await result.current.submit("k", {});
    });
    expect(app.useStore.getState().items).toHaveLength(2);
  });

  it("HALLAZGO orden: ya online, un toque nuevo NO debe adelantarse a un ítem pendiente del mismo job", async () => {
    // Cola: iniciar:j1 pendiente (se encoló en modo avión). Vuelve la señal y,
    // antes de que el vaciado termine, el usuario toca finalizar:j1. `submit`
    // manda directo y finalizar llega ANTES que iniciar (FIFO roto).
    const app = loadApp();
    app.login(userA);
    app.useStore
      .getState()
      .enqueue(queued("i1", { dedupeKey: "iniciar:j1", payload: { t: "i1" } }));
    const order: string[] = [];
    const post = jest.fn(async (p: unknown) => {
      order.push((p as { t: string }).t);
      return {};
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.submit("k", { t: "f1" }, { dedupeKey: "finalizar:j1" }));
    await app.rtl.act(() => result.current.flush());
    expect(order).toEqual(["i1", "f1"]);
  });

  it("HALLAZGO: retryFailed puede dejar dos ítems con la misma dedupeKey (se saltea el dedupe)", async () => {
    const app = loadApp();
    app.login(userA);
    const s = app.useStore.getState();
    s.enqueue(queued("x1", { dedupeKey: "iniciar:j1" }));
    s.moveToFailed("x1", "e", 422);
    s.enqueue(queued("x2", { dedupeKey: "iniciar:j1" })); // nuevo toque tras el fallo
    app.useStore.getState().retryFailed("x1");
    const keys = app.useStore.getState().items.filter((i) => i.dedupeKey === "iniciar:j1");
    expect(keys).toHaveLength(1);
  });
});

describe("2. occurred_at", () => {
  it("se fija al TOQUE y es idéntico en cada reintento aunque pase el tiempo y cambie el reloj", async () => {
    jest.useFakeTimers({ now: new Date("2026-11-17T10:00:00.000Z") });
    const app = loadApp();
    app.login(userA);
    setOnline(false);
    const post = jest.fn(async () => {
      throw networkError();
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.submit("k", {}, { dedupeKey: "iniciar:j1" }));
    const firstAttempt = occurredAtOf(post.mock.calls[0]);
    expect(firstAttempt).toBe("2026-11-17T10:00:00.000Z");

    // pasan 3 h y el dispositivo cambia de zona horaria y de fecha (reloj mal puesto)
    process.env.TZ = "Pacific/Auckland";
    jest.setSystemTime(new Date("2031-01-01T00:00:00.000Z"));
    setOnline(true);
    for (let i = 0; i < 3; i += 1) await app.rtl.act(() => result.current.flush());
    process.env.TZ = "UTC";
    const times = post.mock.calls.slice(1).map(occurredAtOf);
    expect(times).toEqual(Array(3).fill("2026-11-17T10:00:00.000Z"));
    expect(app.useStore.getState().items[0]!.occurredAt).toBe("2026-11-17T10:00:00.000Z");
  });

  it("tras recargar (v4 persistida) conserva occurredAt e id", async () => {
    jest.useFakeTimers({ now: new Date("2026-11-17T10:00:00.000Z") });
    const app = loadApp();
    app.login(userA);
    const post = jest.fn(async () => {
      throw networkError();
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.submit("k", { x: 1 }, { dedupeKey: "iniciar:j1" }));
    const before = app.useStore.getState().items[0];
    jest.setSystemTime(new Date("2026-11-18T08:00:00.000Z"));
    unmountAll();

    const app2 = reload();
    expect(app2.useStore.getState().items[0]).toEqual(before);
    app2.login(userA);
    const post2 = jest.fn(async () => ({}));
    const m2 = app2.mount({ k: { post: post2 } });
    await app2.rtl.act(() => m2.result.current.flush());
    expect(post2).toHaveBeenCalledWith({ x: 1 }, before!.id, {
      occurredAt: "2026-11-17T10:00:00.000Z",
    });
  });

  it("migración desde v3 (forma vieja real, sin occurredAt ni conflicts): sobrevive y usa createdAt", async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        version: 3,
        state: {
          items: [
            {
              id: "11111111-1111-4111-8111-111111111111",
              userId: "u1",
              tenantId: "t1",
              kind: "k",
              payload: { patente: "AB123CD" },
              createdAt: "2026-11-10T09:15:00.000Z",
              attempts: 2,
              lastError: "Sin conexión al sincronizar",
            },
          ],
          failed: [],
        },
      }),
    );
    const app = loadApp();
    app.login(userA);
    const st = app.useStore.getState();
    expect(st.conflicts).toEqual([]);
    expect(st.items).toHaveLength(1);
    const post = jest.fn(async () => ({}));
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.flush());
    expect(post).toHaveBeenCalledWith(
      { patente: "AB123CD" },
      "11111111-1111-4111-8111-111111111111",
      { occurredAt: "2026-11-10T09:15:00.000Z" },
    );
  });

  it("migración desde v1/v2: ítems sin dueño se conservan y NUNCA se mandan", async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        version: 1,
        state: {
          items: [
            { id: "old", kind: "k", payload: {}, createdAt: "2026-01-01T00:00:00Z", attempts: 0 },
          ],
        },
      }),
    );
    const app = loadApp();
    app.login(userA);
    const post = jest.fn(async () => ({}));
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.flush());
    expect(post).not.toHaveBeenCalled();
    expect(app.useStore.getState().items.map((i) => i.id)).toEqual(["old"]);
  });

  it("migración v1 sin `items` en el estado no rompe (estado vacío)", () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, state: {} }));
    const app = loadApp();
    expect(app.useStore.getState().items).toEqual([]);
    expect(app.useStore.getState().conflicts).toEqual([]);
  });

  it("occurredAt es ISO en UTC ('Z') sin importar la zona horaria del dispositivo", async () => {
    for (const tz of ["America/Argentina/Buenos_Aires", "Asia/Kolkata", "Pacific/Kiritimati"]) {
      process.env.TZ = tz;
      const app = loadApp();
      app.login(userA);
      const post = jest.fn(async () => {
        throw networkError();
      });
      const { result } = app.mount({ k: { post } });
      await app.rtl.act(() => result.current.submit("k", {}));
      const at = app.useStore.getState().items[0]!.occurredAt!;
      expect(at).toMatch(/Z$/);
      expect(new Date(at).toISOString()).toBe(at);
      unmountAll();
      localStorage.clear();
    }
    process.env.TZ = "UTC";
  });

  it("toque ONLINE exitoso: la hora que recibe el handler es la del toque, no la de la respuesta", async () => {
    jest.useFakeTimers({ now: new Date("2026-11-17T10:00:00.000Z") });
    const app = loadApp();
    app.login(userA);
    const gate = deferred();
    const post = jest.fn(async () => gate.promise);
    const { result } = app.mount({ k: { post } });
    let p!: Promise<void>;
    await app.rtl.act(async () => {
      p = result.current.submit("k", {});
    });
    jest.setSystemTime(new Date("2026-11-17T10:05:00.000Z"));
    gate.reject(networkError()); // la respuesta tarda 5 min y falla => se encola
    await app.rtl.act(() => p);
    expect(occurredAtOf(post.mock.calls[0])).toBe("2026-11-17T10:00:00.000Z");
    expect(app.useStore.getState().items[0]!.occurredAt).toBe("2026-11-17T10:00:00.000Z");
  });
});

describe("3. Idempotency-Key y aislamiento entre usuarios", () => {
  it("el id del ítem es la key en el POST directo, en el encolado y en todos los reintentos", async () => {
    const app = loadApp();
    app.login(userA);
    const post = jest.fn(async (_p: unknown, _k: string) => {
      throw networkError();
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.submit("k", {}));
    const id = app.useStore.getState().items[0]!.id;
    for (let i = 0; i < 4; i += 1) await app.rtl.act(() => result.current.flush());
    const keys = post.mock.calls.map((c) => c[1]);
    expect(keys).toHaveLength(5);
    expect(new Set(keys)).toEqual(new Set([id]));
    expect(id).toMatch(/^[0-9a-f-]{36}$/);
    expect(app.useStore.getState().items[0]!.id).toBe(id);
  });

  it("markFailed/noteTransient/retryFailed no cambian el id", () => {
    const app = loadApp();
    const s = app.useStore.getState();
    s.enqueue(queued("same"));
    s.noteTransient("same", "x");
    s.markFailed("same", "x");
    expect(app.useStore.getState().items[0]!.id).toBe("same");
    s.moveToFailed("same", "x", 422);
    s.retryFailed("same");
    expect(app.useStore.getState().items[0]!.id).toBe("same");
  });

  it("dos usuarios en el mismo navegador: cada flush manda SOLO lo suyo (logout/login con cola pendiente)", async () => {
    const app = loadApp();
    app.login(userA);
    const post = jest.fn(async (_p: unknown, _k: string) => ({}));
    const { result } = app.mount({ k: { post } });
    const s = app.useStore.getState();
    s.enqueue(queued("a1", { dedupeKey: "iniciar:j1" }));
    s.enqueue(queued("b1", { userId: "u2", dedupeKey: "iniciar:j1" }));
    s.enqueue(queued("c1", { tenantId: "t2", dedupeKey: "iniciar:j1" })); // u1 en otro tenant
    s.enqueue(queued("n1", { userId: "", tenantId: "" }));

    // A cierra sesión: nadie loguea => no se manda nada
    app.login(null);
    await app.rtl.act(() => result.current.flush());
    expect(post).not.toHaveBeenCalled();

    app.login(userB);
    await app.rtl.act(() => result.current.flush());
    expect(post.mock.calls.map((c) => c[1])).toEqual(["b1"]);

    app.login(userC);
    await app.rtl.act(() => result.current.flush());
    expect(post.mock.calls.map((c) => c[1])).toEqual(["b1", "c1"]);

    app.login(userA);
    await app.rtl.act(() => result.current.flush());
    expect(post.mock.calls.map((c) => c[1])).toEqual(["b1", "c1", "a1"]);
    // el huérfano (sin dueño) sigue guardado
    expect(app.useStore.getState().items.map((i) => i.id)).toEqual(["n1"]);
  });

  it("failed/conflicts/pending/estado de UI se filtran por dueño (no se filtran a otro usuario)", async () => {
    const app = loadApp();
    app.login(userB);
    const s = app.useStore.getState();
    s.enqueue(queued("a-fail"));
    s.moveToFailed("a-fail", "e", 422);
    s.enqueue(queued("a-conf"));
    s.moveToConflict("a-conf", "e", 409);
    s.enqueue(queued("a-pend"));
    const { result } = app.mountStatus();
    expect(result.current.pending).toBe(0);
    expect(result.current.failed).toEqual([]);
    expect(result.current.conflicts).toEqual([]);
    expect(result.current.needsAttention).toBe(false);
    app.rtl.act(() => app.login(userA));
    expect(result.current.pending).toBe(1);
    expect(result.current.failed).toHaveLength(1);
    expect(result.current.conflicts).toHaveLength(1);
    expect(result.current.needsAttention).toBe(true);
  });

  it("submit sin sesión: no encola ni manda (onError)", async () => {
    const app = loadApp();
    app.login(null);
    const post = jest.fn(async () => ({}));
    const { result } = app.mount({ k: { post } });
    const onError = jest.fn();
    await app.rtl.act(() => result.current.submit("k", {}, { onError }));
    expect(post).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalled();
    expect(app.useStore.getState().items).toEqual([]);
  });

  it("si la sesión cambia de usuario a mitad de un vaciado no se manda lo del anterior", async () => {
    const app = loadApp();
    app.login(userA);
    const s = app.useStore.getState();
    s.enqueue(queued("a1"));
    s.enqueue(queued("a2"));
    const gate = deferred();
    const post = jest.fn(async (_p: unknown, key: string) => {
      if (key === "a1") await gate.promise;
      return {};
    });
    const { result } = app.mount({ k: { post } });
    let p!: Promise<void>;
    await app.rtl.act(async () => {
      p = result.current.flush();
    });
    app.login(userB); // otro usuario entra en la misma tablet mientras a1 está en vuelo
    gate.resolve(undefined);
    await app.rtl.act(() => p);
    // a2 era de A; con la sesión ya de B no debería mandarse con las cookies de B
    expect(post.mock.calls.map((c) => c[1])).toEqual(["a1"]);
  });
});
