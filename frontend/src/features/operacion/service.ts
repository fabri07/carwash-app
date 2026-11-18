import { api } from "@/lib/api";
import type {
  CancelDelayBody,
  DayResponse,
  InspectionBody,
  Job,
  JobEventsPage,
  ManualBookingBody,
  ManualBookingResponse,
  OccurredBody,
  PaymentBody,
  ReceiveBody,
  RetrospectiveBody,
  VoidPaymentBody,
} from "./types";

/**
 * Agenda del día y operación del job (FASE-6-CONTRATO §3 y adenda §12). Toda acción manda
 * `Idempotency-Key` (en la cola offline es el id del ítem) y `occurred_at` en el cuerpo: la hora
 * del toque, no la del envío.
 */
const withKey = (key: string) => ({ headers: { "Idempotency-Key": key } });

export async function getDay(date: string): Promise<DayResponse> {
  return (await api.get<DayResponse>("/agenda/day", { params: { date } })).data;
}

export async function getJob(id: string): Promise<Job> {
  return (await api.get<Job>(`/jobs/${id}`)).data;
}

export async function listJobEvents(
  id: string,
  params: { limit: number; offset: number },
): Promise<JobEventsPage> {
  return (await api.get<JobEventsPage>(`/jobs/${id}/events`, { params })).data;
}

export async function receiveJob(body: ReceiveBody, key: string): Promise<Job> {
  return (await api.post<Job>("/jobs", body, withKey(key))).data;
}

export async function startJob(id: string, body: OccurredBody, key: string): Promise<Job> {
  return (await api.post<Job>(`/jobs/${id}/start`, body, withKey(key))).data;
}

export async function finishJob(id: string, body: OccurredBody, key: string): Promise<Job> {
  return (await api.post<Job>(`/jobs/${id}/finish`, body, withKey(key))).data;
}

export async function pickUpJob(id: string, body: OccurredBody, key: string): Promise<Job> {
  return (await api.post<Job>(`/jobs/${id}/pick-up`, body, withKey(key))).data;
}

export async function cancelDelay(id: string, body: CancelDelayBody, key: string): Promise<Job> {
  return (await api.post<Job>(`/jobs/${id}/cancel-delay`, body, withKey(key))).data;
}

export async function recordPayment(id: string, body: PaymentBody, key: string): Promise<Job> {
  return (await api.post<Job>(`/jobs/${id}/payments`, body, withKey(key))).data;
}

export async function voidPayment(
  id: string,
  paymentId: string,
  body: VoidPaymentBody,
  key: string,
): Promise<Job> {
  return (await api.post<Job>(`/jobs/${id}/payments/${paymentId}/void`, body, withKey(key))).data;
}

export async function recordInspection(
  id: string,
  body: InspectionBody,
  key: string,
): Promise<Job> {
  return (await api.post<Job>(`/jobs/${id}/inspection`, body, withKey(key))).data;
}

export async function recordRetrospective(body: RetrospectiveBody, key: string): Promise<Job> {
  return (await api.post<Job>("/jobs/retrospective", body, withKey(key))).data;
}

export async function createManualBooking(
  body: ManualBookingBody,
  key: string,
): Promise<ManualBookingResponse> {
  return (await api.post<ManualBookingResponse>("/bookings/manual", body, withKey(key))).data;
}
