"use client";

import { useEffect, useRef, useState } from "react";

import { apiErrorCode, configError, FormSubmitError } from "@/lib/errors";
import { toast } from "@/stores/toastStore";

/**
 * Lo común a las pantallas de ABM del catálogo (configuración y vehículos): el diálogo de
 * alta/edición/baja, el guardado con `Idempotency-Key` (un replay es "ya estaba hecho", no un
 * error) y el paso atrás de página cuando la última queda vacía.
 */

export type EditorDialog<R> =
  | { kind: "create"; idempotencyKey: string }
  | { kind: "edit"; item: R }
  | { kind: "delete"; item: R }
  | null;

export function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

interface CatalogEditorOptions<R, F> {
  /** "puesto", "marca": para los mensajes. */
  singular: string;
  /** Género del sustantivo: "creada", "Esa marca". */
  feminine?: boolean;
  create: (values: F, idempotencyKey: string) => Promise<unknown>;
  update: (item: R, values: F) => Promise<unknown>;
  /** Releer la lista después de guardar. */
  refresh: () => Promise<unknown>;
}

export function useCatalogEditor<R, F>({
  singular,
  feminine = false,
  create,
  update,
  refresh,
}: CatalogEditorOptions<R, F>) {
  const [dialog, setDialog] = useState<EditorDialog<R>>(null);
  const close = () => setDialog(null);
  const openCreate = () => setDialog({ kind: "create", idempotencyKey: crypto.randomUUID() });

  /** Alta (con la clave del diálogo) o edición. Los errores van al formulario. */
  async function save(values: F, current?: R, idempotencyKey?: string) {
    let replay = false;
    try {
      if (current) await update(current, values);
      else await create(values, idempotencyKey ?? crypto.randomUUID());
    } catch (e) {
      if (current || apiErrorCode(e) !== "DUPLICATE_IDEMPOTENT") {
        throw new FormSubmitError(configError(e));
      }
      replay = true;
    }
    await refresh();
    close();
    const ending = feminine ? "a" : "o";
    if (replay) {
      toast.info(
        // "ya se había creado": con "haber" el participio no concuerda; solo cambia Ese/Esa.
        `${feminine ? "Esa" : "Ese"} ${singular} ya se había creado. Revisá en la lista cómo quedó.`,
      );
    } else {
      toast.success(`${capitalize(singular)} ${current ? "actualizad" : "cread"}${ending}.`);
    }
  }

  /** Lo que el diálogo de alta/edición le pasa al formulario como `onSubmit`. */
  function submitFor(state: EditorDialog<R>) {
    return (values: F) =>
      state?.kind === "edit"
        ? save(values, state.item)
        : save(values, undefined, state?.kind === "create" ? state.idempotencyKey : undefined);
  }

  return { dialog, setDialog, close, openCreate, save, submitFor };
}

/**
 * Quitar u ocultar el último ítem de la última página la deja vacía: vuelve a la última página
 * con datos. `goTo` puede cambiar en cada render (se lee por ref).
 */
export function useStepBackOnEmptyPage(
  page: { items: unknown[]; total: number } | undefined,
  offset: number,
  limit: number,
  goTo: (offset: number) => void,
) {
  const goToRef = useRef(goTo);
  goToRef.current = goTo;
  useEffect(() => {
    if (page && page.items.length === 0 && page.total > 0 && offset > 0) {
      goToRef.current(Math.floor((page.total - 1) / limit) * limit);
    }
  }, [page, offset, limit]);
}
