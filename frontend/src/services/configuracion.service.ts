import { api } from "@/lib/api";
import type { PageParams } from "@/services/team.service";
import type {
  BusinessHour,
  BusinessHourPatch,
  BusinessHourPayload,
  PaginatedResponse,
  PaymentMethod,
  PaymentMethodPatch,
  PaymentMethodPayload,
  Resource,
  ResourcePatch,
  ResourcePayload,
  SettingsPatch,
  TenantSettings,
  VehicleSize,
  VehicleSizePatch,
  VehicleSizePayload,
} from "@/types/api";

/**
 * Configuración del negocio y catálogos de operación (FASE-4-CONTRATO §3.2 y
 * §4, PR 4.2).
 *
 * - `/settings`: lectura para todo el equipo, `PATCH` solo el dueño. Es un
 *   cambio parcial: cada sección de "Mi negocio" manda solo sus campos.
 * - Catálogos (tamaños, puestos, franjas, medios de pago): lectura para todo el
 *   equipo, escritura con `CATALOGO_EDITAR`.
 *
 * Los `POST` mandan `Idempotency-Key` igual que el equipo: el replay vuelve con
 * 409 `DUPLICATE_IDEMPOTENT` y quien llama lo trata como "ya estaba hecho".
 */

function idempotencyHeaders(key?: string) {
  return key ? { headers: { "Idempotency-Key": key } } : undefined;
}

// ── Configuración ───────────────────────────────────────────────────────────

export async function getSettings(): Promise<TenantSettings> {
  const res = await api.get<TenantSettings>("/settings");
  return res.data;
}

/** 409 `SLUG_TAKEN` si la dirección ya la usa otro negocio. */
export async function updateSettings(data: SettingsPatch): Promise<TenantSettings> {
  const res = await api.patch<TenantSettings>("/settings", data);
  return res.data;
}

// ── Catálogos ───────────────────────────────────────────────────────────────

/** Los cuatro catálogos tienen la misma forma de CRUD; cambia la ruta y el tipo. */
function catalog<T, Create, Patch>(path: string) {
  return {
    async list(params: PageParams): Promise<PaginatedResponse<T>> {
      const res = await api.get<PaginatedResponse<T>>(path, { params });
      return res.data;
    },
    async create(data: Create, idempotencyKey?: string): Promise<T> {
      const res = await api.post<T>(path, data, idempotencyHeaders(idempotencyKey));
      return res.data;
    },
    async update(id: string, data: Patch): Promise<T> {
      const res = await api.patch<T>(`${path}/${id}`, data);
      return res.data;
    },
    /** Anula (ADR-0003). */
    async remove(id: string): Promise<void> {
      await api.delete(`${path}/${id}`);
    },
  };
}

const vehicleSizes = catalog<VehicleSize, VehicleSizePayload, VehicleSizePatch>("/vehicle-sizes");
const resources = catalog<Resource, ResourcePayload, ResourcePatch>("/resources");
const businessHours = catalog<BusinessHour, BusinessHourPayload, BusinessHourPatch>(
  "/business-hours",
);
const paymentMethods = catalog<PaymentMethod, PaymentMethodPayload, PaymentMethodPatch>(
  "/payment-methods",
);

/** 409 `CODE_TAKEN` si el código ya existe. */
export const listVehicleSizes = vehicleSizes.list;
export const createVehicleSize = vehicleSizes.create;
export const updateVehicleSize = vehicleSizes.update;
export const deleteVehicleSize = vehicleSizes.remove;

/** 409 `NAME_TAKEN` si el nombre ya existe; borrar uno con turnos por delante → 409 `RESOURCE_IN_USE`. */
export const listResources = resources.list;
export const createResource = resources.create;
export const updateResource = resources.update;
export const deleteResource = resources.remove;

/** 422 `VALIDATION_ERROR` si la franja cierra antes de abrir. */
export const listBusinessHours = businessHours.list;
export const createBusinessHour = businessHours.create;
export const updateBusinessHour = businessHours.update;
export const deleteBusinessHour = businessHours.remove;

/** 409 `CODE_TAKEN` si el código ya existe. */
export const listPaymentMethods = paymentMethods.list;
export const createPaymentMethod = paymentMethods.create;
export const updatePaymentMethod = paymentMethods.update;
export const deletePaymentMethod = paymentMethods.remove;
