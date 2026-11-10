"use client";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { BodyTypeSizesForm } from "@/features/vehiculos/BodyTypeSizesForm";
import { ColorsTab, MakesTab, ModelsTab } from "@/features/vehiculos/VehicleCatalogTabs";

/**
 * Catálogo de vehículos del negocio (FASE-4-CONTRATO §4, PR 4.4): la pestaña "Vehículos" de
 * Configuración, para quien tiene `CATALOGO_EDITAR`. El negocio arranca con la base del mercado
 * argentino; acá suma lo que falte, renombra u oculta, y elige qué tamaño sugiere cada carrocería.
 */
export function VehicleCatalogPanel() {
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Marcas, modelos y colores que se ofrecen al cargar un vehículo. Arrancan con los del mercado
        argentino: sumá lo que falte y ocultá lo que no atendés.
      </p>
      <Tabs defaultValue="marcas">
        <TabsList className="h-auto flex-wrap justify-start">
          <TabsTrigger value="marcas">Marcas</TabsTrigger>
          <TabsTrigger value="modelos">Modelos</TabsTrigger>
          <TabsTrigger value="colores">Colores</TabsTrigger>
          <TabsTrigger value="carrocerias">Carrocería y tamaño</TabsTrigger>
        </TabsList>
        <TabsContent value="marcas">
          <MakesTab />
        </TabsContent>
        <TabsContent value="modelos">
          <ModelsTab />
        </TabsContent>
        <TabsContent value="colores">
          <ColorsTab />
        </TabsContent>
        <TabsContent value="carrocerias">
          <BodyTypeSizesForm />
        </TabsContent>
      </Tabs>
    </div>
  );
}
