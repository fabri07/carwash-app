/**
 * T3 6.3 — ataque 6: fugas de PII, cuota de almacenamiento llena y
 * consistencia entre pestañas / storage corrupto.
 */
import {
  STORAGE_KEY,
  deferred,
  loadApp,
  networkError,
  queued,
  resetBrowser,
  unmountAll,
  userA,
} from "./helpers";

beforeEach(() => {
  resetBrowser();
});
afterEach(() => {
  unmountAll();
  jest.restoreAllMocks();
  resetBrowser();
});

const PATENTE = "AB123CD";
const TEL = "+54 9 351 000-0001";
const MAIL = "cliente@example.com";

function quotaFull() {
  const real = Storage.prototype.setItem;
  const spy = jest.spyOn(Storage.prototype, "setItem").mockImplementation(function (
    this: Storage,
    k: string,
    v: string,
  ) {
    if (k === STORAGE_KEY) throw new DOMException("quota", "QuotaExceededError");
    return real.call(this, k, v);
  });
  return spy;
}

describe("6a. PII en localStorage", () => {
  it("la cola NO guarda credenciales ni el email de la sesión ni cookies/tokens", async () => {
    const app = loadApp();
    app.login(userA);
    const post = jest.fn(async () => {
      throw networkError();
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.submit("k", { patente: PATENTE }));
    const raw = localStorage.getItem(STORAGE_KEY)!;
    expect(raw).not.toMatch(/token|password|cookie|authorization|a@example\.com/i);
  });

  it("lastError/estado de un 422 que ECO del dato personal no copia el cuerpo de la respuesta", async () => {
    const app = loadApp();
    app.login(userA);
    app.useStore.getState().enqueue(queued("a", { payload: { patente: PATENTE } }));
    const { httpError } = await import("./helpers");
    const post = jest.fn(async () => {
      throw httpError(422, { detail: [{ msg: `patente ${PATENTE} inválida`, input: TEL }] });
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.flush());
    const f = app.useStore.getState().failed[0];
    expect(f?.lastError).not.toContain(PATENTE);
    expect(f?.lastError).not.toContain(TEL);
  });

  it("DOCUMENTA (Low/info): el payload (patente, teléfono, email del cliente) queda en claro en localStorage y sobrevive al logout", async () => {
    const app = loadApp();
    app.login(userA);
    const post = jest.fn(async () => {
      throw networkError();
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() =>
      result.current.submit("k", { patente: PATENTE, telefono: TEL, email: MAIL }),
    );
    app.login(null); // logout: la cola no se borra a propósito
    const raw = localStorage.getItem(STORAGE_KEY)!;
    expect(raw).toContain(PATENTE);
    expect(raw).toContain(TEL);
    expect(raw).toContain(MAIL);
  });

  it("DOCUMENTA: un fallido descartado (dismissFailed) y un conflicto descartado borran el payload del storage", async () => {
    const app = loadApp();
    const s = app.useStore.getState();
    s.enqueue(queued("a", { payload: { patente: PATENTE } }));
    s.moveToFailed("a", "e", 422);
    s.enqueue(queued("b", { payload: { patente: PATENTE } }));
    s.moveToConflict("b", "e", 409);
    expect(localStorage.getItem(STORAGE_KEY)).toContain(PATENTE);
    app.useStore.getState().dismissFailed("a");
    app.useStore.getState().dismissConflict("b");
    expect(localStorage.getItem(STORAGE_KEY)).not.toContain(PATENTE);
  });

  it("un ítem exitoso no deja el payload en el storage", async () => {
    const app = loadApp();
    app.login(userA);
    app.useStore.getState().enqueue(queued("a", { payload: { patente: PATENTE } }));
    const post = jest.fn(async () => ({}));
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.flush());
    expect(localStorage.getItem(STORAGE_KEY)).not.toContain(PATENTE);
  });
});

describe("6b. cuota de almacenamiento llena", () => {
  it("storage lleno al encolar: NO se confirma como guardado (onError), no queda en memoria y no se manda después", async () => {
    const app = loadApp();
    app.login(userA);
    quotaFull();
    const post = jest.fn(async () => {
      throw networkError();
    });
    const { result } = app.mount({ k: { post } });
    const cb = { onQueued: jest.fn(), onError: jest.fn(), onSuccess: jest.fn() };
    await app.rtl.act(() =>
      result.current.submit("k", { a: 1 }, { ...cb, dedupeKey: "iniciar:j1" }),
    );
    expect(cb.onQueued).not.toHaveBeenCalled();
    expect(cb.onSuccess).not.toHaveBeenCalled();
    expect(cb.onError).toHaveBeenCalledTimes(1);
    expect(app.useStore.getState().items).toEqual([]);
    post.mockClear();
    await app.rtl.act(() => result.current.flush());
    expect(post).not.toHaveBeenCalled();
  });

  it("cuota REAL de jsdom (payload de 6 MB): el toque no se pierde en silencio", async () => {
    const app = loadApp();
    app.login(userA);
    const post = jest.fn(async () => {
      throw networkError();
    });
    const { result } = app.mount({ k: { post } });
    const cb = { onQueued: jest.fn(), onError: jest.fn() };
    await app.rtl.act(() => result.current.submit("k", { foto: "x".repeat(6_000_000) }, cb));
    expect(cb.onQueued.mock.calls.length + cb.onError.mock.calls.length).toBe(1);
    expect(cb.onError).toHaveBeenCalled();
    expect(app.useStore.getState().items).toEqual([]);
  });

  it("storage lleno y dedupeKey: tras onError, el reintento del usuario puede encolar cuando hay lugar", async () => {
    const app = loadApp();
    app.login(userA);
    const spy = quotaFull();
    const post = jest.fn(async () => {
      throw networkError();
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.submit("k", {}, { dedupeKey: "iniciar:j1" }));
    spy.mockRestore();
    const onQueued = jest.fn();
    await app.rtl.act(() => result.current.submit("k", {}, { dedupeKey: "iniciar:j1", onQueued }));
    expect(onQueued).toHaveBeenCalled();
    expect(app.useStore.getState().items).toHaveLength(1);
  });

  it("HALLAZGO (Low): storage lleno + respuesta perdida: el reintento del usuario usa OTRA Idempotency-Key (el servidor podría haber guardado el 1ro)", async () => {
    const app = loadApp();
    app.login(userA);
    const spy = quotaFull();
    const post = jest.fn(async (_p: unknown, _k: string) => {
      throw networkError();
    });
    const { result } = app.mount({ k: { post } });
    await app.rtl.act(() => result.current.submit("k", {}, { dedupeKey: "cobrar:j1" }));
    await app.rtl.act(() => result.current.submit("k", {}, { dedupeKey: "cobrar:j1" }));
    spy.mockRestore();
    const keys = post.mock.calls.map((c) => c[1]);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]); // idealmente la misma key por dedupeKey: HOY son distintas
  });

  it("HALLAZGO: el vaciado sobrevive a un storage que falla al borrar el ítem enviado (no rechaza ni deja el flag colgado)", async () => {
    const app = loadApp();
    app.login(userA);
    app.useStore.getState().enqueue(queued("a"));
    app.useStore.getState().enqueue(queued("b"));
    quotaFull();
    const post = jest.fn(async (_p: unknown, _k: string) => ({}));
    const { result } = app.mount({ k: { post } });
    await expect(app.rtl.act(() => result.current.flush())).resolves.not.toThrow();
    // un segundo vaciado sigue funcionando (el candado no quedó tomado)
    await expect(app.rtl.act(() => result.current.flush())).resolves.not.toThrow();
    expect(post.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("HALLAZGO: el listener `online` no produce promesas rechazadas sin manejar si el storage falla", async () => {
    const app = loadApp();
    app.login(userA);
    app.useStore.getState().enqueue(queued("a"));
    quotaFull();
    const unhandled = jest.fn();
    process.on("unhandledRejection", unhandled);
    const post = jest.fn(async () => ({}));
    app.mount({ k: { post } }, true);
    await app.rtl.act(async () => {
      window.dispatchEvent(new Event("online"));
      await new Promise((r) => setTimeout(r, 20));
    });
    process.off("unhandledRejection", unhandled);
    expect(unhandled).not.toHaveBeenCalled();
  });
});

describe("6c. storage corrupto y varias pestañas", () => {
  it("JSON corrupto en el storage no rompe el arranque", () => {
    localStorage.setItem(STORAGE_KEY, "{not json");
    const app = loadApp();
    expect(app.useStore.getState().items).toEqual([]);
  });

  it("HALLAZGO: dos pestañas encolando — la segunda en escribir pisa el storage y la carga de la primera se pierde al recargar", async () => {
    const tab1 = loadApp();
    const tab2 = loadApp(); // ambas arrancan con la cola vacía
    tab1.useStore.getState().enqueue(queued("de-pestana-1"));
    tab2.useStore.getState().enqueue(queued("de-pestana-2"));
    const reloaded = loadApp();
    expect(
      reloaded.useStore
        .getState()
        .items.map((i) => i.id)
        .sort(),
    ).toEqual(["de-pestana-1", "de-pestana-2"]);
  });

  it("una pestaña que termina de vaciar no deja ítems ya mandados por la otra en el storage", async () => {
    const tab1 = loadApp();
    tab1.login(userA);
    tab1.useStore.getState().enqueue(queued("a"));
    tab1.useStore.getState().enqueue(queued("b"));
    const tab2 = loadApp(); // ve a y b
    tab2.login(userA);
    const gate = deferred();
    const m1 = tab1.mount({
      k: {
        post: jest.fn(async (_p: unknown, k: string) => {
          if (k === "a") await gate.promise;
          return {};
        }),
      },
    });
    let p!: Promise<void>;
    await tab1.rtl.act(async () => {
      p = m1.result.current.flush();
    });
    // la pestaña 2 manda todo y vacía el storage
    const m2 = tab2.mount({ k: { post: jest.fn(async () => ({})) } });
    await tab2.rtl.act(() => m2.result.current.flush());
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).state.items).toEqual([]);
    gate.resolve({});
    await tab1.rtl.act(() => p);
    // el storage final debe quedar vacío: a y b ya están en el servidor
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).state.items).toEqual([]);
  });

  it("migración v3→v4 con failed previos los conserva", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        version: 3,
        state: {
          items: [],
          failed: [{ ...queued("f"), failedAt: "2026-11-10T00:00:00Z", status: 422 }],
        },
      }),
    );
    const app = loadApp();
    expect(app.useStore.getState().failed.map((f) => f.id)).toEqual(["f"]);
    expect(app.useStore.getState().conflicts).toEqual([]);
  });
});
