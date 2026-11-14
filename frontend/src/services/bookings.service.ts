import { api } from "@/lib/api";
import type { PageParams } from "@/services/pagination";
import type {
  BookingCancellation,
  BookingStatusCode,
  DepositCase,
  DepositConfirmed,
  DepositPayload,
  IncidentMark,
  InboxBooking,
  InboxBookingDetail,
  InboxCounts,
  PaginatedResponse,
  ReasonPayload,
  ReschedulePayload,
  RescheduleResult,
  ResolvePayload,
} from "@/types/api";

/**
 * Bandeja del dueño (FASE-5-CONTRATO §4.1, PR 5.3). Leer: `AGENDA_VER`. Aprobar,
 * rechazar, cancelar, no asistió, incidencias y reprogramar: `TURNOS_GESTIONAR`.
 * Confirmar y resolver señas: `COBROS_REGISTRAR`.
 *
 * Las acciones mandan `Idempotency-Key`: un doble toque no se aplica dos veces. En la
 * seña es obligatoria y es la clave del pago (un reenvío devuelve el mismo turno).
 */
function withKey(idempotencyKey?: string) {
  return idempotencyKey ? { headers: { "Idempotency-Key": idempotencyKey } } : undefined;
}

export interface BookingFilters extends PageParams {
  status?: BookingStatusCode[];
  /** Día local del negocio `YYYY-MM-DD` (inclusive). */
  from?: string;
  to?: string;
}

export async function listBookings(
  filters: BookingFilters,
): Promise<PaginatedResponse<InboxBooking>> {
  const res = await api.get<PaginatedResponse<InboxBooking>>("/bookings", {
    params: filters,
    // `status=a&status=b`, como lo lee FastAPI (sin corchetes).
    paramsSerializer: { indexes: null },
  });
  return res.data;
}

export async function getBookingCounts(): Promise<InboxCounts> {
  const res = await api.get<InboxCounts>("/bookings/counts");
  return res.data;
}

export async function getBooking(id: string): Promise<InboxBookingDetail> {
  const res = await api.get<InboxBookingDetail>(`/bookings/${id}`);
  return res.data;
}

export async function confirmDeposit(
  id: string,
  data: DepositPayload,
  idempotencyKey: string,
): Promise<DepositConfirmed> {
  const res = await api.post<DepositConfirmed>(
    `/bookings/${id}/confirm-deposit`,
    data,
    withKey(idempotencyKey),
  );
  return res.data;
}

export async function approveBooking(id: string, idempotencyKey?: string): Promise<InboxBooking> {
  const res = await api.post<InboxBooking>(
    `/bookings/${id}/approve`,
    undefined,
    withKey(idempotencyKey),
  );
  return res.data;
}

export async function rejectBooking(
  id: string,
  data: ReasonPayload,
  idempotencyKey?: string,
): Promise<BookingCancellation> {
  const res = await api.post<BookingCancellation>(
    `/bookings/${id}/reject`,
    data,
    withKey(idempotencyKey),
  );
  return res.data;
}

export async function cancelBooking(
  id: string,
  data: ReasonPayload,
  idempotencyKey?: string,
): Promise<BookingCancellation> {
  const res = await api.post<BookingCancellation>(
    `/bookings/${id}/cancel`,
    data,
    withKey(idempotencyKey),
  );
  return res.data;
}

export async function markNoShow(id: string, idempotencyKey?: string): Promise<InboxBooking> {
  const res = await api.post<InboxBooking>(
    `/bookings/${id}/no-show`,
    undefined,
    withKey(idempotencyKey),
  );
  return res.data;
}

export async function rescheduleBooking(
  id: string,
  data: ReschedulePayload,
  idempotencyKey?: string,
): Promise<RescheduleResult> {
  const res = await api.post<RescheduleResult>(
    `/bookings/${id}/reschedule`,
    data,
    withKey(idempotencyKey),
  );
  return res.data;
}

export async function markIncident(
  id: string,
  data: ReasonPayload,
  idempotencyKey?: string,
): Promise<IncidentMark> {
  const res = await api.post<IncidentMark>(
    `/bookings/${id}/incident-marks`,
    data,
    withKey(idempotencyKey),
  );
  return res.data;
}

export async function removeIncidentMark(id: string): Promise<void> {
  await api.delete(`/incident-marks/${id}`);
}

export async function listDepositCases(
  params: PageParams,
): Promise<PaginatedResponse<DepositCase>> {
  const res = await api.get<PaginatedResponse<DepositCase>>("/cancellations", { params });
  return res.data;
}

export async function resolveDepositCase(
  id: string,
  data: ResolvePayload,
  idempotencyKey?: string,
): Promise<BookingCancellation> {
  const res = await api.post<BookingCancellation>(
    `/cancellations/${id}/resolve`,
    data,
    withKey(idempotencyKey),
  );
  return res.data;
}
