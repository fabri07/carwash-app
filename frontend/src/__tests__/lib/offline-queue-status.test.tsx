import { act, renderHook } from "@testing-library/react";

import { useOfflineQueueStatus } from "@/lib/useOfflineQueueStatus";
import { useAuthStore } from "@/stores/authStore";
import { useOfflineQueueStore, type QueuedItem } from "@/stores/offlineQueueStore";

const user = {
  id: "u1",
  email: "a@x.com",
  role: "OWNER" as const,
  tenant_id: "t1",
  username: null,
  permission_profile_id: null,
};

function item(id: string, userId = "u1"): QueuedItem {
  return {
    id,
    userId,
    tenantId: "t1",
    kind: "k",
    payload: {},
    createdAt: "2026-11-17T10:00:00.000Z",
    attempts: 0,
  };
}

beforeEach(() => {
  useAuthStore.setState({ user, tenant: { id: "t1", name: "L" } });
  useOfflineQueueStore.setState({ items: [], failed: [], conflicts: [] });
  Object.defineProperty(navigator, "onLine", { configurable: true, get: () => true });
});

describe("useOfflineQueueStatus", () => {
  it("al día: sin pendientes ni problemas", () => {
    const { result } = renderHook(() => useOfflineQueueStatus());
    expect(result.current).toMatchObject({
      online: true,
      pending: 0,
      needsAttention: false,
      state: "synced",
    });
  });

  it("cuenta pendientes solo del usuario actual y reacciona a conflictos y fallidos", () => {
    const { result } = renderHook(() => useOfflineQueueStatus());
    act(() => {
      useOfflineQueueStore.setState({
        items: [item("a"), item("b"), item("ajeno", "u2")],
        conflicts: [{ ...item("c"), failedAt: "x", status: 409 }],
        failed: [{ ...item("d"), failedAt: "x", status: 422 }],
      });
    });
    expect(result.current.pending).toBe(2);
    expect(result.current.state).toBe("syncing");
    expect(result.current.conflicts.map((c) => c.id)).toEqual(["c"]);
    expect(result.current.failed.map((c) => c.id)).toEqual(["d"]);
    expect(result.current.needsAttention).toBe(true);
  });

  it("sin señal: state offline", () => {
    const { result } = renderHook(() => useOfflineQueueStatus());
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    expect(result.current.online).toBe(false);
    expect(result.current.state).toBe("offline");
  });
});
