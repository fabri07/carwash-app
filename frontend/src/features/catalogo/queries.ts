"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { configKeys, useAllVehicleSizes } from "@/features/configuracion/queries";
import {
  getTemplate,
  serviceCategoriesApi,
  servicesApi,
  type ServiceListParams,
} from "@/services/catalog.service";
import type { TemplateKindCode } from "@/types/api";

export const catalogKeys = {
  services: ["catalog", "services"] as const,
  serviceList: (p: ServiceListParams) => ["catalog", "services", p] as const,
  template: (kind: TemplateKindCode) => ["catalog", "template", kind] as const,
};

/** Tope del backend (200): categorías y tamaños son decenas, caben en una sola página. */
const ALL = { limit: 200, offset: 0 };

/** Página de servicios con búsqueda y filtro de categoría; sin parpadeo al paginar. */
export function useServices(params: ServiceListParams) {
  return useQuery({
    queryKey: catalogKeys.serviceList(params),
    queryFn: () => servicesApi.list(params),
    placeholderData: keepPreviousData,
  });
}

/**
 * Categorías (y tamaños, en `configuracion/queries`) viven bajo `["config", …]`: así la pestaña "Categorías" (`CatalogTab`) y la
 * pantalla de Negocio los invalidan y estas listas se refrescan solas.
 */
export function useAllCategories() {
  return useQuery({
    queryKey: configKeys.list("service-categories", ALL),
    queryFn: () => serviceCategoriesApi.list(ALL),
  });
}

/** `kind` nulo = todavía no eligió plantilla: no consulta. */
export function useTemplate(kind: TemplateKindCode | null) {
  return useQuery({
    queryKey: kind ? catalogKeys.template(kind) : ["catalog", "template", "none"],
    queryFn: () => getTemplate(kind as TemplateKindCode),
    enabled: kind !== null,
  });
}

/** Re-exportado: un solo hook de tamaños para servicios y vehículos. */
export { useAllVehicleSizes };
