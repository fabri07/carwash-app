import { useQuery } from "@tanstack/react-query";

import {
  getAvailability,
  getBusiness,
  getCatalog,
  type AvailabilityParams,
} from "@/services/public.service";

export const TURNERO_KEYS = {
  business: (slug: string) => ["turnero", slug, "business"] as const,
  catalog: (slug: string) => ["turnero", slug, "catalog"] as const,
  availability: (slug: string, params: AvailabilityParams | null) =>
    ["turnero", slug, "availability", params] as const,
};

/** Un 4xx del turnero es definitivo (negocio inexistente, servicio no reservable): no se reintenta. */
export function retryUnlessClientError(count: number, error: unknown): boolean {
  const status = (error as { response?: { status?: number } } | null)?.response?.status;
  if (status !== undefined && status >= 400 && status < 500) return false;
  return count < 2;
}

export function useTurneroBusiness(slug: string) {
  return useQuery({
    queryKey: TURNERO_KEYS.business(slug),
    queryFn: () => getBusiness(slug),
    staleTime: 5 * 60_000,
    retry: retryUnlessClientError,
  });
}

export function useTurneroCatalog(slug: string) {
  return useQuery({
    queryKey: TURNERO_KEYS.catalog(slug),
    queryFn: () => getCatalog(slug),
    staleTime: 5 * 60_000,
    retry: retryUnlessClientError,
  });
}

/** Los horarios cambian con cada reserva: siempre frescos. `null` = todavía no hay día. */
export function useTurneroAvailability(slug: string, params: AvailabilityParams | null) {
  return useQuery({
    queryKey: TURNERO_KEYS.availability(slug, params),
    queryFn: () => getAvailability(slug, params as AvailabilityParams),
    enabled: params !== null,
    staleTime: 0,
    retry: retryUnlessClientError,
  });
}
