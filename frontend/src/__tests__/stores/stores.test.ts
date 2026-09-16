import * as Sentry from "@sentry/nextjs";

import { useAuthStore } from "@/stores/authStore";
import { itemsOwnedBy, useOfflineQueueStore } from "@/stores/offlineQueueStore";
import { TOAST_DURATION, toast, useToastStore } from "@/stores/toastStore";

const session = {
  user: { id: "u1", email: "a@b.com", role: "OWNER" as const, tenant_id: "t1" },
  tenant: { id: "t1", name: "Lavadero" },
};

describe("authStore", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useAuthStore.setState({ user: null, tenant: null });
    jest.clearAllMocks();
  });

  it("setSession guarda usuario y tenant y etiqueta Sentry sin email", () => {
    useAuthStore.getState().setSession(session);
    expect(useAuthStore.getState()).toMatchObject(session);
    expect(Sentry.setUser).toHaveBeenCalledWith({ id: "u1" });
    expect(Sentry.setTag).toHaveBeenCalledWith("tenant_id", "t1");
  });

  it("lo persistido es solo usuario y tenant — ningún token", () => {
    useAuthStore.getState().setSession(session);
    const raw = window.localStorage.getItem("carwash_auth");
    expect(raw).not.toBeNull();
    const persisted = JSON.parse(raw!) as { state: Record<string, unknown> };
    expect(Object.keys(persisted.state).sort()).toEqual(["tenant", "user"]);
    expect(raw).not.toMatch(/token/i);
  });

  it("clear limpia el estado y el contexto de Sentry", () => {
    useAuthStore.getState().setSession(session);
    useAuthStore.getState().clear();
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().tenant).toBeNull();
    expect(Sentry.setUser).toHaveBeenLastCalledWith(null);
  });

  it("rehidratar marca _hasHydrated y resincroniza Sentry", async () => {
    // Primero el setState: cada set del store reescribe lo persistido.
    useAuthStore.setState({ _hasHydrated: false });
    window.localStorage.setItem("carwash_auth", JSON.stringify({ state: session, version: 1 }));
    await useAuthStore.persist.rehydrate();
    expect(useAuthStore.getState()._hasHydrated).toBe(true);
    expect(useAuthStore.getState().user).toEqual(session.user);
    expect(Sentry.setTag).toHaveBeenCalledWith("tenant_id", "t1");
  });
});

