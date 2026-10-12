"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { getSettings } from "@/services/configuracion.service";
import type { PageParams } from "@/services/team.service";
import type { PaginatedResponse } from "@/types/api";

/**
 * Queries de configuración. Las claves cuelgan de `["config", …]`: guardar una
 * sección invalida la configuración; tocar un catálogo, solo ese catálogo.
 */
export type CatalogName = "vehicle-sizes" | "resources" | "business-hours" | "payment-methods";

export const configKeys = {
  all: ["config"] as const,
  settings: ["config", "settings"] as const,
  catalog: (name: CatalogName) => ["config", name] as const,
  catalogPage: (name: CatalogName, p: PageParams) => ["config", name, p] as const,
};

export function useSettings() {
  return useQuery({ queryKey: configKeys.settings, queryFn: getSettings });
}

/** Página de un catálogo. `keepPreviousData`: al pasar de página la tabla no parpadea vacía. */
export function useCatalogPage<T>(
  name: CatalogName,
  params: PageParams,
  list: (params: PageParams) => Promise<PaginatedResponse<T>>,
) {
  return useQuery({
    queryKey: configKeys.catalogPage(name, params),
    queryFn: () => list(params),
    placeholderData: keepPreviousData,
  });
}
