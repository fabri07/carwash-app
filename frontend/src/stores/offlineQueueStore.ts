import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Tipo de carga encolada. En Véktor era una unión cerrada de dominio
 * (`"sale" | "expense" | …`); acá es abierto porque en la Fase 2 no hay
 * dominio. Cada consumidor registra sus handlers en `useOfflineSubmit`.
 */
export type QueuedKind = string;

export interface QueuedItem {
  id: string; // UUID — también es la Idempotency-Key del POST
  /**
   * Dueño de la carga. La cola sobrevive al logout a propósito (perder cargas
   * sin sincronizar es peor), así que en una tablet compartida puede haber
   * ítems de otro usuario: el flush solo manda los del usuario y tenant de la
   * sesión actual, para no enviar la carga de A con las cookies de B.
   */
  userId: string;
  tenantId: string;
  kind: QueuedKind;
  payload: unknown;
  createdAt: string; // ISO
  /** Intentos rechazados con un 4xx permanente. Las fallas transitorias NO cuentan. */
  attempts: number;
  lastError?: string;
}

/** Ítem sacado de la cola por rechazos permanentes. Se conserva visible, no se borra. */
export interface FailedItem extends QueuedItem {
  failedAt: string; // ISO
  status?: number;
}

interface OfflineQueueState {
  items: QueuedItem[];
  failed: FailedItem[];
  enqueue: (item: QueuedItem) => void;
  remove: (id: string) => void;
  /** Rechazo permanente (4xx): suma un intento. */
  markFailed: (id: string, error: string) => void;
  /** Falla transitoria (red, 5xx, 408, 429, 401): anota el motivo sin gastar intento. */
  noteTransient: (id: string, error: string) => void;
  /** Agotó los intentos por 4xx permanente: pasa a `failed`, visible para el usuario. */
  moveToFailed: (id: string, error: string, status?: number) => void;
  /** El usuario decide reintentar un fallido: vuelve a la cola con los intentos en cero. */
  retryFailed: (id: string) => void;
  /** El usuario descarta un fallido a sabiendas. Es el único camino que borra una carga. */
  dismissFailed: (id: string) => void;
}

/**
 * Cola local de cargas para cuando se cae internet. Persiste en el storage
 * por defecto de zustand. `version` permite migrar el shape sin romper colas
 * viejas en silencio. No guarda credenciales: el payload es negocio.
 */
export function itemsOwnedBy<T extends QueuedItem>(
  items: T[],
  owner: { userId: string; tenantId: string } | null,
): T[] {
  if (!owner) return [];
  return items.filter((i) => i.userId === owner.userId && i.tenantId === owner.tenantId);
}

export const useOfflineQueueStore = create<OfflineQueueState>()(
  persist(
    (set) => ({
      items: [],
      enqueue: (item) => set((s) => ({ items: [...s.items, item] })),
      remove: (id) => set((s) => ({ items: s.items.filter((i) => i.id !== id) })),
      failed: [],
      markFailed: (id, error) =>
        set((s) => ({
          items: s.items.map((i) =>
            i.id === id ? { ...i, attempts: i.attempts + 1, lastError: error } : i,
          ),
        })),
      noteTransient: (id, error) =>
        set((s) => ({
          items: s.items.map((i) => (i.id === id ? { ...i, lastError: error } : i)),
        })),
      moveToFailed: (id, error, status) =>
        set((s) => {
          const item = s.items.find((i) => i.id === id);
          if (!item) return {};
          const failed: FailedItem = {
            ...item,
            attempts: item.attempts + 1,
            lastError: error,
            status,
            failedAt: new Date().toISOString(),
          };
          return { items: s.items.filter((i) => i.id !== id), failed: [...s.failed, failed] };
        }),
      retryFailed: (id) =>
        set((s) => {
          const f = s.failed.find((i) => i.id === id);
          if (!f) return {};
          // eslint-disable-next-line @typescript-eslint/no-unused-vars
          const { failedAt, status, ...item } = f;
          return {
            failed: s.failed.filter((i) => i.id !== id),
            items: [...s.items, { ...item, attempts: 0 }],
          };
        }),
      dismissFailed: (id) => set((s) => ({ failed: s.failed.filter((i) => i.id !== id) })),
    }),
    {
      name: "carwash_offline_queue",
      version: 3,
      // v1 no tenía dueño. No se borran (serían cargas perdidas), pero sin
      // dueño no coinciden con ninguna sesión y nunca se mandan a ciegas.
      // v3 agrega la lista de fallidos.
      migrate: (persisted, version) => {
        const state = persisted as { items?: Partial<QueuedItem>[]; failed?: FailedItem[] };
        if (version < 2) {
          state.items = (state.items ?? []).map((i) => ({ userId: "", tenantId: "", ...i }));
        }
        if (version < 3) {
          state.failed = state.failed ?? [];
        }
        return state as OfflineQueueState;
      },
    },
  ),
);
