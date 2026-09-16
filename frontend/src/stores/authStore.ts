import * as Sentry from "@sentry/nextjs";
import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { AuthResponse, Tenant, User } from "@/types/api";

/**
 * Estado de sesión del cliente.
 *
 * ADR-0009: los tokens viven en cookies `HttpOnly` que el JS nunca ve. Lo único
 * que se persiste es `user` y `tenant`, para pintar el shell sin parpadeo al recargar.
 * La autoridad sobre "¿hay sesión?" es el backend (y, para evitar el HTML
 * filtrado, el `middleware.ts`), no este store.
 */
interface AuthState {
  user: User | null;
  tenant: Tenant | null;
  _hasHydrated: boolean;
  /** Guarda lo que devuelven login, registro, refresh y `/auth/me`. */
  setSession: (session: AuthResponse) => void;
  setHasHydrated: (state: boolean) => void;
  /** Limpia el estado local. No habla con el backend: eso es `auth.service.logout`. */
  clear: () => void;
}

// Sentry: tag por negocio, sin email ni nombre — espejo de
// `deps.py::get_current_user` del backend.
export function syncSentryContext(user: User | null): void {
  if (user) {
    Sentry.setUser({ id: user.id });
    Sentry.setTag("tenant_id", user.tenant_id);
  } else {
    Sentry.setUser(null);
    Sentry.setTag("tenant_id", undefined);
  }
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      tenant: null,
      _hasHydrated: false,
      setSession: ({ user, tenant }) => {
        syncSentryContext(user);
        set({ user, tenant });
      },
      setHasHydrated: (state) => set({ _hasHydrated: state }),
      clear: () => {
        syncSentryContext(null);
        set({ user: null, tenant: null });
      },
    }),
    {
      name: "carwash_auth",
      version: 1,
      // Solo usuario y tenant. Si alguna vez aparece un token acá, la guarda
      // de ADR-0009 (`meta/sin-token-en-localstorage.test.ts`) se pone en rojo.
      partialize: (state) => ({ user: state.user, tenant: state.tenant }),
      onRehydrateStorage: () => (state) => {
        syncSentryContext(state?.user ?? null);
        state?.setHasHydrated(true);
      },
    },
  ),
);
