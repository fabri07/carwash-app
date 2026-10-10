import * as React from "react";
import { ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * `<select>` nativo con el aspecto de `Input`. Para formularios en el celular
 * es mejor que el `Select` de Radix: abre el selector del sistema (rueda en
 * iOS, lista en Android), no depende de portales ni de eventos de puntero, y se
 * prueba en jsdom sin colgar el worker (el `Select` de Radix lo cuelga, igual
 * que el `DropdownMenu`; ver `shell.test.tsx`). `Select` queda para listas con
 * contenido rico que el nativo no puede dibujar.
 */
const NativeSelect = React.forwardRef<HTMLSelectElement, React.ComponentProps<"select">>(
  ({ className, children, ...props }, ref) => (
    <div className="relative">
      <select
        ref={ref}
        className={cn(
          "flex h-11 min-h-touch w-full appearance-none rounded-md border border-input bg-background px-3 py-2 pr-9 text-base shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 aria-[invalid=true]:border-destructive",
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        aria-hidden="true"
        className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 opacity-50"
      />
    </div>
  ),
);
NativeSelect.displayName = "NativeSelect";

export { NativeSelect };
