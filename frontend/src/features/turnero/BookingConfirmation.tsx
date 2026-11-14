import Link from "next/link";
import { CheckCircle2, Clock3, MessageCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { TurneroBooking, TurneroBusiness } from "@/types/api";

import { BookingSummary } from "./BookingSummary";
import { depositMessage, whatsappLink, whenLabel } from "./format";

const NO_INSTRUCTIONS = "Coordiná el pago de la seña por WhatsApp.";

/**
 * Confirmación (D5-1): código, seña con las instrucciones del negocio (o "coordiná por
 * WhatsApp") y un botón a WhatsApp con el texto precargado para mandar el comprobante.
 */
export function BookingConfirmation({
  slug,
  business,
  booking,
}: {
  slug: string;
  business: TurneroBusiness;
  booking: TurneroBooking;
}) {
  const link = whatsappLink(business.whatsapp_e164, depositMessage(booking));
  const pendingDeposit = booking.status === "PENDIENTE_SEÑA";
  const inReview = booking.status === "PENDIENTE_REVISION";

  return (
    <section aria-labelledby="confirmacion" className="space-y-5">
      <div className="flex items-center gap-2">
        <CheckCircle2 className="h-7 w-7 text-primary" aria-hidden="true" />
        <h2 id="confirmacion" className="text-lg font-semibold">
          {inReview
            ? "Recibimos tu pedido"
            : pendingDeposit
              ? "Tu turno está reservado"
              : "¡Turno confirmado!"}
        </h2>
      </div>

      {inReview && (
        <p className="text-sm text-muted-foreground">
          El negocio va a revisar tu pedido y te va a confirmar por WhatsApp. Por ahora no tenés que
          pagar nada.
        </p>
      )}

      <BookingSummary booking={booking} />

      {pendingDeposit && (
        <div className="space-y-2 rounded-md border border-primary/40 bg-primary/5 p-4 text-sm">
          <p className="flex items-center gap-2 font-medium">
            <Clock3 className="h-4 w-4" aria-hidden="true" />
            {booking.hold_expires_at
              ? `Te guardamos el horario hasta el ${whenLabel(booking.hold_expires_at, booking.timezone)}`
              : "Te guardamos el horario por un rato"}
          </p>
          <p>Para confirmarlo, pagá la seña y mandá el comprobante.</p>
          <p className="whitespace-pre-line rounded bg-background p-2 font-mono text-xs">
            {business.deposit_instructions ?? NO_INSTRUCTIONS}
          </p>
        </div>
      )}

      {link && (
        <Button asChild className="w-full">
          <a href={link} target="_blank" rel="noopener noreferrer">
            <MessageCircle className="mr-2 h-4 w-4" aria-hidden="true" />
            {pendingDeposit ? "Enviar comprobante por WhatsApp" : "Escribir por WhatsApp"}
          </a>
        </Button>
      )}

      <p className="text-center text-sm text-muted-foreground">
        Guardá el código: con él y tu WhatsApp podés ver o cancelar el turno en{" "}
        <Link href={`/${slug}/mi-turno`} className="font-medium text-primary underline">
          Mi turno
        </Link>
        .
      </p>
    </section>
  );
}
