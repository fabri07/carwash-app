"use client";

import { Lock } from "lucide-react";

import { EmptyState } from "@/components/ui/empty-state";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CategoriesTab } from "@/features/catalogo/CategoriesTab";
import { ServicesTab } from "@/features/catalogo/ServicesTab";
import { TemplatesTab } from "@/features/catalogo/TemplatesTab";
import { useCan, useIsOwner, usePermissionsLoaded } from "@/hooks/usePermission";

/**
 * `/catalogo` (FASE-4-CONTRATO §7, PR 4.3): servicios, categorías y plantillas.
 *
 * La ve quien tiene `CATALOGO_EDITAR` o `PRECIOS_EDITAR` (las categorías, solo el primero); las plantillas, solo el dueño. Los botones de precios, alta y
 * baja de servicio dependen de `PRECIOS_EDITAR` (los resuelve `ServicesTab`). Ocultar no es
 * autorizar: el backend responde 403 igual si alguien llega por URL.
 */
export function CatalogPageClient() {
  const isOwner = useIsOwner();
  const can = useCan();
  const canCatalog = can("CATALOGO_EDITAR");
  // `PRECIOS_EDITAR` es un permiso independiente: quien solo lo tiene ve la lista y carga precios.
  const canAccess = canCatalog || can("PRECIOS_EDITAR");
  const loaded = usePermissionsLoaded();

  // Tras recargar los permisos no están hasta que llega `/auth/me`: sin esto, quien edita el
  // catálogo vería un "sin acceso" falso.
  if (!isOwner && !loaded) {
    return <p className="text-sm text-muted-foreground">Cargando…</p>;
  }

  if (!canAccess) {
    return (
      <section aria-labelledby="catalog-title" className="rounded-xl border bg-card">
        <h1 id="catalog-title" className="sr-only">
          Catálogo
        </h1>
        <EmptyState
          icon={<Lock className="h-6 w-6" />}
          title="No tenés acceso a esta sección"
          description="El catálogo lo editan el dueño y quienes tengan ese permiso. Si necesitás un cambio, pedíselo."
          action={{ label: "Volver al inicio", href: "/dashboard" }}
        />
      </section>
    );
  }

  return (
    <section aria-labelledby="catalog-title" className="space-y-4">
      <div>
        <h1 id="catalog-title" className="text-2xl font-semibold">
          Catálogo
        </h1>
        <p className="text-sm text-muted-foreground">
          Los servicios que ofrecés, con su precio y duración por tamaño de vehículo.
        </p>
      </div>
      <Tabs defaultValue="servicios">
        <TabsList className="h-auto flex-wrap justify-start">
          <TabsTrigger value="servicios">Servicios</TabsTrigger>
          {canCatalog && <TabsTrigger value="categorias">Categorías</TabsTrigger>}
          {isOwner && <TabsTrigger value="plantillas">Plantillas</TabsTrigger>}
        </TabsList>
        <TabsContent value="servicios">
          <ServicesTab />
        </TabsContent>
        {canCatalog && (
          <TabsContent value="categorias">
            <CategoriesTab />
          </TabsContent>
        )}
        {isOwner && (
          <TabsContent value="plantillas">
            <TemplatesTab />
          </TabsContent>
        )}
      </Tabs>
    </section>
  );
}
