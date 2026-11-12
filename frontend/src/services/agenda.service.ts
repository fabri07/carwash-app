import { api } from "@/lib/api";
import type { PageParams } from "@/services/pagination";
import type {
  AgendaAvailability,
  AgendaBlock,
  AgendaBlockCreated,
  AgendaBlockPayload,
  AgendaOpening,
  AgendaOpeningPayload,
  PaginatedResponse,
} from "@/types/api";

/**
 * Agenda del negocio (FASE-5-CONTRATO §4.1, PR 5.1). Leer: `AGENDA_VER`. Bloquear y
 * abrir: `TURNOS_GESTIONAR`. Los rangos se mandan en hora local del negocio (día y
 * horas opcionales; sin horas es el día completo) y vuelven como instantes UTC.
 *
 * Las altas mandan `Idempotency-Key` si hay clave: un doble toque no crea dos filas.
 */
function withKey(idempotencyKey?: string) {
  return idempotencyKey ? { headers: { "Idempotency-Key": idempotencyKey } } : undefined;
}

export async function listBlocks(params: PageParams): Promise<PaginatedResponse<AgendaBlock>> {
  const res = await api.get<PaginatedResponse<AgendaBlock>>("/agenda/blocks", { params });
  return res.data;
}

/** Devuelve el bloqueo y los turnos activos que quedaron adentro (no los cancela). */
export async function createBlock(
  data: AgendaBlockPayload,
  idempotencyKey?: string,
): Promise<AgendaBlockCreated> {
  const res = await api.post<AgendaBlockCreated>("/agenda/blocks", data, withKey(idempotencyKey));
  return res.data;
}

export async function removeBlock(id: string): Promise<void> {
  await api.delete(`/agenda/blocks/${id}`);
}

export async function listOpenings(params: PageParams): Promise<PaginatedResponse<AgendaOpening>> {
  const res = await api.get<PaginatedResponse<AgendaOpening>>("/agenda/openings", { params });
  return res.data;
}

export async function createOpening(
  data: AgendaOpeningPayload,
  idempotencyKey?: string,
): Promise<AgendaOpening> {
  const res = await api.post<AgendaOpening>("/agenda/openings", data, withKey(idempotencyKey));
  return res.data;
}

export async function removeOpening(id: string): Promise<void> {
  await api.delete(`/agenda/openings/${id}`);
}

/** Inicios libres de un día para servicios y tamaño, con los puestos libres en cada uno. */
export async function getAvailability(params: {
  date: string;
  vehicle_size_id: string;
  service_ids: string[];
}): Promise<AgendaAvailability> {
  const res = await api.get<AgendaAvailability>("/agenda/availability", {
    params,
    // `service_ids=a&service_ids=b`, como lo lee FastAPI (sin corchetes).
    paramsSerializer: { indexes: null },
  });
  return res.data;
}
