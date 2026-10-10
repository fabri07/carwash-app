import * as Sentry from "@sentry/nextjs";
import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { AuthResponse, PermissionCode, Tenant, User } from "@/types/api";

/**
 * Estado de sesión del cliente.
 *
 * ADR-0009: los tokens viven en cookies `HttpOnly` que el JS nunca ve. Lo único
 * que se persiste es `user` y `tenant`, para pintar el shell sin parpadeo al recargar.
 * La autoridad sobre "¿hay sesión?" es el backend (y, para evitar el HTML
 * filtrado, el `middleware.ts`), no este store.
 *
 * `permissions` y `mustChangePassword` (FASE-4-CONTRATO §2.3) se guardan en
 * memoria y NO se persisten: el backend los lee de la base en cada request y
 * pueden cambiar entre una visita y otra (el dueño edita el perfil). Llegan
 * frescos con cada `/auth/me`, que el shell pide al montar.
 */
interface AuthState {
  user: User | null;
  tenant: Tenant | null;
  /** Lo que el usuario puede hacer (el OWNER recibe todos). Solo para mostrar u ocultar. */
  permissions: PermissionCode[];
  /** Mientras sea `true`, la única pantalla útil es `/cambiar-clave`. */
  mustChangePassword: boolean;
  _hasHydrated: boolean;
  /** Guarda lo que devuelven login, registro, refresh y `/auth/me`. */
  setSession: (session: AuthResponse) => void;
  setHasHydrated: (state: boolean) => void;
  /** Lo marca un 403 `PASSWORD_CHANGE_REQUIRED` de cualquier request (`lib/api.ts`). */
  setMustChangePassword: (value: boolean) => void;
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
      permissions: [],
      mustChangePassword: false,
      _hasHydrated: false,
      setSession: ({ user, tenant, permissions, must_change_password }) => {
        syncSentryContext(user);
        set({ user, tenant, permissions, mustChangePassword: must_change_password });
      },
      setHasHydrated: (state) => set({ _hasHydrated: state }),
      setMustChangePassword: (value) => set({ mustChangePassword: value }),
      clear: () => {
        syncSentryContext(null);
        set({ user: null, tenant: null, permissions: [], mustChangePassword: false });
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
