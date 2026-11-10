"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { useTenantBranding } from "@/lib/branding";
import { CHANGE_PASSWORD_PATH, HOME_PATH, ONBOARDING_PATH } from "@/lib/routes";
import { getMeRequest } from "@/services/auth.service";
import { useAuthStore } from "@/stores/authStore";

/**
 * App shell. De Véktor se saca el `EconomicTicker`, el chat, el PIN gate y la
 * lógica de rutas de dashboard.
 *
 * Tampoco se hereda el `if (!token) router.replace("/login")`: con ADR-0009 el
 * anónimo no llega hasta acá — el `middleware.ts` lo corta antes de renderizar.
 * Lo que sí hace el shell es pedir `/auth/me`: refresca el usuario persistido y,
 * si la sesión murió del lado del servidor, el 401 dispara el refresh
 * single-flight y, si ese falla, la vuelta al login.
 *
 * Con `must_change_password` (FASE-4-CONTRATO §2.3) no se muestra ninguna
 * pantalla del shell: se manda a `/cambiar-clave`. El servidor ya responde 403
 * a todo lo demás; esto evita pintar una pantalla que solo mostraría errores.
 *
 * La paleta y la tipografía del negocio (PR 4.5) se aplican acá, con sesión
 * utilizable; al salir del shell vuelve el tema por defecto.
 *
 * Alta sin terminar (FASE-4-CONTRATO §6, PR 4.6): el dueño va a `/onboarding` desde cualquier
 * pantalla (y con el alta terminada, `/onboarding` lo devuelve al inicio); un empleado no se
 * redirige: ve un aviso arriba de cada pantalla. Se decide con `onboarding_completed` de la sesión
 * del servidor; antes de que llegue no se redirige nada.
 */
export default function ProtectedLayout({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const router = useRouter();
  const setSession = useAuthStore((s) => s.setSession);
  const mustChangePassword = useAuthStore((s) => s.mustChangePassword);
  const hasUser = useAuthStore((s) => s.user !== null);
  const role = useAuthStore((s) => s.user?.role);
  const onboardingCompleted = useAuthStore((s) => s.onboardingCompleted);
  const pathname = usePathname() ?? "";
  const inOnboarding = pathname === ONBOARDING_PATH || pathname.startsWith(`${ONBOARDING_PATH}/`);
  // Solo el dueño se redirige, y solo con el dato del servidor (`null` = todavía no se sabe).
  const ownerMustOnboard =
    !mustChangePassword && role === "OWNER" && onboardingCompleted === false && !inOnboarding;
  const ownerAlreadyDone =
    !mustChangePassword && role === "OWNER" && onboardingCompleted === true && inOnboarding;
  const staffWaiting = role === "STAFF" && onboardingCompleted === false;

  const { data: me } = useQuery({
    queryKey: ["auth", "me"],
    queryFn: getMeRequest,
    // Corto y al volver a la pestaña: si el dueño cambia el perfil de un empleado, su menú
    // se entera pronto. Es solo presentación; el servidor decide en cada request.
    staleTime: 30 * 1000,
    refetchOnWindowFocus: true,
    retry: false,
  });

  useTenantBranding(hasUser && !mustChangePassword);

  useEffect(() => {
    if (me) setSession(me);
  }, [me, setSession]);

  useEffect(() => {
    if (mustChangePassword) router.replace(CHANGE_PASSWORD_PATH);
  }, [mustChangePassword, router]);

  useEffect(() => {
    if (ownerMustOnboard) router.replace(ONBOARDING_PATH);
    else if (ownerAlreadyDone) router.replace(HOME_PATH);
  }, [ownerMustOnboard, ownerAlreadyDone, router]);

  if (mustChangePassword || ownerMustOnboard || ownerAlreadyDone) {
    return (
      <div role="status" aria-label="Cargando" className="flex h-dvh items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-muted border-t-primary" />
      </div>
    );
  }

  return (
    <div className="flex h-dvh overflow-hidden bg-background">
      <Sidebar mobileOpen={mobileOpen} onMobileOpenChange={setMobileOpen} />
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <Header onMenuToggle={() => setMobileOpen((v) => !v)} />
        <main
          className="flex-1 overflow-y-auto p-4 sm:p-6"
          style={{ paddingBottom: "max(1rem, env(safe-area-inset-bottom))" }}
        >
          <div className="mx-auto max-w-[1200px]">
            {staffWaiting && (
              <p
                role="status"
                className="mb-4 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900"
              >
                El negocio todavía se está configurando: el dueño tiene que terminar el alta. Hasta
                entonces puede faltar información en algunas pantallas.
              </p>
            )}
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
