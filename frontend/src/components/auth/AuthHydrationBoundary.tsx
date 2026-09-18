"use client";

import { useAuthStore } from "@/stores/authStore";

/**
 * No renderiza hasta que zustand terminó de rehidratar el store persistido.
 *
 * Sin esto, el primer render ve `user: null` (el valor inicial) aunque haya un
 * usuario guardado, y la UI parpadea "no logueado" — o peor, algún efecto
 * decide redirigir — antes de que llegue el valor real. `_hasHydrated` lo
 * prende `onRehydrateStorage` en `authStore`.
 *
 * Con ADR-0009 lo que se hidrata ya no es el token (vive en cookie HttpOnly)
 * sino el usuario para pintar el shell.
 */
export function AuthHydrationBoundary({ children }: { children: React.ReactNode }) {
  const hasHydrated = useAuthStore((s) => s._hasHydrated);

  if (!hasHydrated) {
    return (
      <div
        role="status"
        aria-label="Cargando"
        className="flex h-screen items-center justify-center bg-background"
      >
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-muted border-t-primary" />
      </div>
    );
  }

  return <>{children}</>;
}
