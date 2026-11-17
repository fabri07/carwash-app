/**
 * @jest-environment node
 */

/**
 * T3 6.3 — ataque 7: service worker. `/v1/*` y todo lo autenticado nunca se
 * cachea; las páginas cacheadas no sobreviven al cierre de sesión.
 */

export {};

class MemoryCache {
  store = new Map<string, Response>();
  failPut = false;
  async match(req: Request | string) {
    const r = this.store.get(typeof req === "string" ? req : req.url);
    return r ? r.clone() : undefined;
  }
  async put(req: Request | string, res: Response) {
    if (this.failPut) throw new Error("QuotaExceededError");
    this.store.set(typeof req === "string" ? req : req.url, res);
  }
}
class MemoryCacheStorage {
  caches = new Map<string, MemoryCache>();
  async open(name: string) {
    if (!this.caches.has(name)) this.caches.set(name, new MemoryCache());
    return this.caches.get(name)!;
  }
  async keys() {
    return [...this.caches.keys()];
  }
  async delete(name: string) {
    return this.caches.delete(name);
  }
  totalEntries() {
    return [...this.caches.values()].reduce((n, c) => n + c.store.size, 0);
  }
}

const ORIGIN = "https://app.carwash.app";

// eslint-disable-next-line @typescript-eslint/no-require-imports
const sw = require("../../../public/sw.js") as {
  strategyFor: (req: Request, origin: string) => string;
  handleFetch: (req: Request, origin?: string) => Promise<Response>;
  PAGES_CACHE: string;
  STATIC_CACHE: string;
};

function req(url: string, init: Omit<RequestInit, "mode"> & { mode?: string } = {}): Request {
  const { mode, ...rest } = init;
  const r = new Request(url, rest);
  if (mode) Object.defineProperty(r, "mode", { value: mode });
  return r;
}
const nav = (url: string, init: Omit<RequestInit, "mode"> = {}) =>
  req(url, { ...init, mode: "navigate" });

