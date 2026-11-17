"use client";

import { Button } from "@/components/ui/button";
import { useOfflineQueueStatus } from "@/lib/useOfflineQueueStatus";
import { useOfflineQueueStore, type FailedItem } from "@/stores/offlineQueueStore";
import { KINDS, useOperacionSubmit } from "./useOperacionSubmit";

const ACTION_LABELS: Record<string, string> = {
  [KINDS.receive]: "la llegada",
  [KINDS.start]: "el inicio",
  [KINDS.finish]: "la finalización",
  [KINDS.pickUp]: "el retiro",
  [KINDS.cancelDelay]: "la cancelación por demora",
  [KINDS.pay]: "el cobro",
  [KINDS.void]: "la anulación del cobro",
  [KINDS.inspection]: "la recepción",
  [KINDS.retrospective]: "la carga del trabajo",
  [KINDS.manualBooking]: "la carga del turno",
};

const label = (i: FailedItem) => ACTION_LABELS[i.kind] ?? "una acción";
const hora = (i: FailedItem) =>
  new Date(i.occurredAt ?? i.createdAt).toLocaleTimeString("es-AR", {
    hour: "2-digit",
    minute: "2-digit",
  });

/**
 * Estado de la cola offline para la agenda y la pantalla del job: cuántos toques esperan señal, y
 * los que NO llegaron (conflictos por estado y rechazos definitivos). Un conflicto o un fallido
 * nunca se pierde en silencio: queda acá hasta que alguien lo descarta o lo reintenta. No muestra
 * datos del cliente, solo qué acción y a qué hora.
 */
export function QueueBanner() {
  const { pending, failed, conflicts, online } = useOfflineQueueStatus();
  const { flush } = useOperacionSubmit();
  const store = useOfflineQueueStore.getState;
  if (pending === 0 && failed.length === 0 && conflicts.length === 0) return null;

  return (
    <div className="space-y-2" data-testid="queue-banner">
      {pending > 0 && (
        <p role="status" className="rounded-md border border-sky-300 p-3 text-sm">
          {online
            ? `Sincronizando ${pending} ${pending === 1 ? "toque guardado" : "toques guardados"}…`
            : `Sin conexión: ${pending} ${pending === 1 ? "toque guardado" : "toques guardados"} en el teléfono, se envían solos al volver la señal.`}
        </p>
      )}
      {conflicts.map((c) => (
        <div
          key={c.id}
          role="alert"
          className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-400 bg-amber-50 p-3 text-sm"
        >
          <span>
            No se aplicó {label(c)} de las {hora(c)}: el trabajo ya había cambiado de estado (otro
            dispositivo se adelantó). Revisá cómo quedó.
          </span>
          <Button variant="outline" onClick={() => store().dismissConflict(c.id)}>
            Entendido
          </Button>
        </div>
      ))}
      {failed.map((f) => (
        <div
          key={f.id}
          role="alert"
          className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-destructive p-3 text-sm"
        >
          <span>
            No se pudo enviar {label(f)} de las {hora(f)}: el servidor la rechazó. Podés
            reintentarla o descartarla.
          </span>
          <span className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => {
                store().retryFailed(f.id);
                void flush();
              }}
            >
              Reintentar
            </Button>
            <Button variant="outline" onClick={() => store().dismissFailed(f.id)}>
              Descartar
            </Button>
          </span>
        </div>
      ))}
    </div>
  );
}
