"use client";

import type { SmartColumn } from "@/components/data/SmartTable";
import { CatalogTab } from "@/features/configuracion/CatalogTab";
import { ServiceCategoryForm } from "@/features/catalogo/ServiceCategoryForm";
import { serviceCategoriesApi } from "@/services/catalog.service";
import type { ServiceCategory } from "@/types/api";
import type { CategoryInput } from "@/validation/services";

const columns: SmartColumn<ServiceCategory>[] = [
  { key: "name", header: "Nombre", hideable: false, className: "font-medium" },
  { key: "sort_order", header: "Orden" },
];

/** Categorías de servicios: mismo ABM que el resto del catálogo (`CatalogTab`). */
export function CategoriesTab() {
  return (
    <CatalogTab<
      ServiceCategory,
      CategoryInput,
      Parameters<typeof serviceCategoriesApi.create>[0],
      Parameters<typeof serviceCategoriesApi.update>[1]
    >
      resource="service-categories"
      title="Categorías de servicios"
      singular="categoría"
      feminine
      api={serviceCategoriesApi}
      columns={columns}
      emptyMessage="No hay categorías. Sirven para agrupar los servicios: Lavado, Interior, Protección…"
      exportFilename="categorias"
      itemName={(c) => c.name}
      toCreate={(v) => ({ name: v.name, sort_order: Number(v.sort_order) })}
      toPatch={(v) => ({ name: v.name, sort_order: Number(v.sort_order) })}
      Form={ServiceCategoryForm}
      deleteHint="No se puede quitar una categoría que todavía tiene servicios: movelos a otra primero."
    />
  );
}
