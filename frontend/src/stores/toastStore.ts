import { create } from "zustand";

/**
 * Contrato de toasts portado de Véktor (ADR-0010 §5): variantes, duración y
 * cola. La presentación ya no es propia: `ToastBridge` vacía esta cola hacia
 * `sonner`. Cualquier parte de la app (incluido código no-React, como un
 * interceptor) encola acá sin saber quién dibuja.
 */
export type ToastVariant = "success" | "error" | "info" | "warning";

/** Duración por defecto en ms. Los errores quedan más tiempo: hay que leerlos. */
export const TOAST_DURATION: Record<ToastVariant, number> = {
  success: 4000,
  info: 4000,
  warning: 6000,
  error: 8000,
};

export interface ToastItem {
  id: string;
  message: string;
  variant: ToastVariant;
  duration: number;
}

interface ToastStore {
  toasts: ToastItem[];
  add: (message: string, variant: ToastVariant, duration?: number) => string;
  remove: (id: string) => void;
}

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  add: (message, variant, duration) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const item: ToastItem = { id, message, variant, duration: duration ?? TOAST_DURATION[variant] };
    set((state) => ({ toasts: [...state.toasts, item] }));
    return id;
  },
  remove: (id) => {
    set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) }));
  },
}));

/** Atajo para código fuera de React. */
export const toast = {
  success: (message: string, duration?: number) =>
    useToastStore.getState().add(message, "success", duration),
  error: (message: string, duration?: number) =>
    useToastStore.getState().add(message, "error", duration),
  info: (message: string, duration?: number) =>
    useToastStore.getState().add(message, "info", duration),
  warning: (message: string, duration?: number) =>
    useToastStore.getState().add(message, "warning", duration),
};
