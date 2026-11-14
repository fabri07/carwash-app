"use client";

import { useInboxCounts } from "@/features/turnos/queries";
import { cn } from "@/lib/utils";

/**
 * Número del ítem "Turnos" del menú: pendientes de seña + lista de espera + señas a resolver
 * (§4.1). Se refresca cada minuto y al volver a la pestaña. Sin nada pendiente no se muestra.
 * Solo se monta para quien ve la agenda (el menú ya filtró el ítem por `AGENDA_VER`).
 */
export function InboxBadge({ collapsed }: { collapsed: boolean }) {
  const counts = useInboxCounts();
  const total = counts.data?.total ?? 0;
  if (total <= 0) return null;
  const label = total > 99 ? "99+" : String(total);
  return (
    <span
      data-testid="inbox-badge"
      aria-label={`${total} pendientes`}
      className={cn(
        "rounded-full bg-primary px-2 text-xs font-semibold leading-5 text-primary-foreground",
        collapsed ? "absolute right-1 top-1 px-1.5" : "ml-auto",
      )}
    >
      {label}
    </span>
  );
}
