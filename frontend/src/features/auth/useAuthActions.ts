"use client";

import { useRouter, useSearchParams } from "next/navigation";

import { refreshSession } from "@/lib/api";
import { authErrorMessage, safeNextPath } from "@/lib/errors";
import { CHANGE_PASSWORD_PATH } from "@/lib/routes";
import { loginRequest, registerRequest } from "@/services/auth.service";
import { useAuthStore } from "@/stores/authStore";
import type { AuthResponse } from "@/types/api";
import type { LoginInput, RegisterInput } from "@/validation/auth";

/**
 * Pega los formularios con el servicio: llama a la API, guarda el usuario en el
 * store (las cookies las puso el backend) y navega a `?next=` validado.
 * Los errores salen como `Error` con un mensaje para el usuario, que es lo que
 * los formularios muestran.
 *
 * Un empleado con clave elegida por el dueño (`must_change_password`) va a
 * `/cambiar-clave` en vez de a `?next=`: cualquier otra pantalla le daría 403.
 */
export function useAuthActions() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const setSession = useAuthStore((s) => s.setSession);
  const next = safeNextPath(searchParams.get("next"));

  function enter(session: AuthResponse) {
    setSession(session);
    router.replace(session.must_change_password ? CHANGE_PASSWORD_PATH : next);
  }

  async function login(values: LoginInput): Promise<void> {
    let res: AuthResponse;
    try {
      res = await loginRequest(values);
    } catch (e) {
      throw new Error(authErrorMessage(e, "login"));
    }
    enter(res);
  }

  async function register(values: RegisterInput): Promise<void> {
    let res: AuthResponse;
    try {
      res = await registerRequest(values);
    } catch (e) {
      throw new Error(authErrorMessage(e, "register"));
    }
    enter(res);
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
    let res: AuthResponse;
    try {
      res = await refreshSession();
    } catch {
      return false;
    }
    enter(res);
    return true;
  }

  return { login, register, trySilentRefresh };
}
