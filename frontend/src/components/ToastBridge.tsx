"use client";

import { useEffect } from "react";
import { toast as sonner } from "sonner";

import { useToastStore } from "@/stores/toastStore";

/**
 * Vacía la cola de `toastStore` hacia `sonner`. El contrato (variantes,
 * duración, cola) es del store; la presentación es de sonner (ADR-0010 §5).
 */
export function ToastBridge() {
  const toasts = useToastStore((s) => s.toasts);
  const remove = useToastStore((s) => s.remove);

  useEffect(() => {
    for (const t of toasts) {
      sonner[t.variant](t.message, { id: t.id, duration: t.duration });
      remove(t.id);
    }
  }, [toasts, remove]);

  return null;
}
