"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";

import { pendingForCurrentUser } from "@/hooks/useOfflineSubmit";
import { clearSwCaches } from "@/lib/sw-register";
import { logoutRequest } from "@/services/auth.service";
import { useAuthStore } from "@/stores/authStore";

export function pendingLogoutMessage(count: number): string {
  return count === 1
    ? "Tenés 1 carga sin sincronizar. Si cerrás sesión, se va a mandar recién cuando vuelvas a entrar con este usuario en este dispositivo. ¿Cerrar sesión igual?"
    : `Tenés ${count} cargas sin sincronizar. Si cerrás sesión, se van a mandar recién cuando vuelvas a entrar con este usuario en este dispositivo. ¿Cerrar sesión igual?`;
}

/**
 * Cierre de sesión. ADR-0009 §7: la invalidación es del servidor (revoca el
 * refresh y manda cookies vencidas); lo local se limpia pase lo que pase.
 *
 * La cola offline NO se borra (serían cargas perdidas): queda atada a su
 * usuario y tenant, y el flush de otra sesión no la toca. Si hay cargas
 * pendientes, se avisa antes y se puede cancelar.
 */
export function useLogout(confirmFn: (message: string) => boolean = (m) => window.confirm(m)) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const clear = useAuthStore((s) => s.clear);
  const [loggingOut, setLoggingOut] = useState(false);

  /** `false` si el usuario canceló por tener cargas pendientes. */
  async function logout(): Promise<boolean> {
    const pending = pendingForCurrentUser().length;
    if (pending > 0 && !confirmFn(pendingLogoutMessage(pending))) return false;

    setLoggingOut(true);
    let serverLoggedOut = true;
    try {
      await logoutRequest();
    } catch {
      serverLoggedOut = false;
    } finally {
      clear();
      queryClient.clear();
      await clearSwCaches().catch(() => undefined);
      // Si el servidor no respondió, las cookies siguen puestas y el
      // middleware rebotaría /login → /dashboard; `expired` lo evita.
      router.replace(serverLoggedOut ? "/login" : "/login?expired=1");
    }
    return true;
  }

  return { logout, loggingOut };
}
