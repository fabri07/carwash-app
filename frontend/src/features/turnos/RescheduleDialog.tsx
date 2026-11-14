"use client";

import { useId, useState } from "react";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { formatRange, localDate, localTime } from "@/lib/agenda";
import { getAvailability } from "@/services/agenda.service";
import { inboxErrorMessage } from "@/features/turnos/queries";
import { zonedToUtcIso } from "@/features/turnos/time";
import type { InboxBooking, ReschedulePayload, Resource } from "@/types/api";

interface RescheduleDialogProps {
  booking: InboxBooking;
  timeZone: string;
  resources: Resource[];
  onClose: () => void;
  /** Rechaza con un `Error` cuyo mensaje se muestra en el diálogo. */
  onSubmit: (payload: ReschedulePayload) => Promise<void>;
}

/**
 * Reprogramar y/o cambiar de puesto (D5-10). Los horarios libres salen de la disponibilidad
 * interna (sin horizonte ni anticipación); el dueño también puede escribir otra hora, aunque quede
 * fuera del horario o de las reglas del servicio: se guarda y se avisa. Un bloqueo o un puesto
 * ocupado no entran (lo decide el servidor).
 */
export function RescheduleDialog({
  booking,
  timeZone,
  resources,
  onClose,
  onSubmit,
}: RescheduleDialogProps) {
  const ids = { date: useId(), resource: useId(), time: useId() };
  const [date, setDate] = useState(() => localDate(new Date(booking.start_at), timeZone));
  const [resourceId, setResourceId] = useState(booking.resource_id);
  const [time, setTime] = useState(() => localTime(booking.start_at, timeZone));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const availability = useQuery({
    queryKey: ["agenda", "availability", booking.id, date],
    queryFn: () =>
      getAvailability({
        date,
        vehicle_size_id: booking.vehicle_size_id,
        service_ids: booking.services.map((s) => s.service_id),
      }),
    enabled: Boolean(date),
  });
  const slots = (availability.data?.slots ?? []).filter((s) => s.resource_ids.includes(resourceId));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!date || !time) {
      setError("Elegí el día y la hora.");
      return;
    }
    const startAt = zonedToUtcIso(date, time, timeZone);
    const payload: ReschedulePayload = {};
    if (new Date(startAt).getTime() !== new Date(booking.start_at).getTime()) {
      payload.start_at = startAt;
    }
    if (resourceId !== booking.resource_id) payload.resource_id = resourceId;
    if (!payload.start_at && !payload.resource_id) {
      setError("Cambiá el horario o el puesto.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSubmit(payload);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : inboxErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent>
        <form onSubmit={(e) => void submit(e)} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Reprogramar {booking.code}</DialogTitle>
            <DialogDescription>
              Ahora: {formatRange(booking.start_at, booking.end_at, timeZone)}. Avisale al cliente
              el cambio.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor={ids.date}>Día</Label>
              <Input
                id={ids.date}
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor={ids.resource}>Puesto</Label>
              <NativeSelect
                id={ids.resource}
                value={resourceId}
                onChange={(e) => setResourceId(e.target.value)}
              >
                {resources.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </NativeSelect>
            </div>
          </div>
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Horarios libres en ese puesto</legend>
            {availability.isLoading ? (
              <p className="text-sm text-muted-foreground">Buscando horarios…</p>
            ) : slots.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No hay horarios libres ese día en ese puesto. Podés escribir otra hora abajo.
              </p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {slots.map((s) => {
                  const hhmm = localTime(s.start_at, timeZone);
                  return (
                    <Button
                      key={s.start_at}
                      type="button"
                      size="sm"
                      variant={hhmm === time ? "default" : "outline"}
                      aria-pressed={hhmm === time}
                      onClick={() => setTime(hhmm)}
                    >
                      {hhmm}
                    </Button>
                  );
                })}
              </div>
            )}
          </fieldset>
          <div className="space-y-2">
            <Label htmlFor={ids.time}>Hora</Label>
            <Input
              id={ids.time}
              type="time"
              value={time}
              onChange={(e) => setTime(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Podés salirte del horario o de las reglas del servicio: se guarda y te avisamos. Un
              bloqueo no se puede pisar.
            </p>
          </div>
          {error && (
            <p role="alert" className="text-sm font-medium text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
              Volver
            </Button>
            <Button type="submit" disabled={busy}>
              Guardar cambio
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
