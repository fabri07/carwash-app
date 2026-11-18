"use client";

import { usePathname } from "next/navigation";
import { LogOut, Menu } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { NAV_ITEMS, activeItem } from "@/components/layout/Sidebar";
import { useSettings } from "@/features/configuracion/queries";
import { useLogout } from "@/features/auth/useLogout";
import { useAuthStore } from "@/stores/authStore";

/**
 * Barra superior. De Véktor se conserva el menú de usuario y el logout; se
 * sacan notificaciones y el acceso a configuración. Targets de 44px.
 */
export function getPageLabel(pathname: string): string {
  return activeItem(pathname, NAV_ITEMS)?.label ?? "";
}

export function getInitials(name: string): string {
  return name
    .split(/[\s@.]+/)
    .filter(Boolean)
    .map((n) => n[0] ?? "")
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

interface HeaderProps {
  onMenuToggle: () => void;
}

export function Header({ onMenuToggle }: HeaderProps) {
  const pathname = usePathname();
  const user = useAuthStore((s) => s.user);
  const tenant = useAuthStore((s) => s.tenant);
  const { logout, loggingOut } = useLogout();
  const logoUrl = useSettings().data?.logo_url;

  const displayName = user?.email ?? user?.username ?? "";
  const initials = getInitials(tenant?.name || displayName || "U");

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b-2 border-b-primary bg-card px-2 md:px-4">
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="icon"
          className="md:hidden"
          onClick={onMenuToggle}
          aria-label="Abrir menú"
        >
          <Menu />
        </Button>
        <span className="text-sm font-semibold">{getPageLabel(pathname)}</span>
      </div>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" aria-label="Menú de usuario" className="rounded-full">
            {logoUrl ? (
              // eslint-disable-next-line @next/next/no-img-element -- bucket público de R2, sin optimizar
              <img
                src={logoUrl}
                alt=""
                className="h-9 w-9 rounded-full border border-primary/30 bg-white object-contain p-0.5"
              />
            ) : (
              <span className="flex h-9 w-9 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
                {initials}
              </span>
            )}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          {displayName && (
            <>
              <DropdownMenuLabel className="font-normal">
                {tenant?.name && (
                  <span className="block truncate font-semibold">{tenant.name}</span>
                )}
                <span className="block truncate text-muted-foreground">{displayName}</span>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
            </>
          )}
          <DropdownMenuItem disabled={loggingOut} onSelect={() => void logout()}>
            <LogOut aria-hidden="true" />
            Cerrar sesión
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}
