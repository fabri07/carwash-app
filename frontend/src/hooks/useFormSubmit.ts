"use client";

import { useState } from "react";
import type { FieldValues, Path, UseFormReturn } from "react-hook-form";

import { FormSubmitError } from "@/lib/errors";

/**
 * Envuelve el `onSubmit` de un formulario (ADR-0010): si rechaza con un
 * `FormSubmitError` cuyo campo existe en el formulario, el mensaje va debajo
 * de ese campo; cualquier otro error queda en `formError`, para mostrar arriba
 * del botón. `isSubmitting` lo sigue llevando react-hook-form.
 */
export function useFormSubmit<T extends FieldValues>(
  form: UseFormReturn<T>,
  onSubmit: (values: T) => Promise<void>,
) {
  const [formError, setFormError] = useState<string | null>(null);

  async function submit(values: T) {
    setFormError(null);
    try {
      await onSubmit(values);
    } catch (e) {
      if (e instanceof FormSubmitError && e.field && e.field in form.getValues()) {
        form.setError(e.field as Path<T>, { type: "server", message: e.message });
        return;
      }
      setFormError(e instanceof Error ? e.message : "Ocurrió un error inesperado.");
    }
  }

  return { onSubmit: form.handleSubmit(submit), formError };
}
