"use client";

import { useRouter, useSearchParams } from "next/navigation";

import { refreshSession } from "@/lib/api";
import { authErrorMessage, safeNextPath } from "@/lib/errors";
import { loginRequest, registerRequest } from "@/services/auth.service";
import { useAuthStore } from "@/stores/authStore";
import type { LoginInput, RegisterInput } from "@/validation/auth";

/**
 * Pega los formularios con el servicio: llama a la API, guarda el usuario en el
 * store (las cookies las puso el backend) y navega a `?next=` validado.
 * Los errores salen como `Error` con un mensaje para el usuario, que es lo que
 * los formularios muestran.
 */
export function useAuthActions() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const setSession = useAuthStore((s) => s.setSession);
  const next = safeNextPath(searchParams.get("next"));

  async function login(values: LoginInput): Promise<void> {
    try {
      const res = await loginRequest(values);
      setSession(res);
    } catch (e) {
      throw new Error(authErrorMessage(e, "login"));
    }
    router.replace(next);
  }

  async function register(values: RegisterInput): Promise<void> {
    try {
      const res = await registerRequest(values);
      setSession(res);
    } catch (e) {
      throw new Error(authErrorMessage(e, "register"));
    }
    router.replace(next);
  }

  /**
   * El access token vence en minutos y su cookie desaparece con él; el refresh
   * solo viaja a `/v1/auth`. Por eso el middleware manda a /login a alguien que
   * todavía tiene sesión renovable. Antes de pedirle la contraseña, se prueba
   * un refresh: si sale, vuelve adonde iba. Con `?expired=1` no se intenta —
   * ya se sabe que el refresh falló.
   */
  async function trySilentRefresh(): Promise<boolean> {
    if (searchParams.has("expired")) return false;
    try {
      setSession(await refreshSession());
    } catch {
      return false;
    }
    router.replace(next);
    return true;
  }

  return { login, register, trySilentRefresh };
}
