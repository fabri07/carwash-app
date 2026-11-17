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
  /**
   * Hora del TOQUE (ISO), fijada una sola vez al primer intento y conservada
   * idéntica en reintentos y tras recargar la página. Es el `occurred_at` de
   * la acción: nunca la hora de la sincronización. Opcional por compatibilidad
   * con ítems viejos (se usa `createdAt` como respaldo).
   */
  occurredAt?: string;
  /**
   * Identifica "la misma acción sobre el mismo objetivo" (p. ej. `iniciar:<jobId>`).
   * Si ya hay un ítem pendiente del mismo dueño con la misma clave, un segundo
   * toque NO encola otro: es el mismo toque repetido. Toques distintos (otra
   * acción u otro job) tienen otra clave y se conservan todos.
   */
  dedupeKey?: string;
}

/** Ítem sacado de la cola por rechazos permanentes. Se conserva visible, no se borra. */
export interface FailedItem extends QueuedItem {
  failedAt: string; // ISO
  status?: number;
}

/**
 * Ítem que el servidor rechazó con 409 `INVALID_TRANSITION`: la acción ya no es
 * válida desde el estado actual del job (otro dispositivo se adelantó). Se saca
 * de la cola y NO se reintenta, pero queda visible y recuperable: la pantalla
 * decide qué mostrarle al usuario (el toque no se pierde en silencio).
 */
export type ConflictItem = FailedItem;

interface OfflineQueueState {
  items: QueuedItem[];
  failed: FailedItem[];
  /** Acciones descartadas por `INVALID_TRANSITION`, a la espera de que el usuario las vea. */
  conflicts: ConflictItem[];
  /** Pasa a `conflicts` (sin reintento). */
  moveToConflict: (id: string, error: string, status?: number) => void;
  /** El usuario vio el conflicto: lo saca de la lista. Único camino que lo borra. */
  dismissConflict: (id: string) => void;
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

const STORAGE_NAME = "carwash_offline_queue";

/** Ítems guardados en el storage (los de todas las pestañas); `[]` si no se puede leer. */
function storedItems(): QueuedItem[] {
  try {
    const storage = useOfflineQueueStore.persist.getOptions().storage;
    const value = storage?.getItem(STORAGE_NAME);
    const stored = value instanceof Promise ? undefined : value;
    const items = (stored?.state as { items?: QueuedItem[] } | undefined)?.items;
    return Array.isArray(items) ? items : [];
  } catch {
    return [];
  }
}

export const useOfflineQueueStore = create<OfflineQueueState>()(
  persist(
    (set) => ({
      items: [],
      enqueue: (item) =>
        set((s) => {
          // Otra pestaña pudo encolar desde que cargó esta: se relee el storage
          // y se une por id, para que la última en escribir no pise a la otra.
          const known = new Set(s.items.map((i) => i.id));
          const base = [...s.items, ...storedItems().filter((i) => !known.has(i.id))];
          // Mismo toque repetido (modo avión, doble tap): una sola entrada.
          if (
            item.dedupeKey &&
            base.some(
              (i) =>
                i.dedupeKey === item.dedupeKey &&
                i.userId === item.userId &&
                i.tenantId === item.tenantId,
            )
          ) {
            return {};
          }
          return { items: [...base, item] };
        }),
      remove: (id) => set((s) => ({ items: s.items.filter((i) => i.id !== id) })),
      failed: [],
      conflicts: [],
      moveToConflict: (id, error, status) =>
        set((s) => {
          const item = s.items.find((i) => i.id === id);
          if (!item) return {};
          const conflict: ConflictItem = {
            ...item,
            lastError: error,
            status,
            failedAt: new Date().toISOString(),
          };
          return {
            items: s.items.filter((i) => i.id !== id),
            conflicts: [...s.conflicts, conflict],
          };
        }),
      dismissConflict: (id) => set((s) => ({ conflicts: s.conflicts.filter((i) => i.id !== id) })),
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
          // Un toque más nuevo de la misma acción ya está en la cola: no se duplica.
          if (
            item.dedupeKey &&
            s.items.some(
              (i) =>
                i.dedupeKey === item.dedupeKey &&
                i.userId === item.userId &&
                i.tenantId === item.tenantId,
            )
          ) {
            return {};
          }
          return {
            failed: s.failed.filter((i) => i.id !== id),
            items: [...s.items, { ...item, attempts: 0 }],
          };
        }),
      dismissFailed: (id) => set((s) => ({ failed: s.failed.filter((i) => i.id !== id) })),
    }),
    {
      name: STORAGE_NAME,
      version: 4,
      // v1 no tenía dueño. No se borran (serían cargas perdidas), pero sin
      // dueño no coinciden con ninguna sesión y nunca se mandan a ciegas.
      // v3 agrega la lista de fallidos. v4, la de conflictos.
      migrate: (persisted, version) => {
        const state = persisted as {
          items?: Partial<QueuedItem>[];
          failed?: FailedItem[];
          conflicts?: ConflictItem[];
        };
        if (version < 2) {
          state.items = (state.items ?? []).map((i) => ({ userId: "", tenantId: "", ...i }));
        }
        if (version < 3) {
          state.failed = state.failed ?? [];
        }
        if (version < 4) {
          state.conflicts = state.conflicts ?? [];
        }
        return state as OfflineQueueState;
      },
    },
  ),
);

// Otra pestaña escribió la cola: se toma su versión (ya incluye lo de las dos).
if (typeof window !== "undefined") {
  window.addEventListener("storage", (e) => {
    if (e.key === STORAGE_NAME) void useOfflineQueueStore.persist.rehydrate();
  });
}
