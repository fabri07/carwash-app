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

// Configuración (PR 4.2): el negocio y el catálogo editable.
export type BusinessSettings = Schemas["SettingsResponse"];
export type BusinessSettingsPatch = Schemas["SettingsUpdate"];
export type VehicleSize = Schemas["VehicleSizeResponse"];
export type VehicleSizePayload = Schemas["VehicleSizeCreate"];
export type VehicleSizePatch = Schemas["VehicleSizeUpdate"];
export type Resource = Schemas["ResourceResponse"];
export type ResourcePayload = Schemas["ResourceCreate"];
export type ResourcePatch = Schemas["ResourceUpdate"];
export type BusinessHours = Schemas["BusinessHoursResponse"];
export type BusinessHoursPayload = Schemas["BusinessHoursCreate"];
export type BusinessHoursPatch = Schemas["BusinessHoursUpdate"];
export type PaymentMethod = Schemas["PaymentMethodResponse"];
export type PaymentMethodPayload = Schemas["PaymentMethodCreate"];
export type PaymentMethodPatch = Schemas["PaymentMethodUpdate"];

// Catálogo de servicios (PR 4.3): categorías, servicios con precio por tamaño y plantillas.
export type ServiceCategory = Schemas["ServiceCategoryResponse"];
export type ServiceCategoryPayload = Schemas["ServiceCategoryCreate"];
export type ServiceCategoryPatch = Schemas["ServiceCategoryUpdate"];
export type Service = Schemas["ServiceResponse"];
export type ServicePayload = Schemas["ServiceCreate"];
export type ServicePatch = Schemas["ServiceUpdate"];
export type ServicePrice = Schemas["ServicePriceResponse"];
export type ServicePriceItem = Schemas["ServicePriceInput"];
export type ServicePricesPayload = Schemas["ServicePricesPut"];
/** `PricingMode` es el nombre del schema. */
export type PricingModeCode = Schemas["PricingMode"];
/** `TemplateKind` es el nombre del schema. */
export type TemplateKindCode = Schemas["TemplateKind"];
export type Template = Schemas["TemplateResponse"];
export type TemplateGroup = Schemas["TemplateCategoryResponse"];
export type TemplateTerm = Schemas["TemplateTermResponse"];
export type TemplateApplyPayload = Schemas["TemplateApply"];
export type TemplateApplyItemPayload = Schemas["TemplateApplyItem"];
export type TemplateApplyResult = Schemas["TemplateApplyResponse"];
export type TemplateSkipped = Schemas["TemplateSkippedResponse"];

// Catálogo de vehículos (PR 4.4): marcas, modelos, colores y carrocería → tamaño.
export type VehicleMake = Schemas["VehicleMakeResponse"];
export type VehicleMakePayload = Schemas["VehicleMakeCreate"];
export type VehicleMakePatch = Schemas["VehicleMakeUpdate"];
export type VehicleModel = Schemas["VehicleModelResponse"];
export type VehicleModelPayload = Schemas["VehicleModelCreate"];
export type VehicleModelPatch = Schemas["VehicleModelUpdate"];
export type VehicleColor = Schemas["VehicleColorResponse"];
export type VehicleColorPayload = Schemas["VehicleColorCreate"];
export type VehicleColorPatch = Schemas["VehicleColorUpdate"];
/** `VehicleBodyType` es el nombre del schema. */
export type BodyTypeCode = Schemas["VehicleBodyType"];
export type BodyTypeSize = Schemas["BodyTypeSizeResponse"];
export type BodyTypeSizesPayload = Schemas["BodyTypeSizesPut"];

// Personalización (PR 4.5): catálogo de paletas y tipografías, logo y paleta sugerida.
export type BrandCatalog = Schemas["BrandingCatalog"];
export type BrandPalette = Schemas["PaletteOut"];
export type BrandFont = Schemas["FontOut"];
export type BrandSuggestion = Schemas["PaletteSuggestion"];
export type LogoUpload = Schemas["LogoResponse"];

// Wizard de alta (PR 4.6): estado por paso, faltantes y el 409 de `/onboarding/complete`.
export type OnboardingStatus = Schemas["OnboardingStatusResponse"];
/** `OnboardingStep` es el nombre del schema. */
export type OnboardingStepCode = Schemas["OnboardingStep"];
/** `OnboardingRequirement` es el nombre del schema. */
export type OnboardingRequirementCode = Schemas["OnboardingRequirement"];
export type OnboardingMissingItem = Schemas["OnboardingMissing"];
export type OnboardingIncomplete = Schemas["OnboardingIncompleteResponse"];

// Agenda (PR 5.1, FASE-5-CONTRATO §3–§5): modo de reserva y reglas del servicio, bloqueos,
// aperturas especiales y disponibilidad interna.
/** `ServiceBookingMode` es el nombre del schema. */
export type BookingModeCode = Schemas["ServiceBookingMode"];
/** `BookingStatus` es el nombre del schema. */
export type BookingStatusCode = Schemas["BookingStatus"];
export type ScheduleRule = Schemas["ScheduleRuleResponse"];
export type ScheduleRuleItem = Schemas["ScheduleRuleInput"];
export type AgendaBlock = Schemas["BlockResponse"];
export type AgendaBlockPayload = Schemas["BlockCreate"];
export type AgendaBlockCreated = Schemas["BlockCreatedResponse"];
export type AgendaOverlap = Schemas["OverlappingBooking"];
export type AgendaOpening = Schemas["OpeningResponse"];
export type AgendaOpeningPayload = Schemas["OpeningCreate"];
export type AgendaAvailability = Schemas["AvailabilityResponse"];

// Turnero público (PR 5.2, FASE-5-CONTRATO §4.2): `/v1/public/{slug}/…`, sin sesión.
export type TurneroBusiness = Schemas["PublicBusinessResponse"];
export type TurneroCatalog = Schemas["PublicCatalogResponse"];
export type TurneroService = Schemas["PublicServiceOut"];
export type TurneroSize = Schemas["PublicVehicleSize"];
export type TurneroAvailability = Schemas["PublicAvailabilityResponse"];
export type TurneroSlot = Schemas["PublicSlot"];
export type TurneroBookingPayload = Schemas["PublicBookingCreate"];
export type TurneroBooking = Schemas["PublicBookingResponse"];
export type TurneroLookupPayload = Schemas["PublicBookingLookup"];
export type TurneroCancelPayload = Schemas["PublicBookingCancel"];
export type TurneroCancellation = Schemas["PublicCancellationResponse"];

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
