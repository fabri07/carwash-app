import { api } from "@/lib/api";
import type { AuthResponse, LoginPayload, RegisterPayload } from "@/types/api";

/**
 * Servicio HTTP de auth. Con cookies HttpOnly (ADR-0009) ninguna función
 * devuelve ni recibe tokens: el backend los pone con `Set-Cookie` y el
 * navegador los manda solo. Las formas salen del OpenAPI generado (ADR-0013).
 */
export async function loginRequest(data: LoginPayload): Promise<AuthResponse> {
  const res = await api.post<AuthResponse>("/auth/login", data);
  return res.data;
}

export async function registerRequest(data: RegisterPayload): Promise<AuthResponse> {
  const res = await api.post<AuthResponse>("/auth/register", data);
  return res.data;
}

export async function getMeRequest(): Promise<AuthResponse> {
  const res = await api.get<AuthResponse>("/auth/me");
  return res.data;
}

/** Revoca el refresh token del lado del servidor y recibe cookies vencidas (ADR-0009 §7). */
export async function logoutRequest(): Promise<void> {
  await api.post("/auth/logout");
}
