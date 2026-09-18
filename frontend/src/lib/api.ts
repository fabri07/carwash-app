import * as Sentry from "@sentry/nextjs";
import axios, { type AxiosError, type InternalAxiosRequestConfig } from "axios";

import { useAuthStore } from "@/stores/authStore";
import type { AuthResponse } from "@/types/api";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * Cliente HTTP de la app. ADR-0009: `withCredentials` para que el navegador
 * mande las cookies `HttpOnly`; no hay header `Authorization` porque el JS no
 * tiene ningún token que poner.
 */
export const api = axios.create({
  baseURL: `${API_URL}/v1`,
  headers: { "Content-Type": "application/json" },
  timeout: 15_000,
  withCredentials: true,
});

/**
 * Cliente aparte para el refresh: si usara `api`, un 401 del propio refresh
 * volvería a entrar al interceptor y dispararía otro refresh.
 */
export const authClient = axios.create({
  baseURL: `${API_URL}/v1`,
  timeout: 15_000,
  withCredentials: true,
});

type RetriableConfig = InternalAxiosRequestConfig & { _retry?: boolean };

/** Rutas cuyo 401 significa "credenciales inválidas", no "sesión vencida". */
const NO_REFRESH_PATHS = ["/auth/login", "/auth/register", "/auth/refresh", "/auth/logout"];

/**
 * Single-flight del refresh (Véktor `lib/api.ts:15,117-131`).
 *
 * El mecanismo: la primera respuesta 401 crea la promesa y la guarda en el
 * módulo; las 401 que llegan mientras esa promesa está pendiente encuentran la
 * variable ocupada y se cuelgan de la MISMA promesa en vez de crear otra. Así,
 * N queries que vencen juntas producen UN `POST /auth/refresh`, no N. El
 * backend no rota con revocación (un refresh viejo sigue valiendo hasta que
 * vence), así que N refresh concurrentes no romperían la sesión: lo que se
 * evita es la tormenta de requests y N `Set-Cookie` compitiendo por pisarse.
 *
 * El `.finally()` libera la variable al terminar (bien o mal), para que el
 * próximo vencimiento, minutos después, dispare un refresh nuevo y no reciba
 * la promesa vieja ya resuelta.
 */
let _refreshPromise: Promise<AuthResponse> | null = null;

export function refreshSession(): Promise<AuthResponse> {
  if (!_refreshPromise) {
    _refreshPromise = authClient
      .post<AuthResponse>("/auth/refresh")
      .then((r) => r.data)
      .finally(() => {
        _refreshPromise = null;
      });
  }
  return _refreshPromise;
}

/** Indirección sobre `window.location` para poder observarla en tests (jsdom no deja espiarla). */
export const browserNavigation = {
  assign: (url: string): void => window.location.assign(url),
};

/** La sesión murió de verdad: limpiar estado local y volver al login. */
export function onSessionExpired(): void {
  useAuthStore.getState().clear();
  if (typeof window !== "undefined") {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    browserNavigation.assign(`/login?next=${next}&expired=1`);
  }
}

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  // Trazabilidad: una correlación por request (frontend → API → response).
  if (!config.headers["X-Trace-Id"]) {
    config.headers["X-Trace-Id"] = crypto.randomUUID();
  }
  Sentry.addBreadcrumb({
    category: "http",
    message: `${config.method?.toUpperCase()} ${config.url}`,
    data: { trace_id: config.headers["X-Trace-Id"] },
    level: "info",
  });
  return config;
});

export async function handleApiResponseError(error: AxiosError): Promise<unknown> {
  const originalRequest = error.config as RetriableConfig | undefined;

  if (!error.response) {
    // Una request cancelada a propósito (AbortController de React Query) llega
    // con la misma forma que un error de red. No es un fallo: solo breadcrumb.
    if (axios.isCancel(error)) {
      Sentry.addBreadcrumb({
        category: "http",
        message: `canceled: ${originalRequest?.method?.toUpperCase()} ${originalRequest?.url}`,
        level: "info",
      });
      return Promise.reject(error);
    }
    // Red caída, timeout, DNS o CORS: el backend nunca vio la request y nunca
    // la reportó. Sin este evento quedaría invisible en los dos proyectos.
    Sentry.captureException(error, {
      tags: { trace_id: originalRequest?.headers?.["X-Trace-Id"] as string | undefined },
    });
    return Promise.reject(error);
  }

  if (error.response.status >= 500) {
    // El backend ya generó su evento; acá solo la referencia para cruzarlos.
    Sentry.addBreadcrumb({
      category: "http",
      message: `5xx: ${originalRequest?.method?.toUpperCase()} ${originalRequest?.url}`,
      data: { trace_id: error.response.headers?.["x-trace-id"] },
      level: "error",
    });
  }

  const url = originalRequest?.url ?? "";
  const isRefreshable = !NO_REFRESH_PATHS.some((p) => url.includes(p));

  if (error.response.status === 401 && originalRequest && isRefreshable) {
    // ADR-0009: el cliente no puede leer el `exp` de una cookie HttpOnly, así
    // que el refresh lo dispara el 401 y no un reloj. Un solo reintento por
    // request (`_retry`): si después del refresh sigue en 401, no hay loop.
    if (originalRequest._retry) {
      onSessionExpired();
      return Promise.reject(error);
    }
    originalRequest._retry = true;
    try {
      await refreshSession();
    } catch (refreshError) {
      onSessionExpired();
      return Promise.reject(refreshError);
    }
    return api.request(originalRequest);
  }

  return Promise.reject(error);
}

api.interceptors.response.use((response) => response, handleApiResponseError);
