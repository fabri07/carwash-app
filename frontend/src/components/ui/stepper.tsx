import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Indicador de pasos, sin dependencias (lo usa el wizard de alta, PR 4.6).
 * Solo muestra en qué paso se está: la navegación entre pasos es del wizard,
 * que decide si se puede volver o saltar. Es una lista ordenada con
 * `aria-current="step"` en el paso activo, para que el lector de pantalla diga
 * "paso 3 de 8".
 */
export interface StepperStep {
  id: string;
  label: string;
}

interface StepperProps {
  steps: StepperStep[];
  /** Índice (desde 0) del paso actual. Los anteriores se marcan como hechos. */
  current: number;
  className?: string;
}

export function Stepper({ steps, current, className }: StepperProps) {
  return (
    <ol aria-label="Pasos" className={cn("flex flex-wrap items-center gap-2", className)}>
      {steps.map((step, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <li
            key={step.id}
            aria-current={active ? "step" : undefined}
            className="flex items-center gap-2 text-sm"
          >
            <span
              aria-hidden="true"
              className={cn(
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                done && "border-primary bg-primary text-primary-foreground",
                active && "border-primary text-primary",
                !done && !active && "text-muted-foreground",
              )}
            >
              {done ? <Check className="h-4 w-4" /> : i + 1}
            </span>
            <span className={cn(active ? "font-medium" : "text-muted-foreground")}>
              {step.label}
              {done && <span className="sr-only"> (hecho)</span>}
            </span>
            {i < steps.length - 1 && (
              <span aria-hidden="true" className="mx-1 hidden h-px w-6 bg-border sm:block" />
            )}
          </li>
        );
      })}
    </ol>
  );
}
