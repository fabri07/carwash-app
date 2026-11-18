"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { useAllVehicleSizes, useSettings } from "@/features/configuracion/queries";
import { servicesApi } from "@/services/catalog.service";
import { paymentMethodsApi, resourcesApi } from "@/services/config.service";
import type { PaymentMethod, Service } from "@/types/api";
import { BUSINESS_TIME_ZONE } from "@/lib/period";
import { getDay, getJob, listJobEvents } from "./service";

/** Todo cuelga de `["operacion", …]`: una acción invalida el día y el job de una vez. */
export const operacionKeys = {
  all: ["operacion"] as const,
  day: (date: string) => ["operacion", "day", date] as const,
  days: ["operacion", "day"] as const,
  job: (id: string) => ["operacion", "job", id] as const,
  jobs: ["operacion", "job"] as const,
  events: (id: string) => ["operacion", "events", id] as const,
};

/** Tope del backend: una página de 200 alcanza para puestos, medios de pago y servicios. */
const ALL = { limit: 200, offset: 0 };
/** Eventos por página de la línea de tiempo (ADR-0006). */
export const EVENTS_PAGE = 20;

/** La agenda de un día del negocio (una pantalla, sin paginar: el backend topa y avisa `truncated`). */
export function useDay(date: string) {
  return useQuery({
    queryKey: operacionKeys.day(date),
    queryFn: () => getDay(date),
    enabled: Boolean(date),
    refetchOnWindowFocus: "always",
  });
}

export function useJob(id: string) {
  return useQuery({
    queryKey: operacionKeys.job(id),
    queryFn: () => getJob(id),
    refetchOnWindowFocus: "always",
  });
}

/** Línea de tiempo paginada con "Ver más" (`has_more` del backend). */
export function useJobEvents(id: string) {
  return useInfiniteQuery({
    queryKey: operacionKeys.events(id),
    queryFn: ({ pageParam }) => listJobEvents(id, { limit: EVENTS_PAGE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.has_more ? last.offset + last.limit : undefined),
  });
}

/** Zona horaria del negocio: "hoy" y "ayer" son del negocio, no del navegador (contrato §5). */
export function useBusinessTimeZone(): string {
  const settings = useSettings();
  return settings.data?.timezone ?? BUSINESS_TIME_ZONE;
}

export function usePaymentMethods() {
  return useQuery({
    queryKey: ["operacion", "payment-methods"],
    queryFn: () => paymentMethodsApi.list(ALL),
  });
}

export function useResourceList() {
  return useQuery({
    queryKey: ["operacion", "resources"],
    queryFn: () => resourcesApi.list(ALL),
  });
}

/** Servicios con precio fijo: un servicio "a cotizar" no se carga a mano (contrato §12.5 y §12.7). */
export function useChargeableServices() {
  return useQuery({
    queryKey: ["operacion", "services"],
    queryFn: async (): Promise<Service[]> => {
      const page = await servicesApi.list(ALL);
      return page.items.filter((s) => s.pricing_mode === "PRECIO_FIJO");
    },
  });
}

export { useAllVehicleSizes };

/**
 * Medio de pago por defecto para cobrar en 2 toques (§12.8): el efectivo si acepta cobros; si no, el
 * primero que acepte cobros.
 */
export function defaultPaymentMethod(methods: PaymentMethod[] | undefined): PaymentMethod | null {
  const incomes = (methods ?? []).filter((m) => m.for_income);
  return incomes.find((m) => m.code === "EFECTIVO") ?? incomes[0] ?? null;
}
