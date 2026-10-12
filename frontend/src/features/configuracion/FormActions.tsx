"use client";

import { Button } from "@/components/ui/button";

interface FormActionsProps {
  formError: string | null;
  isSubmitting: boolean;
  submitLabel: string;
  /** Sin `onCancel` no hay botón de cancelar (p. ej. una sección de "Mi negocio"). */
  onCancel?: () => void;
}

/**
 * Pie común de los formularios de configuración: el error sin campo arriba del
 * botón (ADR-0010) y los botones. Lo comparten las pantallas de configuración y
 * el wizard de alta (PR 4.6).
 */
export function FormActions({ formError, isSubmitting, submitLabel, onCancel }: FormActionsProps) {
  return (
    <>
      {formError && (
        <p role="alert" className="text-sm font-medium text-destructive">
          {formError}
        </p>
      )}
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        {onCancel && (
          <Button type="button" variant="outline" onClick={onCancel}>
            Cancelar
          </Button>
        )}
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Guardando…" : submitLabel}
        </Button>
      </div>
    </>
  );
}
