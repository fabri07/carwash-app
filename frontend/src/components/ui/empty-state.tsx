import type { ReactNode } from "react";
import Link from "next/link";
import { Inbox } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Estado vacío, portado de Véktor (`components/ui/EmptyState.tsx`). shadcn no
 * tiene equivalente. Cambios: tokens de shadcn en vez de `vk-*`, la acción usa
 * `Button` (44px) y el ícono por defecto sale de lucide.
 */
interface EmptyStateAction {
  label: string;
  href?: string;
  onClick?: () => void;
}

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: EmptyStateAction;
  variant?: "default" | "compact";
  className?: string;
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  variant = "default",
  className,
}: EmptyStateProps) {
  const isCompact = variant === "compact";

  return (
    <div
      className={cn(
        "flex flex-col items-center text-center",
        isCompact ? "p-6" : "p-10",
        className,
      )}
    >
      <div
        aria-hidden="true"
        className={cn(
          "mb-4 flex flex-shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground",
          isCompact ? "h-10 w-10" : "h-14 w-14",
        )}
      >
        {icon ?? <Inbox className="h-6 w-6" />}
      </div>

      <h2 className={cn("font-semibold text-foreground", isCompact ? "text-sm" : "text-base")}>
        {title}
      </h2>

      {description && (
        <p
          className={cn(
            "max-w-sm text-muted-foreground",
            isCompact ? "mt-1 text-xs" : "mt-2 text-sm",
          )}
        >
          {description}
        </p>
      )}

      {action && (
        <div className="mt-4">
          {action.href ? (
            <Button asChild>
              <Link href={action.href}>{action.label}</Link>
            </Button>
          ) : (
            <Button type="button" onClick={action.onClick}>
              {action.label}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
