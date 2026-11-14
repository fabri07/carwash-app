import axios from "axios";

import type {
  TurneroAvailability,
  TurneroBooking,
  TurneroBookingPayload,
  TurneroBusiness,
  TurneroCancelPayload,
  TurneroCancellation,
  TurneroCatalog,
  TurneroLookupPayload,
} from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * Cliente del turnero público (FASE-5-CONTRATO §4.2, PR 5.2): `/v1/public/{slug}/…`.
 *
 * Aparte de `lib/api.ts` a propósito: el cliente que reserva no tiene sesión, así que un 401
 * no dispara un refresh ni lo manda a `/login`, y no viajan las cookies (`withCredentials:
 * false`): si el dueño de otro negocio abre el turnero, su sesión no participa. Sin
 * interceptores: los errores llegan tal cual a la pantalla, que los traduce por código.
 */
export const publicApi = axios.create({
  baseURL: `${API_URL}/v1/public`,
  headers: { "Content-Type": "application/json" },
  timeout: 15_000,
  withCredentials: false,
});

function base(slug: string): string {
  return `/${encodeURIComponent(slug)}`;
}

export async function getBusiness(slug: string): Promise<TurneroBusiness> {
  return (await publicApi.get<TurneroBusiness>(base(slug))).data;
}

export async function getCatalog(slug: string): Promise<TurneroCatalog> {
  return (await publicApi.get<TurneroCatalog>(`${base(slug)}/catalog`)).data;
}

export interface AvailabilityParams {
  date: string;
  vehicleSizeId: string;
  serviceIds: string[];
}

export async function getAvailability(
  slug: string,
  { date, vehicleSizeId, serviceIds }: AvailabilityParams,
): Promise<TurneroAvailability> {
  const params = new URLSearchParams({ date, vehicle_size_id: vehicleSizeId });
  for (const id of serviceIds) params.append("service_ids", id);
  return (await publicApi.get<TurneroAvailability>(`${base(slug)}/availability`, { params })).data;
}

/** `Idempotency-Key` obligatorio: un doble toque en "Reservar" devuelve el mismo turno. */
export async function createBooking(
  slug: string,
  payload: TurneroBookingPayload,
  idempotencyKey: string,
): Promise<TurneroBooking> {
  const res = await publicApi.post<TurneroBooking>(`${base(slug)}/bookings`, payload, {
    headers: { "Idempotency-Key": idempotencyKey },
  });
  return res.data;
}

export async function lookupBooking(
  slug: string,
  payload: TurneroLookupPayload,
): Promise<TurneroBooking> {
  return (await publicApi.post<TurneroBooking>(`${base(slug)}/bookings/lookup`, payload)).data;
}

export async function cancelBooking(
  slug: string,
  payload: TurneroCancelPayload,
): Promise<TurneroCancellation> {
  return (await publicApi.post<TurneroCancellation>(`${base(slug)}/bookings/cancel`, payload)).data;
}
