import { api } from "@/lib/api";
import type {
  PaginatedResponse,
  PermissionProfile,
  PermissionProfilePatch,
  PermissionProfilePayload,
  StaffCreatePayload,
  StaffMember,
  StaffPasswordPayload,
  StaffPatch,
} from "@/types/api";

/**
 * Equipo: perfiles de permisos y empleados (FASE-4-CONTRATO §4, PR 4.1). Solo
 * el `OWNER`; a un `STAFF` el backend le responde 403.
 *
 * Los `POST` de alta mandan `Idempotency-Key`: un doble toque (o un reintento
 * tras un timeout) no crea dos empleados. El replay vuelve con 409
 * `DUPLICATE_IDEMPOTENT`, que quien llama trata como "ya estaba hecho".
 */
export interface PageParams {
  limit: number;
  offset: number;
}

function idempotencyHeaders(key?: string) {
  return key ? { headers: { "Idempotency-Key": key } } : undefined;
}

// ── Perfiles ────────────────────────────────────────────────────────────────

export async function listProfiles(
  params: PageParams,
): Promise<PaginatedResponse<PermissionProfile>> {
  const res = await api.get<PaginatedResponse<PermissionProfile>>("/permission-profiles", {
    params,
  });
  return res.data;
}

export async function createProfile(
  data: PermissionProfilePayload,
  idempotencyKey?: string,
): Promise<PermissionProfile> {
  const res = await api.post<PermissionProfile>(
    "/permission-profiles",
    data,
    idempotencyHeaders(idempotencyKey),
  );
  return res.data;
}

export async function updateProfile(
  id: string,
  data: PermissionProfilePatch,
): Promise<PermissionProfile> {
  const res = await api.patch<PermissionProfile>(`/permission-profiles/${id}`, data);
  return res.data;
}

/** 409 `PROFILE_IN_USE` si tiene empleados vivos. */
export async function deleteProfile(id: string): Promise<void> {
  await api.delete(`/permission-profiles/${id}`);
}

// ── Empleados ───────────────────────────────────────────────────────────────

export async function listStaff(params: PageParams): Promise<PaginatedResponse<StaffMember>> {
  const res = await api.get<PaginatedResponse<StaffMember>>("/staff", { params });
  return res.data;
}

export async function createStaff(
  data: StaffCreatePayload,
  idempotencyKey?: string,
): Promise<StaffMember> {
  const res = await api.post<StaffMember>("/staff", data, idempotencyHeaders(idempotencyKey));
  return res.data;
}

/** Campos ausentes no se tocan; `email: null` lo borra. */
export async function updateStaff(id: string, data: StaffPatch): Promise<StaffMember> {
  const res = await api.patch<StaffMember>(`/staff/${id}`, data);
  return res.data;
}

/** Desactiva (anula) y cierra sus sesiones. */
export async function deleteStaff(id: string): Promise<void> {
  await api.delete(`/staff/${id}`);
}

/** Clave nueva elegida por el dueño: el empleado la cambia al entrar y sus sesiones se cierran. */
export async function resetStaffPassword(
  id: string,
  data: StaffPasswordPayload,
): Promise<StaffMember> {
  const res = await api.post<StaffMember>(`/staff/${id}/reset-password`, data);
  return res.data;
}
