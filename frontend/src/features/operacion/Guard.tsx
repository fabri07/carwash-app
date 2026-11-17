"use client";

import { Lock } from "lucide-react";

import { EmptyState } from "@/components/ui/empty-state";
import { useCan, useIsOwner, usePermissionsLoaded } from "@/hooks/usePermission";
import type { PermissionCode } from "@/types/api";

/**
 * Compuerta de pantalla: oculta lo que la sesión no puede ver. Ocultar no es autorizar: el
 * servidor sigue respondiendo 403/404 y esos errores se muestran igual.
 */
export function Guard({
  permission,
  title,
  children,
}: {
  permission: PermissionCode;
  title: string;
  children: React.ReactNode;
}) {
  const can = useCan();
  const loaded = usePermissionsLoaded();
  const isOwner = useIsOwner();
  if (!isOwner && !loaded) return <p className="text-sm text-muted-foreground">Cargando…</p>;
  if (!can(permission)) {
    return (
      <section className="rounded-xl border bg-card">
        <EmptyState
          icon={<Lock className="h-6 w-6" />}
          title={`No tenés acceso a ${title}`}
          description="Si lo necesitás, pedile el permiso al dueño del negocio."
          action={{ label: "Volver al inicio", href: "/dashboard" }}
        />
      </section>
    );
  }
  return <>{children}</>;
}
