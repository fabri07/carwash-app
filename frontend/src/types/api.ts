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

/** Envelope único de errores: `{ detail: { code, message, field } }`. */
export type ApiError = Schemas["ErrorResponse"];
export type ApiErrorCode = Schemas["ErrorCode"];

/**
 * Envelope único de colecciones (ADR-0006), genérico. Se deriva de la única
 * instancia concreta del contrato para no redeclarar sus campos a mano.
 */
export type PaginatedResponse<T> = Omit<
  Schemas["PaginatedResponse_DummyResourceResponse_"],
  "items"
> & {
  items: T[];
};
