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
import { BusinessTab, DepositTab } from "@/features/configuracion/SettingsTabs";
import { useCan, useIsOwner, usePermissionsLoaded } from "@/hooks/usePermission";

/**
 * `/configuracion` (FASE-4-CONTRATO §7, PR 4.2b): una página con pestañas.
 *
 * Negocio y Seña y reservas son del dueño (`PATCH /settings`). Horarios, puestos,
 * medios de pago y tamaños los edita quien tiene `CATALOGO_EDITAR`. Ocultar no es
 * autorizar: el backend responde 403 igual si alguien llega por URL.
 */
export function ConfigPageClient() {
  const isOwner = useIsOwner();
  const can = useCan();
  const canCatalog = can("CATALOGO_EDITAR");
  const loaded = usePermissionsLoaded();

  // Tras recargar los permisos no están hasta que llega `/auth/me`: sin esto, quien edita el
  // catálogo vería un "sin acceso" falso.
  if (!isOwner && !loaded) {
    return <p className="text-sm text-muted-foreground">Cargando…</p>;
  }

  if (!isOwner && !canCatalog) {
    return (
      <section aria-labelledby="config-title" className="rounded-xl border bg-card">
        <h1 id="config-title" className="sr-only">
          Configuración
        </h1>
        <EmptyState
          icon={<Lock className="h-6 w-6" />}
          title="No tenés acceso a esta sección"
          description="La configuración del negocio la manejan el dueño y quienes tengan permiso para editar el catálogo. Si necesitás un cambio, pedíselo."
          action={{ label: "Volver al inicio", href: "/dashboard" }}
        />
      </section>
    );
  }

  const first = isOwner ? "negocio" : "horarios";
  return (
    <section aria-labelledby="config-title" className="space-y-4">
      <div>
        <h1 id="config-title" className="text-2xl font-semibold">
          Configuración
        </h1>
        <p className="text-sm text-muted-foreground">
          Cómo funciona tu negocio: datos, seña, horarios, puestos y medios de pago.
        </p>
      </div>
      <Tabs defaultValue={first}>
        <TabsList className="h-auto flex-wrap justify-start">
          {isOwner && <TabsTrigger value="negocio">Negocio</TabsTrigger>}
          {isOwner && <TabsTrigger value="sena">Seña y reservas</TabsTrigger>}
          {canCatalog && <TabsTrigger value="horarios">Horarios</TabsTrigger>}
          {canCatalog && <TabsTrigger value="puestos">Puestos</TabsTrigger>}
          {canCatalog && <TabsTrigger value="medios">Medios de pago</TabsTrigger>}
          {canCatalog && <TabsTrigger value="tamanos">Tamaños</TabsTrigger>}
        </TabsList>
        {isOwner && (
          <TabsContent value="negocio">
            <BusinessTab />
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
      </Tabs>
    </section>
  );
}
