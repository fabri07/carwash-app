import type { Metadata } from "next";

import { EmptyState } from "@/components/ui/empty-state";

export const metadata: Metadata = {
  title: "Inicio | carwash.app",
};

/**
 * Dashboard vacío del checkpoint de la Fase 2: un `EmptyState` y nada más.
 * Cero dominio de lavadero, cero datos de mentira.
 */
export default function DashboardPage() {
  return (
    <section aria-labelledby="dashboard-title" className="rounded-xl border bg-card">
      <h1 id="dashboard-title" className="sr-only">
        Inicio
      </h1>
      <EmptyState
        title="Todavía no hay nada acá"
        description="Tu cuenta está lista. Las herramientas del negocio van a aparecer en esta pantalla."
      />
    </section>
  );
}
