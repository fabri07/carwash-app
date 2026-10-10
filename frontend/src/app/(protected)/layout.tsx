"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { CHANGE_PASSWORD_PATH } from "@/lib/routes";
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
 */
export default function ProtectedLayout({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const router = useRouter();
  const setSession = useAuthStore((s) => s.setSession);
  const mustChangePassword = useAuthStore((s) => s.mustChangePassword);

  const { data: me } = useQuery({
    queryKey: ["auth", "me"],
    queryFn: getMeRequest,
    // Corto y al volver a la pestaña: si el dueño cambia el perfil de un empleado, su menú
    // se entera pronto. Es solo presentación; el servidor decide en cada request.
    staleTime: 30 * 1000,
    refetchOnWindowFocus: true,
    retry: false,
  });

  useEffect(() => {
    if (me) setSession(me);
  }, [me, setSession]);

  useEffect(() => {
    if (mustChangePassword) router.replace(CHANGE_PASSWORD_PATH);
  }, [mustChangePassword, router]);

  if (mustChangePassword) {
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
          <div className="mx-auto max-w-[1200px]">{children}</div>
        </main>
      </div>
    </div>
  );
}
