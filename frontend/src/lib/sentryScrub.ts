import type { ErrorEvent, EventHint } from "@sentry/nextjs";

/**
 * `sendDefaultPii: false` evita que el SDK agregue PII automáticamente, pero no
 * filtra lo que el código agrega explícitamente: el `config` de un `AxiosError`
 * capturado (headers, body), breadcrumbs o `extra`. Este scrubbing es una capa
 * aparte, compartida por el runtime de browser y el de servidor.
 *
 * Tiene que coincidir con `backend/app/observability/sentry.py`: si los dos
 * scrubbers divergen, el PII se filtra por el lado que quedó viejo. Cambio de
 * vocabulario respecto de Véktor: fuera CUIT/monto/proveedor, dentro patente,
 * dominio, teléfono y nombre de cliente. La patente es el PII fuerte de este
 * dominio.
 */

export const REDACTED = "[Filtered]";

export const SENSITIVE_KEY_RE =
  /(authoriz|token|password|cookie|secret|dni|email|phone|telefono|amount|customer_name|nombre_cliente|patente|dominio)/i;

/** Patente argentina: Mercosur `AA123BB` y formato viejo `AAA123`. */
export const PATENTE_VALUE_RE = /\b(?:[A-Z]{2}\s?-?\d{3}\s?-?[A-Z]{2}|[A-Z]{3}\s?-?\d{3})\b/i;
export const DNI_VALUE_RE = /\b\d{1,2}\.?\d{3}\.?\d{3}\b/;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasSensitiveValue(value: string): boolean {
  return PATENTE_VALUE_RE.test(value) || DNI_VALUE_RE.test(value);
}

export function redactValue(key: string, value: unknown): unknown {
  if (SENSITIVE_KEY_RE.test(key)) return REDACTED;
  if (typeof value === "string" && hasSensitiveValue(value)) return REDACTED;
  return value;
}

function scrubMapping(data: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(data)) {
    result[key] = redactValue(key, value);
  }
  return result;
}

interface AxiosLikeError {
  isAxiosError?: boolean;
  config?: {
    headers?: unknown;
    data?: unknown;
  };
}

/**
 * `beforeSend` compartido. Redacta, en orden:
 * 1. Headers, query string y body del `request` del evento.
 * 2. `config.headers`/`config.data` de un `AxiosError` capturado.
 * 3. `extra` y `breadcrumbs[*].data`.
 */
export function scrubSentryEvent(event: ErrorEvent, hint: EventHint): ErrorEvent | null {
  if (event.request) {
    if (isPlainObject(event.request.headers)) {
      event.request.headers = scrubMapping(event.request.headers) as typeof event.request.headers;
    }
    const qs = event.request.query_string;
    if (typeof qs === "string" && (SENSITIVE_KEY_RE.test(qs) || hasSensitiveValue(qs))) {
      event.request.query_string = REDACTED;
    }
    if (event.request.data !== undefined) {
      event.request.data = REDACTED;
    }
    if (typeof event.request.url === "string" && hasSensitiveValue(event.request.url)) {
      event.request.url = REDACTED;
    }
  }

  // Nunca mutar `config` in place: el AxiosError original puede seguir
  // referenciado por la app. Se adjunta una copia saneada como contexto.
  const original = hint.originalException as AxiosLikeError | undefined;
  if (original?.isAxiosError && original.config) {
    const { config } = original;
    const axiosContext: Record<string, unknown> = {};
    if (isPlainObject(config.headers)) {
      axiosContext.headers = scrubMapping(config.headers);
    }
    if (config.data !== undefined) {
      axiosContext.data = REDACTED;
    }
    if (Object.keys(axiosContext).length > 0) {
      event.contexts = { ...event.contexts, axios_request: axiosContext };
    }
  }

  if (isPlainObject(event.extra)) {
    event.extra = scrubMapping(event.extra);
  }

  for (const crumb of event.breadcrumbs ?? []) {
    if (isPlainObject(crumb.data)) {
      crumb.data = scrubMapping(crumb.data);
    }
    if (typeof crumb.message === "string" && hasSensitiveValue(crumb.message)) {
      crumb.message = REDACTED;
    }
  }

  return event;
}
