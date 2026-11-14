"use client";

import { useState, type ComponentType } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  CalendarClock,
  ChevronLeft,
  ChevronRight,
  Droplets,
  LayoutDashboard,
  Settings,
  Tags,
  Users,
  type LucideIcon,
} from "lucide-react";

import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { InboxBadge } from "@/features/turnos/InboxBadge";
import { can } from "@/lib/permissions";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/authStore";
import type { PermissionCode, User } from "@/types/api";

/**
 * Navegación lateral. Reescrito (manifiesto de portado): de Véktor se toma la
 * estructura — colapsable en escritorio, cajón en móvil, ítem activo — y se
 * reconstruye el cajón sobre shadcn `sheet` (foco atrapado, `Escape`, overlay
 * y `aria-*` los resuelve Radix).
 *
 * Todo target táctil mide al menos 44px (`min-h-touch`); en Véktor eran 32-36.
 */
export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  /** Encabezado del grupo. Ítems consecutivos con la misma sección van juntos. */
  section?: string;
  /** Se muestra solo a quien tiene este permiso (el OWNER los tiene todos). */
  permission?: PermissionCode;
  /** Se muestra a quien tiene al menos uno de estos permisos (el OWNER los tiene todos). */
  anyPermission?: PermissionCode[];
  /** Solo el dueño (configuración del negocio y del equipo, Y5). */
  ownerOnly?: boolean;
  /** Contador al lado del ítem (p. ej. lo pendiente de la bandeja de turnos). */
  badge?: ComponentType<{ collapsed: boolean }>;
}

// Cada fase agrega acá sus pantallas, con el permiso que las habilita.
export const NAV_ITEMS: NavItem[] = [
  { label: "Inicio", href: "/dashboard", icon: LayoutDashboard },
  // Bandeja del dueño (PR 5.3): lo que espera una decisión lleva su número al lado.
  {
    label: "Turnos",
    href: "/turnos",
    icon: CalendarClock,
    permission: "AGENDA_VER",
    badge: InboxBadge,
  },
  {
    label: "Catálogo",
    href: "/catalogo",
    icon: Tags,
    anyPermission: ["CATALOGO_EDITAR", "PRECIOS_EDITAR"],
  },
  {
    label: "Negocio",
    href: "/configuracion",
    icon: Settings,
    section: "Configuración",
    // Con permisos de agenda se entra a ver "Bloqueos y aperturas" (adenda 5.1-12).
    anyPermission: ["CATALOGO_EDITAR", "AGENDA_VER", "TURNOS_GESTIONAR"],
  },
  {
    label: "Equipo",
    href: "/configuracion/equipo",
    icon: Users,
    section: "Configuración",
    ownerOnly: true,
  },
];

/**
 * Ítems que ve este usuario. Ocultar no es autorizar: el backend responde 403
 * igual si alguien escribe la URL (FASE-4-CONTRATO §2.3). Sin usuario (store
 * todavía vacío) solo quedan los ítems sin requisito.
 */
export function visibleNavItems(
  items: NavItem[],
  user: Pick<User, "role"> | null,
  permissions: readonly PermissionCode[],
): NavItem[] {
  return items.filter((item) => {
    if (item.ownerOnly && user?.role !== "OWNER") return false;
    if (item.permission && !can(user, permissions, item.permission)) return false;
    if (item.anyPermission && !item.anyPermission.some((p) => can(user, permissions, p))) {
      return false;
    }
    return true;
  });
}

export function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * El ítem que corresponde a la ruta: el de `href` más largo que coincide. Sin esto,
 * `/configuracion/equipo` marcaría también "Negocio" (`/configuracion`).
 */
export function activeItem<T extends { href: string }>(
  pathname: string,
  items: T[],
): T | undefined {
  return items.reduce<T | undefined>(
    (best, item) =>
      isActive(pathname, item.href) && (!best || item.href.length > best.href.length) ? item : best,
    undefined,
  );
}

function NavList({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  const pathname = usePathname();
  const user = useAuthStore((s) => s.user);
  const permissions = useAuthStore((s) => s.permissions);
  const items = visibleNavItems(NAV_ITEMS, user, permissions);
  const current = activeItem(pathname, items);
  return (
    <nav aria-label="Principal" className="flex flex-1 flex-col gap-1 p-2">
      {items.map(({ label, href, icon: Icon, section, badge: Badge }, i) => {
        const active = current?.href === href;
        const startsSection = section !== undefined && section !== items[i - 1]?.section;
        return (
          <div key={href} className="contents">
            {startsSection && (
              <p
                className={cn(
                  "mt-3 px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-sidebar-foreground/50",
                  collapsed && "sr-only",
                )}
              >
                {section}
              </p>
            )}
            <Link
              href={href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              title={collapsed ? label : undefined}
              className={cn(
                "relative flex min-h-touch items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                active
                  ? "bg-sidebar-accent text-sidebar-foreground"
                  : "text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground",
                collapsed && "justify-center px-0",
              )}
            >
              <Icon className="h-5 w-5 shrink-0" aria-hidden="true" />
              <span className={cn(collapsed && "sr-only")}>{label}</span>
              {Badge && <Badge collapsed={collapsed} />}
            </Link>
          </div>
        );
      })}
    </nav>
  );
}

function Brand({ collapsed }: { collapsed: boolean }) {
  return (
    <Link
      href="/dashboard"
      className={cn(
        "flex min-h-touch items-center gap-2 rounded-md px-2 font-semibold text-sidebar-foreground",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        collapsed && "justify-center",
      )}
    >
      <Droplets className="h-6 w-6 shrink-0 text-sky-400" aria-hidden="true" />
      <span className={cn(collapsed && "sr-only")}>carwash.app</span>
    </Link>
  );
}

interface SidebarProps {
  mobileOpen: boolean;
  onMobileOpenChange: (open: boolean) => void;
}

export function Sidebar({ mobileOpen, onMobileOpenChange }: SidebarProps) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <>
      {/* Escritorio / tablet */}
      <aside
        data-testid="sidebar-desktop"
        className={cn(
          "hidden h-full shrink-0 flex-col border-r border-sidebar-border bg-sidebar transition-[width] duration-200 md:flex",
          collapsed ? "w-[72px]" : "w-[248px]",
        )}
      >
        <div className="flex h-14 items-center border-b border-sidebar-border px-2">
          <Brand collapsed={collapsed} />
        </div>
        <NavList collapsed={collapsed} />
        <div className="border-t border-sidebar-border p-2">
          <button
            type="button"
            onClick={() => setCollapsed((v) => !v)}
            aria-label={collapsed ? "Expandir menú" : "Colapsar menú"}
            aria-expanded={!collapsed}
            className="flex min-h-touch w-full items-center justify-center rounded-md text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {collapsed ? <ChevronRight className="h-5 w-5" /> : <ChevronLeft className="h-5 w-5" />}
          </button>
        </div>
      </aside>

      {/* Móvil: cajón sobre shadcn sheet */}
      <Sheet open={mobileOpen} onOpenChange={onMobileOpenChange}>
        <SheetContent
          side="left"
          className="flex w-[272px] flex-col border-sidebar-border bg-sidebar p-0 text-sidebar-foreground"
        >
          <SheetTitle className="sr-only">Menú</SheetTitle>
          <SheetDescription className="sr-only">Navegación principal</SheetDescription>
          <div className="flex h-14 items-center border-b border-sidebar-border px-2 pr-14">
            <Brand collapsed={false} />
          </div>
          <NavList collapsed={false} onNavigate={() => onMobileOpenChange(false)} />
        </SheetContent>
      </Sheet>
    </>
  );
}
