"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { configKeys } from "@/features/configuracion/queries";
import { vehicleSizesApi } from "@/services/config.service";
import {
  getBodyTypeSizes,
  vehicleColorsApi,
  vehicleMakesApi,
  vehicleModelsApi,
  type VehicleListParams,
  type VehicleModelListParams,
} from "@/services/vehicles.service";

/** Todo cuelga de `["vehicles", …]`: guardar algo invalida el catálogo entero de una vez. */
export const vehicleKeys = {
  all: ["vehicles"] as const,
  makes: (p: VehicleListParams) => ["vehicles", "makes", p] as const,
  models: (p: VehicleModelListParams) => ["vehicles", "models", p] as const,
  colors: (p: VehicleListParams) => ["vehicles", "colors", p] as const,
  bodyTypeSizes: ["vehicles", "body-type-sizes"] as const,
};

/** Tope del backend (200): marcas (~40), modelos de una marca y colores caben en una página. */
export const ALL = { limit: 200, offset: 0 };

export function useVehicleMakes(params: VehicleListParams) {
  return useQuery({
    queryKey: vehicleKeys.makes(params),
    queryFn: () => vehicleMakesApi.list(params),
    placeholderData: keepPreviousData,
  });
}

/** `enabled: false` mientras el selector no tiene marca elegida. */
export function useVehicleModels(params: VehicleModelListParams, enabled = true) {
  return useQuery({
    queryKey: vehicleKeys.models(params),
    queryFn: () => vehicleModelsApi.list(params),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useVehicleColors(params: VehicleListParams) {
  return useQuery({
    queryKey: vehicleKeys.colors(params),
    queryFn: () => vehicleColorsApi.list(params),
    placeholderData: keepPreviousData,
  });
}

export function useBodyTypeSizes() {
  return useQuery({ queryKey: vehicleKeys.bodyTypeSizes, queryFn: getBodyTypeSizes });
}

/**
 * Los tamaños del negocio, con la misma clave que usa el resto de la configuración
 * (`["config", "vehicle-sizes", …]`): la pestaña Tamaños los invalida y esta lista se refresca.
 * No se importa de `features/catalogo` para no arrastrar el servicio de servicios.
 */
export function useVehicleSizesForSuggestion() {
  return useQuery({
    queryKey: configKeys.list("vehicle-sizes", ALL),
    queryFn: () => vehicleSizesApi.list(ALL),
  });
}
