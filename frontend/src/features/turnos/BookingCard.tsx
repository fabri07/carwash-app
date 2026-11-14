"use client";

import type { ReactNode } from "react";
import { MessageCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { formatRange, localTime } from "@/lib/agenda";
import { formatARS } from "@/lib/money";
import { STATUS_LABELS } from "@/features/turnos/queries";
import { whatsappLink } from "@/features/turnos/time";
import type { InboxBooking } from "@/types/api";

interface BookingCardProps {
  booking: InboxBooking;
  timeZone: string;
  resourceName: (id: string) => string;
  /** Botones de acción (según pestaña y permisos). */
  children?: ReactNode;
}

/**
 * Un turno en la bandeja: horario en la zona del negocio, puesto, cliente (con su WhatsApp para
 * avisarle), patente, servicios y seña. La patente es PII fuerte: se muestra, nunca se loguea.
 */
export function BookingCard({ booking, timeZone, resourceName, children }: BookingCardProps) {
  const wa = whatsappLink(booking.customer?.phone_e164);
  const services = booking.services.map((s) => s.name).join(" + ");
  return (
    <li className="space-y-2 p-3" aria-label={`Turno ${booking.code}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-0.5">
          <p className="font-medium">
            {formatRange(booking.start_at, booking.end_at, timeZone)} ·{" "}
            {resourceName(booking.resource_id)}
          </p>
          <p className="text-sm">
            {booking.customer?.name ?? "Cliente"}
            {booking.vehicle_plate ? ` · ${booking.vehicle_plate}` : ""}
            {services ? ` · ${services}` : ""}
          </p>
          <p className="text-xs text-muted-foreground">
            {booking.code}
            {booking.deposit_required_cents > 0 &&
              ` · Seña ${formatARS(booking.deposit_required_cents)}` +
                (booking.deposit_paid_cents > 0
                  ? ` (cobrado ${formatARS(booking.deposit_paid_cents)})`
                  : "")}
            {booking.status === "PENDIENTE_SEÑA" &&
              booking.hold_expires_at &&
              ` · Retenido hasta las ${localTime(booking.hold_expires_at, timeZone)}`}
          </p>
          {booking.notes && <p className="text-xs text-muted-foreground">{booking.notes}</p>}
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={booking.status === "CONFIRMADO" ? "secondary" : "outline"}>
            {STATUS_LABELS[booking.status]}
          </Badge>
          {wa && (
            <a
              href={wa}
              target="_blank"
              rel="noreferrer"
              className="inline-flex min-h-touch items-center gap-1 rounded-md px-2 text-sm font-medium text-primary underline-offset-4 hover:underline"
              aria-label={`Escribirle por WhatsApp a ${booking.customer?.name ?? "el cliente"}`}
            >
              <MessageCircle className="h-4 w-4" aria-hidden="true" />
              WhatsApp
            </a>
          )}
        </div>
      </div>
      {children && <div className="flex flex-wrap gap-2">{children}</div>}
    </li>
  );
}
