import type { components } from "@/types/api.generated";

/**
 * Alias de la Fase 6 sobre los tipos generados del OpenAPI (ADR-0013). Viven acá y no en
 * `types/api.ts` para que cada PR de la fase tenga su carpeta. Nada se escribe a mano.
 */
type Schemas = components["schemas"];

export type Job = Schemas["JobResponse"];
export type JobStatus = Schemas["JobStatus"];
export type JobEvent = Schemas["JobEventResponse"];
export type JobPayment = Schemas["PaymentBrief"];
export type DayResponse = Schemas["DayResponse"];
export type DayEntry = Schemas["DayEntryResponse"];
export type DayResource = Schemas["DayResource"];
export type ReceiveBody = Schemas["ReceiveBody"];
export type PaymentBody = Schemas["PaymentBody"];
export type VoidPaymentBody = Schemas["VoidPaymentBody"];
export type OccurredBody = Schemas["OccurredBody"];
export type CancelDelayBody = Schemas["CancelDelayBody"];
export type InspectionBody = Schemas["InspectionBody"];
export type ManualBookingBody = Schemas["ManualBookingBody"];
export type ManualBookingResponse = Schemas["ManualBookingResponse"];
export type RetrospectiveBody = Schemas["RetrospectiveBody"];
export type DirtLevel = Schemas["DirtLevel"];
export type JobEventsPage = Schemas["PaginatedResponse_JobEventResponse_"];
