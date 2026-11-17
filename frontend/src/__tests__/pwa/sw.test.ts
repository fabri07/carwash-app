/**
 * @jest-environment node
 */

/**
 * La estrategia del service worker se testea, no se confía (ADR-0011).
 * `public/sw.js` se carga tal cual se sirve, con `caches` y `fetch` simulados.
 */

class MemoryCache {
  store = new Map<string, Response>();
  async match(req: Request | string) {
    const r = this.store.get(typeof req === "string" ? req : req.url);
    return r ? r.clone() : undefined;
  }
  async put(req: Request | string, res: Response) {
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
  async match(url: string) {
    for (const c of this.caches.values()) {
      const hit = await c.match(url);
      if (hit) return hit;
    }
    return undefined;
  }
}

const ORIGIN = "https://app.carwash.app";

// eslint-disable-next-line @typescript-eslint/no-require-imports
const sw = require("../../../public/sw.js") as {
  strategyFor: (req: Request, origin: string) => string;
  handleFetch: (req: Request, origin?: string) => Promise<Response>;
  cleanupOldCaches: () => Promise<void>;
  STATIC_CACHE: string;
  PAGES_CACHE: string;
};

function navigate(url: string): Request {
  // `mode: "navigate"` no se puede construir con `new Request`; se simula.
  const r = new Request(url);
  Object.defineProperty(r, "mode", { value: "navigate" });
  return r;
}

describe("service worker", () => {
  let storage: MemoryCacheStorage;
  let fetchMock: jest.Mock;

  beforeEach(() => {
    storage = new MemoryCacheStorage();
    (globalThis as unknown as { caches: MemoryCacheStorage }).caches = storage;
    fetchMock = jest.fn(async () => new Response("red", { status: 200 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  it("una navegación va a la red primero", async () => {
    await storage
      .open(sw.PAGES_CACHE)
      .then((c) => c.put(`${ORIGIN}/dashboard`, new Response("viejo")));
    const res = await sw.handleFetch(navigate(`${ORIGIN}/dashboard`), ORIGIN);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(await res.text()).toBe("red");
  });

  it("sin red, una navegación cae a la caché", async () => {
    await storage
      .open(sw.PAGES_CACHE)
      .then((c) => c.put(`${ORIGIN}/dashboard`, new Response("guardado")));
    fetchMock.mockRejectedValueOnce(new TypeError("offline"));
    const res = await sw.handleFetch(navigate(`${ORIGIN}/dashboard`), ORIGIN);
    expect(await res.text()).toBe("guardado");
  });

  it("sin red y sin caché, la navegación falla (no inventa nada)", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("offline"));
    await expect(sw.handleFetch(navigate(`${ORIGIN}/nada`), ORIGIN)).rejects.toThrow("offline");
  });

  it("no guarda redirects (p. ej. el 307 a /login del middleware)", async () => {
    const redirected = new Response("login", { status: 200 });
    Object.defineProperty(redirected, "redirected", { value: true });
    fetchMock.mockResolvedValueOnce(redirected);
    await sw.handleFetch(navigate(`${ORIGIN}/dashboard`), ORIGIN);
    expect(await storage.match(`${ORIGIN}/dashboard`)).toBeUndefined();
  });

  it("un asset con hash sale de la caché sin tocar la red", async () => {
    const url = `${ORIGIN}/_next/static/chunks/app-abc123.js`;
    await sw.handleFetch(new Request(url), ORIGIN); // primera vez: red + guarda
    fetchMock.mockClear();
    const res = await sw.handleFetch(new Request(url), ORIGIN);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(await res.text()).toBe("red");
  });

  it("una respuesta de la API nunca se guarda en caché", async () => {
    const url = "https://api.carwash.app/v1/dummy-resources";
    expect(sw.strategyFor(new Request(url), ORIGIN)).toBe("network-only");
    await sw.handleFetch(new Request(url), ORIGIN);
    expect(await storage.match(url)).toBeUndefined();
    const local = `${ORIGIN}/api/algo`;
    expect(sw.strategyFor(navigate(local), ORIGIN)).toBe("network-only");
  });

  /**
   * Verificado en 6.3 (no se rehízo): el manifest es instalable (manifest.test),
   * el SW no precachea, solo cachea `/_next/static/**` y navegaciones sin
   * redirect, y la API (cualquier origen, `/api/*` y `/v1/*`) va siempre a la red.
   * La cola offline de datos vive en `offlineQueueStore`, no en este SW.
   */
  it("`/v1/*` del mismo origen (incluso como navegación o con cookies) nunca se cachea", async () => {
    for (const path of ["/v1/agenda/day?date=2026-11-17", "/v1/jobs/j1", "/v1/"]) {
      const url = `${ORIGIN}${path}`;
      expect(sw.strategyFor(new Request(url), ORIGIN)).toBe("network-only");
      expect(sw.strategyFor(navigate(url), ORIGIN)).toBe("network-only");
      await sw.handleFetch(new Request(url, { headers: { cookie: "session=x" } }), ORIGIN);
      expect(await storage.match(url)).toBeUndefined();
    }
  });

  it("una navegación con query no se guarda: sin red no se sirve la agenda de OTRO día", async () => {
    const a = `${ORIGIN}/agenda?fecha=2026-11-16`;
    const b = `${ORIGIN}/agenda?fecha=2026-11-17`;
    fetchMock.mockResolvedValueOnce(new Response("agenda A", { status: 200 }));
    await sw.handleFetch(navigate(a), ORIGIN);
    expect(await storage.match(a)).toBeUndefined();
    // Sin red, ni A ni B salen de la caché.
    fetchMock.mockRejectedValue(new Error("offline"));
    await expect(sw.handleFetch(navigate(b), ORIGIN)).rejects.toThrow("offline");
    await expect(sw.handleFetch(navigate(a), ORIGIN)).rejects.toThrow("offline");
  });

  it("ni un POST ni el propio sw.js pasan por la caché", () => {
    expect(sw.strategyFor(new Request(`${ORIGIN}/dashboard`, { method: "POST" }), ORIGIN)).toBe(
      "network-only",
    );
    expect(sw.strategyFor(new Request(`${ORIGIN}/sw.js`), ORIGIN)).toBe("network-only");
    expect(sw.strategyFor(new Request(`${ORIGIN}/_next/image?url=x`), ORIGIN)).toBe(
      "network-first",
    );
    expect(sw.strategyFor(new Request(`${ORIGIN}/icons/icon-192.png`), ORIGIN)).toBe(
      "network-only",
    );
  });

  it("no hay precaching: el archivo no declara lista de URLs ni cachea en install", () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const src = require("node:fs").readFileSync(
      require.resolve("../../../public/sw.js"),
      "utf8",
    ) as string;
    expect(src).not.toMatch(/addAll\(/);
    expect(src).toMatch(/skipWaiting\(\)/);
    expect(src).toMatch(/clients\.claim\(\)/);
  });

  it("al activar, borra cachés propias de versiones anteriores y respeta las ajenas", async () => {
    await storage.open("carwash-static-v0");
    await storage.open("otra-app");
    await storage.open(sw.STATIC_CACHE);
    await sw.cleanupOldCaches();
    expect(await storage.keys()).toEqual(["otra-app", sw.STATIC_CACHE]);
  });
});
