"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/**
 * Confirmación propia, en vez de `window.confirm`: se ve como la app, se
 * prueba en jsdom y no la bloquea un navegador que suprime los diálogos
 * nativos (las PWA instaladas en iOS los muestran mal).
 *
 * `onConfirm` puede ser async: mientras corre, los botones se deshabilitan. Si
 * rechaza con un `Error`, su mensaje queda en el diálogo (p. ej. "hay empleados
 * con este perfil") y el diálogo no se cierra.
 */
interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: React.ReactNode;
  confirmLabel: string;
  /** `destructive` para acciones que borran o desactivan. */
  variant?: "default" | "destructive";
  onConfirm: () => Promise<void> | void;
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  variant = "default",
  onConfirm,
}: ConfirmDialogProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleOpenChange(next: boolean) {
    if (busy) return;
    if (!next) setError(null);
    onOpenChange(next);
  }

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ocurrió un error inesperado.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {error && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {error}
          </p>
        )}
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => handleOpenChange(false)}
          >
            Cancelar
          </Button>
          <Button type="button" variant={variant} disabled={busy} onClick={() => void confirm()}>
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
