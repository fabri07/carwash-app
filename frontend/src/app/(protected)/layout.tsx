"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
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
 */
export default function ProtectedLayout({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const setSession = useAuthStore((s) => s.setSession);

  const { data: me } = useQuery({
    queryKey: ["auth", "me"],
    queryFn: getMeRequest,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  useEffect(() => {
    if (me) setSession(me);
  }, [me, setSession]);

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
