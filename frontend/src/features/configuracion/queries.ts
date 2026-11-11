"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { getSettings, vehicleSizesApi, type CrudApi } from "@/services/config.service";
import type { PageParams } from "@/services/pagination";

/** Las claves cuelgan de `["config", …]` para invalidar la configuración de una vez. */
export const configKeys = {
  all: ["config"] as const,
  settings: ["config", "settings"] as const,
  list: (resource: string, p: PageParams) => ["config", resource, p] as const,
  resource: (resource: string) => ["config", resource] as const,
};

export function useSettings() {
  return useQuery({ queryKey: configKeys.settings, queryFn: getSettings });
}

/** Página de un recurso del catálogo; `keepPreviousData` evita el parpadeo al paginar. */
export function useCatalogPage<R, C, U>(
  resource: string,
  api: CrudApi<R, C, U>,
  params: PageParams,
) {
  return useQuery({
    queryKey: configKeys.list(resource, params),
    queryFn: () => api.list(params),
    placeholderData: keepPreviousData,
  });
}

/** Tope del backend (200): los tamaños son unos pocos, caben en una sola página. */
const ALL_SIZES = { limit: 200, offset: 0 };

/**
 * Todos los tamaños vivos del negocio. Lo usan servicios (precios por tamaño) y vehículos
 * (carrocería → tamaño); la pestaña Tamaños invalida `["config", "vehicle-sizes"]`.
 */
export function useAllVehicleSizes() {
  return useQuery({
    queryKey: configKeys.list("vehicle-sizes", ALL_SIZES),
    queryFn: () => vehicleSizesApi.list(ALL_SIZES),
  });
}
