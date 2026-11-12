"use client";

import { Lock } from "lucide-react";

import { EmptyState } from "@/components/ui/empty-state";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  BusinessHoursTab,
  PaymentMethodsTab,
  ResourcesTab,
  VehicleSizesTab,
} from "@/features/configuracion/CatalogTabs";
import { AgendaTab } from "@/features/configuracion/AgendaTab";
import { BrandingTab } from "@/features/configuracion/BrandingTab";
import { BusinessTab, DepositTab } from "@/features/configuracion/SettingsTabs";
import { VehicleCatalogPanel } from "@/features/vehiculos/VehicleCatalogPanel";
import { useCan, useIsOwner, usePermissionsLoaded } from "@/hooks/usePermission";

/**
 * `/configuracion` (FASE-4-CONTRATO §7, PR 4.2b): una página con pestañas.
 *
 * Negocio, Personalización (PR 4.5) y Seña y reservas son del dueño (`PATCH /settings`). Horarios, puestos,
 * medios de pago, tamaños y vehículos (PR 4.4) los edita quien tiene `CATALOGO_EDITAR`. Ocultar no es
 * autorizar: el backend responde 403 igual si alguien llega por URL.
 */
export function ConfigPageClient() {
  const isOwner = useIsOwner();
  const can = useCan();
  const canCatalog = can("CATALOGO_EDITAR");
  // F5 (PR 5.1): bloqueos y aperturas se ven con AGENDA_VER y se editan con TURNOS_GESTIONAR.
  const canAgenda = can("AGENDA_VER") || can("TURNOS_GESTIONAR");
  const loaded = usePermissionsLoaded();

  // Tras recargar los permisos no están hasta que llega `/auth/me`: sin esto, quien edita el
  // catálogo vería un "sin acceso" falso.
  if (!isOwner && !loaded) {
    return <p className="text-sm text-muted-foreground">Cargando…</p>;
  }

  if (!isOwner && !canCatalog && !canAgenda) {
    return (
      <section aria-labelledby="config-title" className="rounded-xl border bg-card">
        <h1 id="config-title" className="sr-only">
          Configuración
        </h1>
        <EmptyState
          icon={<Lock className="h-6 w-6" />}
          title="No tenés acceso a esta sección"
          description="La configuración del negocio la manejan el dueño y quienes tengan permiso para editar el catálogo o la agenda. Si necesitás un cambio, pedíselo."
          action={{ label: "Volver al inicio", href: "/dashboard" }}
        />
      </section>
    );
  }

  const first = isOwner ? "negocio" : canCatalog ? "horarios" : "agenda";
  return (
    <section aria-labelledby="config-title" className="space-y-4">
      <div>
        <h1 id="config-title" className="text-2xl font-semibold">
          Configuración
        </h1>
        <p className="text-sm text-muted-foreground">
          Cómo funciona tu negocio: datos, marca, seña, horarios, puestos y medios de pago.
        </p>
      </div>
      <Tabs defaultValue={first}>
        <TabsList className="h-auto flex-wrap justify-start">
          {isOwner && <TabsTrigger value="negocio">Negocio</TabsTrigger>}
          {isOwner && <TabsTrigger value="marca">Personalización</TabsTrigger>}
          {isOwner && <TabsTrigger value="sena">Seña y reservas</TabsTrigger>}
          {canCatalog && <TabsTrigger value="horarios">Horarios</TabsTrigger>}
          {canAgenda && <TabsTrigger value="agenda">Bloqueos y aperturas</TabsTrigger>}
          {canCatalog && <TabsTrigger value="puestos">Puestos</TabsTrigger>}
          {canCatalog && <TabsTrigger value="medios">Medios de pago</TabsTrigger>}
          {canCatalog && <TabsTrigger value="tamanos">Tamaños</TabsTrigger>}
          {canCatalog && <TabsTrigger value="vehiculos">Vehículos</TabsTrigger>}
        </TabsList>
        {isOwner && (
          <TabsContent value="negocio">
            <BusinessTab />
          </TabsContent>
        )}
        {isOwner && (
          <TabsContent value="marca">
            <BrandingTab />
          </TabsContent>
        )}
        {isOwner && (
          <TabsContent value="sena">
            <DepositTab />
          </TabsContent>
        )}
        {canCatalog && (
          <TabsContent value="horarios">
            <BusinessHoursTab />
          </TabsContent>
        )}
        {canAgenda && (
          <TabsContent value="agenda">
            <AgendaTab />
          </TabsContent>
        )}
        {canCatalog && (
          <TabsContent value="puestos">
            <ResourcesTab />
          </TabsContent>
        )}
        {canCatalog && (
          <TabsContent value="medios">
            <PaymentMethodsTab />
          </TabsContent>
        )}
        {canCatalog && (
          <TabsContent value="tamanos">
            <VehicleSizesTab />
          </TabsContent>
        )}
        {canCatalog && (
          <TabsContent value="vehiculos">
            <VehicleCatalogPanel />
          </TabsContent>
        )}
      </Tabs>
    </section>
  );
}