let storage: MemoryCacheStorage;
let fetchMock: jest.Mock;
beforeEach(() => {
  storage = new MemoryCacheStorage();
  (globalThis as unknown as { caches: MemoryCacheStorage }).caches = storage;
  fetchMock = jest.fn(async () => new Response("{}", { status: 200 }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

describe("7a. la API y lo autenticado nunca se guarda", () => {
  const apiPaths = [
    "/v1/jobs",
    "/v1/agenda/day?date=2026-11-17",
    "/v1/auth/me",
    "/v1/jobs/abc/events",
    "/api/auth/session",
    "/api/anything",
  ];
  const methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];

  it.each(apiPaths)(
    "GET fetch/navegación a %s: network-only y nada en cachés (con Authorization y cookie)",
    async (path) => {
      for (const mode of ["cors", "same-origin", "navigate"]) {
        const r = req(`${ORIGIN}${path}`, {
          mode,
          headers: { Authorization: "Bearer x", Cookie: "access_token=abc" },
        });
        expect(sw.strategyFor(r, ORIGIN)).toBe("network-only");
        await sw.handleFetch(r, ORIGIN);
      }
      expect(storage.totalEntries()).toBe(0);
      expect(storage.caches.size).toBe(0);
    },
  );

  it.each(methods.filter((m) => m !== "GET"))(
    "método %s a cualquier ruta nunca toca la caché",
    async (m) => {
      for (const path of ["/v1/jobs", "/dashboard", "/_next/static/a.js", "/agenda"]) {
        const r = req(`${ORIGIN}${path}`, {
          method: m,
          mode: "navigate",
          headers: { Authorization: "Bearer x" },
        });
        expect(sw.strategyFor(r, ORIGIN)).toBe("network-only");
        await sw.handleFetch(r, ORIGIN);
      }
      expect(storage.totalEntries()).toBe(0);
    },
  );

  it("otro origen (la API en api.carwash.app) siempre network-only", async () => {
    for (const mode of ["cors", "navigate"]) {
      const r = req("https://api.carwash.app/v1/jobs", { mode });
      expect(sw.strategyFor(r, ORIGIN)).toBe("network-only");
    }
  });

  it("variantes de ruta de la API: /v1 y /api sin barra final, doble barra, mayúsculas, %2F", () => {
    const bad: string[] = [];
    for (const p of [
      "/v1",
      "/api",
      "//v1/jobs",
      "/v1?x=1",
      "/api?x=1",
      "/%76%31/jobs",
      "/V1/jobs",
      "/v1%2Fjobs",
    ]) {
      const s = sw.strategyFor(nav(`${ORIGIN}${p}`), ORIGIN);
      if (s !== "network-only") bad.push(`${p} -> ${s}`);
    }
    // /V1 en mayúsculas no es la API (el servidor distingue); lo demás debería ser network-only
    expect(bad.filter((b) => !b.startsWith("/V1/"))).toEqual([]);
  });

  it("`Cache-Control: no-store/private`, `Set-Cookie` o `Vary: Cookie` en una navegación NO deben guardarse", async () => {
    const cases: Array<[string, Record<string, string>]> = [
      ["/dashboard", { "Cache-Control": "no-store" }],
      ["/agenda", { "Cache-Control": "private, no-cache" }],
      ["/operacion", { "Set-Cookie": "access_token=abc; HttpOnly" }],
      ["/turnos", { Vary: "Cookie" }],
    ];
    const leaked: string[] = [];
    for (const [path, headers] of cases) {
      fetchMock.mockResolvedValueOnce(
        new Response("<html>datos del usuario A</html>", { status: 200, headers }),
      );
      await sw.handleFetch(nav(`${ORIGIN}${path}`), ORIGIN);
      if (storage.totalEntries() > leaked.length) leaked.push(path);
    }
    expect(leaked).toEqual([]);
  });

  it.each([[301], [302], [307], [401], [403], [404], [500], [503]])(
    "una navegación con respuesta %i no se guarda",
    async (status) => {
      fetchMock.mockResolvedValueOnce(new Response("x", { status }));
      await sw.handleFetch(nav(`${ORIGIN}/dashboard`), ORIGIN);
      expect(storage.totalEntries()).toBe(0);
    },
  );

  it("un 200 que viene de un redirect (login → /dashboard) no se guarda", async () => {
    const res = new Response("<html>login</html>", { status: 200 });
    Object.defineProperty(res, "redirected", { value: true });
    fetchMock.mockResolvedValueOnce(res);
    await sw.handleFetch(nav(`${ORIGIN}/dashboard`), ORIGIN);
    expect(storage.totalEntries()).toBe(0);
  });
});

describe("7b. rutas protegidas tras logout / otro usuario", () => {
  it("tras vaciar las cachés (logout), sin red no se sirve la página del usuario anterior", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<html>pagina de A</html>", { status: 200 }));
    await sw.handleFetch(nav(`${ORIGIN}/dashboard`), ORIGIN);
    expect(storage.totalEntries()).toBe(1);
    for (const k of await storage.keys()) await storage.delete(k); // lo que hace clearSwCaches
    fetchMock.mockRejectedValue(new TypeError("offline"));
    await expect(sw.handleFetch(nav(`${ORIGIN}/dashboard`), ORIGIN)).rejects.toThrow();
  });

  it("DOCUMENTA: sin limpiar las cachés (p. ej. sesión vencida), offline se sirve la página cacheada del usuario A a quien llegue después", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<html>facturación de A</html>", { status: 200 }));
    await sw.handleFetch(nav(`${ORIGIN}/dashboard`), ORIGIN);
    fetchMock.mockRejectedValue(new TypeError("offline"));
    const res = await sw.handleFetch(nav(`${ORIGIN}/dashboard`), ORIGIN);
    expect(await res.text()).toContain("de A");
  });

  it("el cache de páginas no crece sin tope: 300 navegaciones distintas", async () => {
    for (let i = 0; i < 300; i += 1) {
      fetchMock.mockResolvedValueOnce(new Response(`p${i}`, { status: 200 }));
      await sw.handleFetch(nav(`${ORIGIN}/turnos?page=${i}`), ORIGIN);
    }
    expect(storage.totalEntries()).toBeLessThanOrEqual(100);
  });
});

describe("7c. robustez del SW ante caché llena", () => {
  it("HALLAZGO: si cache.put falla (cuota), la navegación igual debe devolver la respuesta de la red", async () => {
    const c = await storage.open(sw.PAGES_CACHE);
    c.failPut = true;
    fetchMock.mockResolvedValueOnce(new Response("<html>ok</html>", { status: 200 }));
    const res = await sw.handleFetch(nav(`${ORIGIN}/dashboard`), ORIGIN);
    expect(await res.text()).toBe("<html>ok</html>");
  });

  it("HALLAZGO: si cache.put falla (cuota), un asset de /_next/static/ igual debe cargar", async () => {
    const c = await storage.open(sw.STATIC_CACHE);
    c.failPut = true;
    fetchMock.mockResolvedValueOnce(new Response("js", { status: 200 }));
    const res = await sw.handleFetch(req(`${ORIGIN}/_next/static/chunks/a.js`), ORIGIN);
    expect(await res.text()).toBe("js");
  });
});

describe("7d. listeners del SW", () => {
  it("el evento fetch de /v1/* o no-GET no llama a respondWith (lo maneja el navegador)", () => {
    const listeners: Record<string, (e: unknown) => void> = {};
    (globalThis as unknown as { self: unknown }).self = {
      addEventListener: (n: string, cb: (e: unknown) => void) => {
        listeners[n] = cb;
      },
      skipWaiting: jest.fn(),
      location: { origin: ORIGIN },
      clients: { claim: jest.fn() },
    };
    jest.isolateModules(() => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      require("../../../public/sw.js");
    });
    for (const r of [
      nav(`${ORIGIN}/v1/jobs`),
      req(`${ORIGIN}/v1/jobs`, { method: "POST" }),
      req(`${ORIGIN}/dashboard`, { method: "POST", mode: "navigate" }),
    ]) {
      const respondWith = jest.fn();
      listeners.fetch!({ request: r, respondWith });
      expect(respondWith).not.toHaveBeenCalled();
    }
    delete (globalThis as { self?: unknown }).self;
  });
});
