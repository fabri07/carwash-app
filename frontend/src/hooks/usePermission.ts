"use client";

import { can } from "@/lib/permissions";
import { useAuthStore } from "@/stores/authStore";
import type { PermissionCode } from "@/types/api";

/**
 * Permisos del usuario en sesión, para ocultar menús y botones. La UI oculta,
 * el servidor decide: un 403 sigue siendo posible y se maneja como error.
 */
export function usePermission(permission: PermissionCode): boolean {
  const user = useAuthStore((s) => s.user);
  const permissions = useAuthStore((s) => s.permissions);
  return can(user, permissions, permission);
}

/** `can(p)` para cuando un componente mira varios permisos. */
export function useCan(): (permission: PermissionCode) => boolean {
  const user = useAuthStore((s) => s.user);
  const permissions = useAuthStore((s) => s.permissions);
  return (permission) => can(user, permissions, permission);
}

/** El dueño: único que configura el negocio y el equipo (Y5). */
export function useIsOwner(): boolean {
  return useAuthStore((s) => s.user?.role === "OWNER");
}
