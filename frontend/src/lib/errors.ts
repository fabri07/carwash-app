import axios from "axios";

import type { ApiError, ApiErrorCode } from "@/types/api";

/** Código de error del envelope único (`{detail: {code}}`), si vino. */
export function apiErrorCode(e: unknown): ApiErrorCode | undefined {
  if (!axios.isAxiosError(e)) return undefined;
  return (e.response?.data as Partial<ApiError> | undefined)?.detail?.code;
}

/** Mensaje de error y, si vino, el campo del formulario al que pertenece. */
export interface FieldError {
  field: string | null;
  message: string;
}

/**
 * Error que un `onSubmit` le devuelve a su formulario: con `field`, el
 * formulario lo pone debajo de ese campo (`setError`); sin `field`, arriba del
 * botón. Así el 409 `USERNAME_TAKEN` aparece en "Usuario" y no en un toast.
 */
export class FormSubmitError extends Error {
  readonly field: string | null;

  constructor({ field, message }: FieldError) {
    super(message);
    this.name = "FormSubmitError";
    this.field = field;
  }
}

const MESSAGES: Partial<Record<ApiErrorCode, string>> = {
  // Un solo campo para email o usuario (D4-3): el mensaje no dice cuál falló.
  INVALID_CREDENTIALS: "Usuario, email o contraseña incorrectos.",
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

/** Mensaje genérico por estado, para cuando el código no tiene uno propio. */
function genericMessage(e: unknown): string {
  if (!axios.isAxiosError(e)) return "Ocurrió un error inesperado. Probá de nuevo.";
  if (!e.response) return "No hay conexión con el servidor. Revisá tu internet y probá de nuevo.";
  const { status } = e.response;
  if (status === 403) return "No tenés permiso para hacer esto.";
  if (status === 404) return "No se encontró lo que buscabas. Puede que alguien lo haya borrado.";
  if (status === 422 || status === 400) return "Revisá los datos ingresados.";
  if (status === 429) return MESSAGES.RATE_LIMITED!;
  return "El servidor no pudo procesar el pedido. Probá de nuevo en unos minutos.";
}

/**
 * Errores del equipo (empleados y perfiles), con el campo del formulario donde
 * mostrarlos. Los 409 del contrato (§4) van al campo que los causó; el resto,
 * arriba del botón (`field: null`).
 */
const TEAM_ERRORS: Partial<Record<ApiErrorCode, FieldError>> = {
  USERNAME_TAKEN: { field: "username", message: "Ese usuario ya existe. Probá con otro." },
  EMAIL_TAKEN: { field: "email", message: "Ese email ya está en uso en otra cuenta." },
  NAME_TAKEN: { field: "name", message: "Ya hay un perfil con ese nombre." },
  PROFILE_IN_USE: {
    field: null,
    message:
      "No se puede borrar: hay empleados con este perfil. Asignales otro perfil y probá de nuevo.",
  },
  PASSWORD_CHANGE_REQUIRED: {
    field: null,
    message: "Antes de seguir tenés que cambiar tu contraseña.",
  },
};

export function teamError(e: unknown): FieldError {
  const code = apiErrorCode(e);
  const known = code ? TEAM_ERRORS[code] : undefined;
  return known ?? { field: null, message: genericMessage(e) };
}

/** Errores del cambio de clave propio (`POST /auth/change-password`). */
export function changePasswordError(e: unknown): FieldError {
  const code = apiErrorCode(e);
  if (code === "INVALID_CREDENTIALS") {
    return { field: "current_password", message: "La contraseña actual no es correcta." };
  }
  if (code === "VALIDATION_ERROR") {
    return { field: "new_password", message: "La nueva tiene que ser distinta de la actual." };
  }
  return { field: null, message: genericMessage(e) };
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
