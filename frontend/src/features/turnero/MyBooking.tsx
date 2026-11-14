"use client";

import { useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { cancelBooking, lookupBooking } from "@/services/public.service";
import type { TurneroBooking, TurneroCancellation } from "@/types/api";

import { BookingLookupForm } from "./BookingLookupForm";
import { BookingSummary } from "./BookingSummary";
import { CancelBookingForm } from "./CancelBookingForm";
import { turneroErrorMessage } from "./format";
import { useTurneroBusiness } from "./queries";
import type { CancelValues, LookupValues } from "./schema";

const CLASSIFICATION_TEXT: Record<string, string> = {
  NORMAL: "Cancelaste con tiempo.",
  TARDIA: "Cancelaste con poca anticipación: el negocio puede retener la seña según su política.",
  POSTERIOR_AL_TURNO:
    "El turno ya había empezado: el negocio puede retener la seña según su política.",
};

const DEPOSIT_TEXT: Record<string, string> = {
  DEVOLUCION_PENDIENTE: "El negocio te va a devolver la seña.",
  EN_REVISION: "El negocio va a revisar qué pasa con la seña y te va a avisar.",
};

/**
 * "Mi turno" (D5-8): con el código y el WhatsApp de la reserva se ve el turno propio y se puede
 * cancelar. Un código o un WhatsApp que no coinciden dan el mismo "no encontramos" (§6).
 */
export function MyBooking({ slug }: { slug: string }) {
  const business = useTurneroBusiness(slug);
  const [credentials, setCredentials] = useState<LookupValues | null>(null);
  const [booking, setBooking] = useState<TurneroBooking | null>(null);
  const [cancellation, setCancellation] = useState<TurneroCancellation | null>(null);
  const [confirming, setConfirming] = useState(false);

  if (!business.data) return null; // el marco muestra cargando o "no encontrado"

  async function lookup(values: LookupValues) {
    try {
      setBooking(await lookupBooking(slug, values));
      setCredentials(values);
      setCancellation(null);
    } catch (e) {
      throw new Error(turneroErrorMessage(e));
    }
  }

  async function cancel(values: CancelValues) {
    if (!credentials) return;
    try {
      const result = await cancelBooking(slug, {
        ...credentials,
        reason: values.reason || null,
      });
      setCancellation(result);
      setBooking(await lookupBooking(slug, credentials));
      setConfirming(false);
    } catch (e) {
      throw new Error(turneroErrorMessage(e));
    }
  }

  return (
    <section aria-labelledby="mi-turno" className="space-y-5">
      <h2 id="mi-turno" className="text-lg font-semibold">
        Mi turno
      </h2>

      {!booking && <BookingLookupForm onSubmit={lookup} />}

      {booking && (
        <div className="space-y-4">
          <BookingSummary booking={booking} />

          {cancellation && (
            <div role="status" className="space-y-1 rounded-md bg-muted p-3 text-sm">
              <p className="font-medium">Turno cancelado.</p>
              <p>{CLASSIFICATION_TEXT[cancellation.classification]}</p>
              {DEPOSIT_TEXT[cancellation.deposit_status] && (
                <p>{DEPOSIT_TEXT[cancellation.deposit_status]}</p>
              )}
            </div>
          )}

          {booking.can_cancel && !confirming && (
            <Button
              type="button"
              variant="outline"
              className="w-full"
              onClick={() => setConfirming(true)}
            >
              Quiero cancelar
            </Button>
          )}
          {booking.can_cancel && confirming && <CancelBookingForm onSubmit={cancel} />}

          <Button
            type="button"
            variant="ghost"
            className="w-full"
            onClick={() => {
              setBooking(null);
              setCredentials(null);
              setCancellation(null);
              setConfirming(false);
            }}
          >
            Buscar otro turno
          </Button>
        </div>
      )}

      <p className="text-center text-sm">
        <Link href={`/${slug}`} className="font-medium text-primary underline">
          Reservar un turno
        </Link>
      </p>
    </section>
  );
}
