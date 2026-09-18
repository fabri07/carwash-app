import axios from "axios";

import type { ApiError, ApiErrorCode } from "@/types/api";

/** Código de error del envelope único (`{detail: {code}}`), si vino. */
export function apiErrorCode(e: unknown): ApiErrorCode | undefined {
  if (!axios.isAxiosError(e)) return undefined;
  return (e.response?.data as Partial<ApiError> | undefined)?.detail?.code;
}

const MESSAGES: Partial<Record<ApiErrorCode, string>> = {
  INVALID_CREDENTIALS: "Email o contraseña incorrectos.",
  EMAIL_TAKEN: "Ya existe una cuenta con ese email.",
  RATE_LIMITED: "Demasiados intentos. Esperá unos minutos.",
  ORIGIN_NOT_ALLOWED: "El pedido fue rechazado por seguridad. Recargá la página.",
};

/**
 * Traduce un error de la API a un mensaje para el usuario. Primero por código
 * del contrato; si no vino, por estado. Un login fallido no dice si el email
 * existe.
 */
export function authErrorMessage(e: unknown, context: "login" | "register"): string {
  if (!axios.isAxiosError(e)) return "Ocurrió un error inesperado. Probá de nuevo.";
  if (!e.response) return "No hay conexión con el servidor. Revisá tu internet y probá de nuevo.";
  const code = apiErrorCode(e);
  const byCode = code ? MESSAGES[code] : undefined;
  if (byCode) return byCode;
  const { status } = e.response;
  if (context === "login" && status === 401) return MESSAGES.INVALID_CREDENTIALS!;
  if (context === "register" && status === 409) return MESSAGES.EMAIL_TAKEN!;
  if (status === 422 || status === 400) return "Revisá los datos ingresados.";
  if (status === 429) return MESSAGES.RATE_LIMITED!;
  return "El servidor no pudo procesar el pedido. Probá de nuevo en unos minutos.";
}

/** Caracteres de control C0, DEL y C1: `new URL` los descarta y cambian el significado de la ruta. */
// eslint-disable-next-line no-control-regex
const CONTROL_OR_BACKSLASH = /[\u0000-\u001f\u007f-\u009f\\]/;

function currentOrigin(): string {
  return typeof window !== "undefined" ? window.location.origin : "http://localhost";
}

function looksExternal(candidate: string): boolean {
  return (
    !candidate.startsWith("/") || candidate.startsWith("//") || CONTROL_OR_BACKSLASH.test(candidate)
  );
}

/**
 * `?next=` viene de la URL: sin validar es un open redirect.
 *
 * No alcanza con mirar el prefijo: `/\t/evil.example` empieza con una sola
 * barra, pero `new URL` descarta el tab y lo normaliza a `//evil.example`, que
 * `router.replace` sigue a otro origen. Por eso: (1) se rechazan caracteres de
 * control y backslash, también en la forma decodificada (`/%09/evil.example`,
 * `%2F%2Fevil.example`); (2) la resolución contra el origen actual tiene que
 * quedar en el mismo origen. Se devuelve la forma ya resuelta, no la cruda.
 */
export function safeNextPath(next: string | null | undefined, fallback = "/dashboard"): string {
  if (!next || looksExternal(next)) return fallback;
  let decoded: string;
  try {
    decoded = decodeURIComponent(next);
  } catch {
    return fallback;
  }
  if (looksExternal(decoded)) return fallback;
  const origin = currentOrigin();
  let url: URL;
  try {
    url = new URL(next, origin);
  } catch {
    return fallback;
  }
  if (url.origin !== origin) return fallback;
  return `${url.pathname}${url.search}${url.hash}`;
}
