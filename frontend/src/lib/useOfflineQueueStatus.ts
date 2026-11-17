"use client";

import {
  useConflictOfflineItems,
  useFailedOfflineItems,
  useOfflineQueueCount,
  useOnlineStatus,
} from "@/hooks/useOfflineSubmit";
import type { ConflictItem, FailedItem } from "@/stores/offlineQueueStore";

export interface OfflineQueueStatus {
  online: boolean;
  /** Toques guardados que todavía no llegaron al servidor. */
  pending: number;
  /** Rechazos definitivos (422/403/404/CONFLICT): visibles, no se reintentan. */
  failed: FailedItem[];
  /** Acciones descartadas por `INVALID_TRANSITION`: otro dispositivo se adelantó. */
  conflicts: ConflictItem[];
  /** Hay algo que el usuario tiene que mirar. */
  needsAttention: boolean;
  /** `sin señal`, `sincronizando` (online con pendientes) o `al día`. */
  state: "offline" | "syncing" | "synced";
}

/**
 * Estado reutilizable de la cola offline para un indicador de pantalla (6.2 lo
 * adopta). Solo del usuario y tenant de la sesión actual.
 */
export function useOfflineQueueStatus(): OfflineQueueStatus {
  const online = useOnlineStatus();
  const pending = useOfflineQueueCount();
  const failed = useFailedOfflineItems();
  const conflicts = useConflictOfflineItems();
  return {
    online,
    pending,
    failed,
    conflicts,
    needsAttention: failed.length + conflicts.length > 0,
    state: !online ? "offline" : pending > 0 ? "syncing" : "synced",
  };
}
