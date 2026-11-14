"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { inboxErrorMessage, turnosKeys, useRefreshInbox } from "@/features/turnos/queries";
import { formatDay } from "@/features/turnos/time";
import { formatRange } from "@/lib/agenda";
import { getBooking, removeIncidentMark } from "@/services/bookings.service";
import { toast } from "@/stores/toastStore";
import type { InboxBooking } from "@/types/api";

interface IncidentsDialogProps {
  booking: InboxBooking;
  timeZone: string;
  /** `TURNOS_GESTIONAR`: sin él se ven las marcas pero no se anulan. */
  canManage: boolean;
  onClose: () => void;
}

/**
 * Incidencias del cliente de un turno (D-014): cuántas cuentan en los últimos 90 días y las
 * marcas manuales vivas, cada una con "Desmarcar" (anula, nunca borra). Con 2 o más incidencias,
 * sus próximos pedidos del turnero van a la lista de espera.
 */
export function IncidentsDialog({ booking, timeZone, canManage, onClose }: IncidentsDialogProps) {
  const refresh = useRefreshInbox();
  const detail = useQuery({
    queryKey: [...turnosKeys.all, "detail", booking.id],
    queryFn: () => getBooking(booking.id),
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function unmark(id: string) {
    setBusy(id);
    setError(null);
    try {
      await removeIncidentMark(id);
      await refresh();
      toast.success("Incidencia desmarcada.");
    } catch (e) {
      setError(inboxErrorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  const marks = detail.data?.incident_marks ?? [];
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Incidencias de {booking.customer?.name ?? "el cliente"}</DialogTitle>
          <DialogDescription>
            Turno {booking.code} · {formatRange(booking.start_at, booking.end_at, timeZone)}
          </DialogDescription>
        </DialogHeader>
        {detail.isLoading ? (
          <p className="text-sm text-muted-foreground">Cargando…</p>
        ) : detail.isError ? (
          <p role="alert" className="text-sm font-medium text-destructive">
            No se pudieron cargar las incidencias.
          </p>
        ) : (
          <div className="space-y-3">
            <p className="text-sm">
              Incidencias en los últimos 90 días: <strong>{detail.data?.incidents ?? 0}</strong>
              {(detail.data?.incidents ?? 0) >= 2 &&
                " — sus pedidos nuevos van a la lista de espera."}
            </p>
            {marks.length === 0 ? (
              <p className="text-sm text-muted-foreground">No hay marcas manuales.</p>
            ) : (
              <ul className="divide-y rounded-lg border">
                {marks.map((m) => (
                  <li key={m.id} className="flex items-center justify-between gap-3 p-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{m.reason}</p>
                      <p className="text-xs text-muted-foreground">
                        {formatDay(m.created_at, timeZone)}
                      </p>
                    </div>
                    {canManage && (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={busy === m.id}
                        aria-label={`Desmarcar incidencia: ${m.reason}`}
                        onClick={() => void unmark(m.id)}
                      >
                        Desmarcar
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        {error && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {error}
          </p>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Cerrar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
