"use client";

import { Lock } from "lucide-react";

import { EmptyState } from "@/components/ui/empty-state";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ProfilesTab } from "@/features/equipo/ProfilesTab";
import { StaffTab } from "@/features/equipo/StaffTab";
import { useIsOwner } from "@/hooks/usePermission";

/**
 * `/configuracion/equipo` (FASE-4-CONTRATO D4-2, PR 4.1): empleados y perfiles
 * de permisos. Solo el dueño (Y5).
 *
 * Un empleado que llega por URL ve un aviso y no se monta ninguna query: el
 * backend le respondería 403 igual, pero así no ve una tabla rota.
 */
export function TeamPageClient() {
  const isOwner = useIsOwner();

  if (!isOwner) {
    return (
      <section aria-labelledby="team-title" className="rounded-xl border bg-card">
        <h1 id="team-title" className="sr-only">
          Equipo
        </h1>
        <EmptyState
          icon={<Lock className="h-6 w-6" />}
          title="No tenés acceso a esta sección"
          description="El equipo y sus permisos los configura el dueño del negocio. Si necesitás un cambio, pedíselo."
          action={{ label: "Volver al inicio", href: "/dashboard" }}
        />
      </section>
    );
  }

  return (
    <section aria-labelledby="team-title" className="space-y-4">
      <div>
        <h1 id="team-title" className="text-2xl font-semibold">
          Equipo
        </h1>
        <p className="text-sm text-muted-foreground">
          Quién entra a la app y qué puede hacer cada uno.
        </p>
      </div>
      <Tabs defaultValue="empleados">
        <TabsList>
          <TabsTrigger value="empleados">Empleados</TabsTrigger>
          <TabsTrigger value="perfiles">Perfiles</TabsTrigger>
        </TabsList>
        <TabsContent value="empleados">
          <StaffTab />
        </TabsContent>
        <TabsContent value="perfiles">
          <ProfilesTab />
        </TabsContent>
      </Tabs>
    </section>
  );
}
