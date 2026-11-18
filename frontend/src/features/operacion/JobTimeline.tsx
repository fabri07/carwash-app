"use client";

import { Button } from "@/components/ui/button";
import { useJobEvents } from "./queries";
import { formatStamp } from "./time";
import type { JobEvent } from "./types";

const EVENT_LABELS: Record<JobEvent["event_type"], string> = {
  JOB_RECEIVED: "Llegó",
  JOB_STARTED: "Se inició",
  JOB_FINISHED: "Se finalizó",
  JOB_SETTLED: "Quedó cobrado",
  JOB_PICKED_UP: "Se retiró",
  JOB_CANCELLED_DELAY: "Cancelado por demora",
  DEPOSIT_RETAINED: "Seña retenida",
  DEPOSIT_RETENTION_REVERSED: "Retención de seña revertida",
  PAYMENT_RECORDED: "Cobro registrado",
  PAYMENT_VOIDED: "Cobro anulado",
  INSPECTION_RECORDED: "Recepción registrada",
  PRICE_ADJUSTED: "Precio ajustado",
};

/**
 * Línea de tiempo del job (`GET /jobs/{id}/events`, paginada: ADR-0006). La hora es la del toque
 * (`occurred_at`); los eventos de una carga retrospectiva llevan la marca "carga manual".
 */
export function JobTimeline({ jobId, timeZone }: { jobId: string; timeZone: string }) {
  const events = useJobEvents(jobId);
  const items = events.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <section aria-labelledby="timeline-title" className="space-y-2">
      <h2 id="timeline-title" className="text-lg font-semibold">
        Línea de tiempo
      </h2>
      {events.isLoading && <p className="text-sm text-muted-foreground">Cargando…</p>}
      {events.isError && (
        <p role="alert" className="text-sm text-destructive">
          No se pudo cargar la línea de tiempo.
        </p>
      )}
      <ol className="space-y-1">
        {items.map((e) => (
          <li key={e.id} className="flex items-baseline gap-3 text-sm">
            <span className="w-24 shrink-0 tabular-nums text-muted-foreground">
              {formatStamp(e.occurred_at, timeZone)}
            </span>
            <span>
              {EVENT_LABELS[e.event_type]}
              {e.metadata?.manual_entry === true && (
                <span className="ml-2 text-xs text-muted-foreground">(carga manual)</span>
              )}
            </span>
          </li>
        ))}
      </ol>
      {events.hasNextPage && (
        <Button
          variant="outline"
          disabled={events.isFetchingNextPage}
          onClick={() => void events.fetchNextPage()}
        >
          Ver más
        </Button>
      )}
    </section>
  );
}
