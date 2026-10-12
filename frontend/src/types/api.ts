/**
 * Alias cortos sobre los tipos generados (ADR-0013).
 *
 * Nada de acá se escribe a mano: todo sale de `api.generated.ts`, que genera
 * `npm run gen:api` desde `backend/openapi.json`. Si un alias deja de compilar,
 * cambió el contrato — se ajusta el uso, no el tipo.
 *
 * Los nombres NO pueden coincidir con un schema del backend (lo verifica
 * `src/__tests__/meta/tipos-generados.test.ts`); por eso `UserRole` y no `Role`,
 * `ApiErrorCode` y no `ErrorCode`.
 */
import type { components } from "./api.generated";

type Schemas = components["schemas"];

export type User = Schemas["UserResponse"];
export type Tenant = Schemas["TenantResponse"];
export type UserRole = Schemas["Role"];

/** Lo que devuelven registro, login, refresh y `/auth/me`. Nunca tokens (ADR-0009). */
export type AuthResponse = Schemas["MeResponse"];

export type LoginPayload = Schemas["LoginRequest"];
export type RegisterPayload = Schemas["RegisterRequest"];
export type ChangePasswordPayload = Schemas["ChangePasswordRequest"];

/** Permiso del enum congelado (FASE-4-CONTRATO §2.1). `Permission` es el nombre del schema. */
export type PermissionCode = Schemas["Permission"];

// Equipo (PR 4.1): perfiles de permisos y empleados.
export type PermissionProfile = Schemas["PermissionProfileResponse"];
export type PermissionProfilePayload = Schemas["PermissionProfileCreate"];
export type PermissionProfilePatch = Schemas["PermissionProfileUpdate"];
export type StaffMember = Schemas["StaffResponse"];
export type StaffCreatePayload = Schemas["StaffCreate"];
export type StaffPatch = Schemas["StaffUpdate"];
export type StaffPasswordPayload = Schemas["StaffPasswordReset"];

// Configuración del negocio y catálogos de operación (PR 4.2).
export type TenantSettings = Schemas["SettingsResponse"];
/** Cambio parcial: campos ausentes no se tocan; `null` borra un texto opcional. */
export type SettingsPatch = Schemas["SettingsUpdate"];
export type VehicleSize = Schemas["VehicleSizeResponse"];
export type VehicleSizePayload = Schemas["VehicleSizeCreate"];
export type VehicleSizePatch = Schemas["VehicleSizeUpdate"];
/** Puesto de trabajo (box, rampa): lo que ocupa un turno. */
export type Resource = Schemas["ResourceResponse"];
export type ResourcePayload = Schemas["ResourceCreate"];
export type ResourcePatch = Schemas["ResourceUpdate"];
/** Franja de atención; `weekday` ISO: 1 = lunes … 7 = domingo. */
export type BusinessHour = Schemas["BusinessHoursResponse"];
export type BusinessHourPayload = Schemas["BusinessHoursCreate"];
export type BusinessHourPatch = Schemas["BusinessHoursUpdate"];
export type PaymentMethod = Schemas["PaymentMethodResponse"];
export type PaymentMethodPayload = Schemas["PaymentMethodCreate"];
export type PaymentMethodPatch = Schemas["PaymentMethodUpdate"];

/** Envelope único de errores: `{ detail: { code, message, field } }`. */
export type ApiError = Schemas["ErrorResponse"];
export type ApiErrorCode = Schemas["ErrorCode"];

/**
 * Envelope único de colecciones (ADR-0006), genérico. Se deriva de la única
 * instancia concreta del contrato para no redeclarar sus campos a mano.
 */
export type PaginatedResponse<T> = Omit<Schemas["PaginatedResponse_StaffResponse_"], "items"> & {
  items: T[];
};
