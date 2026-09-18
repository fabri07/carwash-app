import { clearSwCaches, registrarSW } from "@/lib/sw-register";

describe("registrarSW — interruptor de apagado (ADR-0011 §6)", () => {
  const original = process.env.NEXT_PUBLIC_SW_ENABLED;
  let register: jest.Mock;
  let unregisterSpy: jest.Mock;
  let cacheDelete: jest.Mock;

  beforeEach(() => {
    register = jest.fn(async () => ({}));
    unregisterSpy = jest.fn(async () => true);
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: {
        register,
        getRegistrations: jest.fn(async () => [{ unregister: unregisterSpy }]),
      },
    });
    cacheDelete = jest.fn(async () => true);
    (globalThis as unknown as { caches: unknown }).caches = {
      keys: jest.fn(async () => ["carwash-static-v1", "de-otra-app"]),
      delete: cacheDelete,
    };
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_SW_ENABLED = original;
  });

  it("con el interruptor apagado, desregistra el SW existente y limpia sus cachés", async () => {
    process.env.NEXT_PUBLIC_SW_ENABLED = "false";
    await expect(registrarSW()).resolves.toBe("unregistered");
    expect(register).not.toHaveBeenCalled();
    expect(unregisterSpy).toHaveBeenCalled();
    expect(cacheDelete).toHaveBeenCalledWith("carwash-static-v1");
    expect(cacheDelete).not.toHaveBeenCalledWith("de-otra-app");
  });

  it("sin la variable definida, el default es apagado", async () => {
    delete process.env.NEXT_PUBLIC_SW_ENABLED;
    await expect(registrarSW()).resolves.toBe("unregistered");
    expect(register).not.toHaveBeenCalled();
  });

  it("encendido, registra /sw.js con scope raíz", async () => {
    process.env.NEXT_PUBLIC_SW_ENABLED = "true";
    await expect(registrarSW()).resolves.toBe("registered");
    expect(register).toHaveBeenCalledWith("/sw.js", { scope: "/" });
    expect(unregisterSpy).not.toHaveBeenCalled();
  });

  it("clearSwCaches borra solo las cachés propias y tolera un navegador sin Cache API", async () => {
    await clearSwCaches();
    expect(cacheDelete).toHaveBeenCalledTimes(1);
    expect(cacheDelete).toHaveBeenCalledWith("carwash-static-v1");
    delete (globalThis as unknown as { caches?: unknown }).caches;
    await expect(clearSwCaches()).resolves.toBeUndefined();
  });

  it("sin soporte de service worker, no hace nada", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (navigator as any).serviceWorker;
    await expect(registrarSW()).resolves.toBe("unsupported");
  });
});