describe("offlineQueueStore", () => {
  beforeEach(() => useOfflineQueueStore.setState({ items: [] }));

  it("enqueue, markFailed y remove", () => {
    const s = useOfflineQueueStore.getState();
    s.enqueue({
      id: "a",
      userId: "u1",
      tenantId: "t1",
      kind: "k",
      payload: 1,
      createdAt: "x",
      attempts: 0,
    });
    s.enqueue({
      id: "b",
      userId: "u1",
      tenantId: "t1",
      kind: "k",
      payload: 2,
      createdAt: "x",
      attempts: 0,
    });
    s.markFailed("a", "boom");
    expect(useOfflineQueueStore.getState().items[0]).toMatchObject({
      attempts: 1,
      lastError: "boom",
    });
    expect(useOfflineQueueStore.getState().items[1]).toMatchObject({ attempts: 0 });
    s.remove("a");
    expect(useOfflineQueueStore.getState().items.map((i) => i.id)).toEqual(["b"]);
  });

  it("noteTransient anota sin gastar intento; fallidos: mover, reintentar y descartar", () => {
    const base = {
      userId: "u1",
      tenantId: "t1",
      kind: "k",
      payload: 1,
      createdAt: "x",
      attempts: 2,
    };
    useOfflineQueueStore.setState({ items: [{ ...base, id: "a" }], failed: [] });
    const s = useOfflineQueueStore.getState();
    s.noteTransient("a", "Error 503 del servidor");
    expect(useOfflineQueueStore.getState().items[0]).toMatchObject({
      attempts: 2,
      lastError: "Error 503 del servidor",
    });
    s.moveToFailed("a", "Error 422 del servidor", 422);
    s.moveToFailed("no-existe", "x");
    expect(useOfflineQueueStore.getState().items).toEqual([]);
    expect(useOfflineQueueStore.getState().failed).toEqual([
      expect.objectContaining({ id: "a", attempts: 3, status: 422 }),
    ]);
    s.retryFailed("a");
    s.retryFailed("no-existe");
    expect(useOfflineQueueStore.getState().failed).toEqual([]);
    const [back] = useOfflineQueueStore.getState().items;
    expect(back).toMatchObject({ id: "a", attempts: 0 });
    expect(back).not.toHaveProperty("failedAt");
    s.moveToFailed("a", "x");
    s.dismissFailed("a");
    expect(useOfflineQueueStore.getState().failed).toEqual([]);
  });

  it("persiste bajo su propia clave, con versión", () => {
    useOfflineQueueStore.getState().enqueue({
      id: "a",
      userId: "u1",
      tenantId: "t1",
      kind: "k",
      payload: 1,
      createdAt: "x",
      attempts: 0,
    });
    const raw = JSON.parse(window.localStorage.getItem("carwash_offline_queue")!) as {
      version: number;
    };
    expect(raw.version).toBe(3);
  });

  it("itemsOwnedBy filtra por usuario Y tenant", () => {
    const base = { kind: "k", payload: 1, createdAt: "x", attempts: 0 };
    const items = [
      { ...base, id: "1", userId: "u1", tenantId: "t1" },
      { ...base, id: "2", userId: "u2", tenantId: "t1" },
      { ...base, id: "3", userId: "u1", tenantId: "t2" },
    ];
    expect(itemsOwnedBy(items, { userId: "u1", tenantId: "t1" }).map((i) => i.id)).toEqual(["1"]);
    expect(itemsOwnedBy(items, null)).toEqual([]);
  });

  it("migra una cola v1 sin dueño sin borrar los ítems", async () => {
    window.localStorage.setItem(
      "carwash_offline_queue",
      JSON.stringify({
        state: { items: [{ id: "viejo", kind: "k", payload: 1, createdAt: "x", attempts: 0 }] },
        version: 1,
      }),
    );
    await useOfflineQueueStore.persist.rehydrate();
    expect(useOfflineQueueStore.getState().items).toEqual([
      expect.objectContaining({ id: "viejo", userId: "", tenantId: "" }),
    ]);
    expect(useOfflineQueueStore.getState().failed).toEqual([]);
  });

  it("migra una cola v2 (con dueño, sin fallidos) conservando ítems y dueño", async () => {
    window.localStorage.setItem(
      "carwash_offline_queue",
      JSON.stringify({
        state: {
          items: [
            {
              id: "v2",
              userId: "u1",
              tenantId: "t1",
              kind: "k",
              payload: 1,
              createdAt: "x",
              attempts: 2,
            },
          ],
        },
        version: 2,
      }),
    );
    await useOfflineQueueStore.persist.rehydrate();
    expect(useOfflineQueueStore.getState().items).toEqual([
      expect.objectContaining({ id: "v2", userId: "u1", tenantId: "t1", attempts: 2 }),
    ]);
    expect(useOfflineQueueStore.getState().failed).toEqual([]);
  });
});

describe("toastStore", () => {
  beforeEach(() => useToastStore.setState({ toasts: [] }));

  it("encola con la duración por variante, o la explícita", () => {
    const id = toast.error("falló");
    toast.success("ok", 1234);
    toast.info("i");
    toast.warning("w");
    const [err, ok, info, warn] = useToastStore.getState().toasts;
    expect(err).toMatchObject({ id, variant: "error", duration: TOAST_DURATION.error });
    expect(ok).toMatchObject({ variant: "success", duration: 1234 });
    expect(info!.variant).toBe("info");
    expect(warn!.duration).toBe(TOAST_DURATION.warning);
    useToastStore.getState().remove(id);
    expect(useToastStore.getState().toasts).toHaveLength(3);
  });
});
