"use client";

import { useMemo } from "react";

import { useAuthStore } from "@/stores/authStore";
import { useOfflineQueueStore } from "@/stores/offlineQueueStore";
import type { PendingItem } from "./optimistic";

/**
 * Acciones de ESTE job que siguen en la cola offline, del usuario y negocio de la sesión, en el orden
 * en que se tocaron. De acá sale el estado pendiente que se muestra: no hay copia local que un
 * refetch pueda borrar.
 */
export function usePendingForJob(jobId: string): PendingItem[] {
  const user = useAuthStore((s) => s.user);
  const items = useOfflineQueueStore((s) => s.items);
  return useMemo(
    () =>
      items.filter(
        (i) =>
          user !== null &&
          i.userId === user.id &&
          i.tenantId === user.tenant_id &&
          i.kind.startsWith("operacion.") &&
          (i.payload as { jobId?: string } | undefined)?.jobId === jobId,
      ),
    [items, user, jobId],
  );
}
