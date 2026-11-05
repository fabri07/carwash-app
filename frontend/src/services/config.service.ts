import { api } from "@/lib/api";
import type { PageParams } from "@/services/pagination";
import type {
  BusinessHours,
  BusinessHoursPatch,
  BusinessHoursPayload,
  BusinessSettings,
  BusinessSettingsPatch,
  PaginatedResponse,
  PaymentMethod,
  PaymentMethodPatch,
  PaymentMethodPayload,
  Resource,
  ResourcePatch,
  ResourcePayload,
  VehicleSize,
  VehicleSizePatch,
  VehicleSizePayload,
} from "@/types/api";

/**
 * Configuración del negocio y catálogo editable (FASE-4-CONTRATO §4, PR 4.2).
 * Lectura: cualquier usuario autenticado. Escritura: `PATCH /settings` solo el
 * dueño; el ABM del catálogo, `CATALOGO_EDITAR`.
 *
 * Los `POST` mandan `Idempotency-Key`: un doble toque no crea dos filas. El
 * replay vuelve con 409 `DUPLICATE_IDEMPOTENT`, que quien llama trata como
 * "ya estaba hecho".
 */
export async function getSettings(): Promise<BusinessSettings> {
  const res = await api.get<BusinessSettings>("/settings");
  return res.data;
}

/** Campos ausentes no se tocan; `null` borra un opcional. Cambiar el `slug` es solo del dueño. */
export async function patchSettings(data: BusinessSettingsPatch): Promise<BusinessSettings> {
  const res = await api.patch<BusinessSettings>("/settings", data);
  return res.data;
}

/** ABM paginado de un recurso del catálogo. */
export interface CrudApi<R, C, U> {
  list: (params: PageParams) => Promise<PaginatedResponse<R>>;
  create: (data: C, idempotencyKey?: string) => Promise<R>;
  update: (id: string, data: U) => Promise<R>;
  /** 409 `IN_USE` si todavía se usa. */
  remove: (id: string) => Promise<void>;
}

function crud<R, C, U>(path: string): CrudApi<R, C, U> {
  return {
    async list(params) {
      const res = await api.get<PaginatedResponse<R>>(path, { params });
      return res.data;
    },
    async create(data, idempotencyKey) {
      const res = await api.post<R>(
        path,
        data,
        idempotencyKey ? { headers: { "Idempotency-Key": idempotencyKey } } : undefined,
      );
      return res.data;
    },
    async update(id, data) {
      const res = await api.patch<R>(`${path}/${id}`, data);
      return res.data;
    },
    async remove(id) {
      await api.delete(`${path}/${id}`);
    },
  };
}

export const vehicleSizesApi = crud<VehicleSize, VehicleSizePayload, VehicleSizePatch>(
  "/vehicle-sizes",
);
export const resourcesApi = crud<Resource, ResourcePayload, ResourcePatch>("/resources");
export const businessHoursApi = crud<BusinessHours, BusinessHoursPayload, BusinessHoursPatch>(
  "/business-hours",
);
export const paymentMethodsApi = crud<PaymentMethod, PaymentMethodPayload, PaymentMethodPatch>(
  "/payment-methods",
);
