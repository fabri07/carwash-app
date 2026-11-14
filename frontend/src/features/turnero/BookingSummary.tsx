import { formatARS } from "@/lib/money";
import type { TurneroBooking } from "@/types/api";

import { statusLabel, whenLabel } from "./format";

/** El turno propio: lo que muestran la confirmación y "Mi turno". Nunca datos de otro. */
export function BookingSummary({ booking }: { booking: TurneroBooking }) {
  return (
    <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-2 text-sm">
      <dt className="text-muted-foreground">Código</dt>
      <dd className="font-mono text-base font-semibold tracking-wider">{booking.code}</dd>
      <dt className="text-muted-foreground">Estado</dt>
      <dd>{statusLabel(booking.status)}</dd>
      <dt className="text-muted-foreground">Cuándo</dt>
      <dd>{whenLabel(booking.start_at, booking.timezone)}</dd>
      <dt className="text-muted-foreground">Vehículo</dt>
      <dd>{booking.vehicle_size_label}</dd>
      <dt className="text-muted-foreground">Servicios</dt>
      <dd>{booking.services.map((s) => s.name).join(" + ")}</dd>
      <dt className="text-muted-foreground">Total</dt>
      <dd>{formatARS(booking.total_cents)}</dd>
      {booking.deposit_cents > 0 && (
        <>
          <dt className="text-muted-foreground">Seña</dt>
          <dd className="font-medium">{formatARS(booking.deposit_cents)}</dd>
        </>
      )}
    </dl>
  );
}
