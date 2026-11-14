"use client";

import { useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, MessageCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Stepper } from "@/components/ui/stepper";
import { apiErrorCode, FormSubmitError } from "@/lib/errors";
import { formatARS } from "@/lib/money";
import { formatDuration } from "@/lib/time";
import { localTime } from "@/lib/agenda";
import { cn } from "@/lib/utils";
import { createBooking } from "@/services/public.service";
import type { TurneroBooking, TurneroBusiness, TurneroCatalog } from "@/types/api";

import { BookingConfirmation } from "./BookingConfirmation";
import { BookingDetailsForm } from "./BookingDetailsForm";
import {
  bookableDays,
  dayLabel,
  priceFor,
  priceLabel,
  quoteMessage,
  turneroErrorMessage,
  whatsappLink,
} from "./format";
import { useTurneroAvailability, useTurneroBusiness, useTurneroCatalog } from "./queries";
import type { BookingDetailsValues } from "./schema";

const EMPTY_DETAILS: BookingDetailsValues = { name: "", phone: "", plate: "", acceptPolicy: false };

/**
 * Reservar desde el celular (FASE-5-CONTRATO §4.2): tamaño → servicios → día → horario → datos →
 * confirmación. El cliente elige solo el tamaño (D5-3); los servicios a cotizar se muestran con
 * "desde $X" y "Consultar por WhatsApp" (D5-5); las horas, en la zona del negocio (Z9).
 */

const STEPS = [
  { id: "size", label: "Vehículo" },
  { id: "services", label: "Servicios" },
  { id: "day", label: "Día" },
  { id: "time", label: "Horario" },
  { id: "details", label: "Tus datos" },
] as const;

type Step = (typeof STEPS)[number]["id"] | "done";

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `web-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function Choice({
  selected,
  onClick,
  children,
}: {
  selected?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        "flex min-h-touch w-full items-center justify-between rounded-md border px-4 py-3 text-left text-sm transition-colors",
        selected ? "border-primary bg-primary/10 font-medium" : "hover:bg-muted",
      )}
    >
      {children}
    </button>
  );
}

export function BookingFlow({ slug }: { slug: string }) {
  const business = useTurneroBusiness(slug);
  const catalog = useTurneroCatalog(slug);
  if (!business.data) return null; // el marco muestra cargando o "no encontrado"
  if (catalog.isPending) {
    return (
      <div role="status" aria-label="Cargando servicios" className="flex justify-center py-10">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-muted border-t-primary" />
      </div>
    );
  }
  if (!catalog.data) {
    return <p className="text-sm text-destructive">{turneroErrorMessage(catalog.error)}</p>;
  }
  return <Flow slug={slug} business={business.data} catalog={catalog.data} />;
}

function Flow({
  slug,
  business,
  catalog,
}: {
  slug: string;
  business: TurneroBusiness;
  catalog: TurneroCatalog;
}) {
  const queryClient = useQueryClient();
  const [step, setStep] = useState<Step>("size");
  const [sizeId, setSizeId] = useState<string | null>(null);
  const [serviceIds, setServiceIds] = useState<string[]>([]);
  const [day, setDay] = useState<string | null>(null);
  const [startAt, setStartAt] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [booking, setBooking] = useState<TurneroBooking | null>(null);
  // Los datos del cliente viven acá: el formulario se desmonta al volver o al ocuparse el horario.
  const [details, setDetails] = useState<BookingDetailsValues>(EMPTY_DETAILS);
  // Una clave por intento: un doble toque reenvía la misma; cambiar la elección arma otra.
  const key = useRef<string>(newKey());

  const tz = business.timezone;
  const size = catalog.vehicle_sizes.find((s) => s.id === sizeId);
  const offered = useMemo(
    () => catalog.services.filter((s) => sizeId !== null && priceFor(s, sizeId)),
    [catalog.services, sizeId],
  );
  const chosen = offered.filter((s) => serviceIds.includes(s.id));
  const totals = chosen.reduce(
    (acc, s) => {
      const p = priceFor(s, sizeId!)!;
      return { price: acc.price + p.price_cents, duration: acc.duration + p.duration_min };
    },
    { price: 0, duration: 0 },
  );
  const days = useMemo(
    () => bookableDays(new Date(), tz, business.booking_horizon_days),
    [tz, business.booking_horizon_days],
  );
  const availabilityParams =
    step === "time" && day && sizeId ? { date: day, vehicleSizeId: sizeId, serviceIds } : null;
  const availability = useTurneroAvailability(slug, availabilityParams);

  function go(next: Step) {
    setNotice(null);
    key.current = newKey();
    setStep(next);
  }

  function toggleService(id: string) {
    setServiceIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));
  }

  async function submit(values: BookingDetailsValues) {
    try {
      const created = await createBooking(
        slug,
        {
          vehicle_size_id: sizeId!,
          service_ids: serviceIds,
          start_at: startAt!,
          name: values.name,
          phone: values.phone,
          plate: values.plate || null,
          accept_policy: values.acceptPolicy,
        },
        key.current,
      );
      setBooking(created);
      setStep("done");
    } catch (e) {
      const code = apiErrorCode(e);
      if (code === "SLOT_TAKEN") {
        // El horario se ocupó mientras completaba los datos: se vuelve a elegir con la lista
        // fresca.
        await queryClient.invalidateQueries({ queryKey: ["turnero", slug, "availability"] });
        go("time");
        setNotice(turneroErrorMessage(e));
        return;
      }
      if (code === "INVALID_PHONE") {
        throw new FormSubmitError({ field: "phone", message: turneroErrorMessage(e) });
      }
      throw new FormSubmitError({ field: null, message: turneroErrorMessage(e) });
    }
  }

  if (step === "done" && booking) {
    return <BookingConfirmation slug={slug} business={business} booking={booking} />;
  }

  const current = STEPS.findIndex((s) => s.id === step);
  const back: Record<string, Step> = {
    services: "size",
    day: "services",
    time: "day",
    details: "time",
  };

  return (
    <div className="space-y-5">
      {business.welcome_text && step === "size" && (
        <p className="text-sm text-muted-foreground">{business.welcome_text}</p>
      )}
      <Stepper steps={STEPS.map((s) => ({ id: s.id, label: s.label }))} current={current} />
      {back[step] && (
        <Button type="button" variant="ghost" className="-ml-2" onClick={() => go(back[step]!)}>
          <ChevronLeft className="mr-1 h-4 w-4" aria-hidden="true" />
          Volver
        </Button>
      )}
      {notice && (
        <p role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {notice}
        </p>
      )}

      {step === "size" && (
        <section aria-labelledby="paso-tamano" className="space-y-3">
          <h2 id="paso-tamano" className="text-base font-semibold">
            ¿Qué vehículo traés?
          </h2>
          {catalog.vehicle_sizes.length === 0 && (
            <p className="text-sm text-muted-foreground">
              Este negocio todavía no tiene servicios para reservar online.
            </p>
          )}
          {catalog.vehicle_sizes.map((s) => (
            <Choice
              key={s.id}
              selected={s.id === sizeId}
              onClick={() => {
                if (s.id !== sizeId) setServiceIds([]);
                setSizeId(s.id);
                go("services");
              }}
            >
              {s.label}
            </Choice>
          ))}
        </section>
      )}

      {step === "services" && size && (
        <section aria-labelledby="paso-servicios" className="space-y-3">
          <h2 id="paso-servicios" className="text-base font-semibold">
            Servicios para {size.label}
          </h2>
          {offered.length === 0 && (
            <p className="text-sm text-muted-foreground">No hay servicios para este vehículo.</p>
          )}
          <ul className="space-y-2">
            {offered.map((s) => {
              const price = priceFor(s, size.id)!;
              if (!s.bookable) {
                const link = whatsappLink(business.whatsapp_e164, quoteMessage(s.name, size.label));
                return (
                  <li key={s.id} className="rounded-md border p-3 text-sm">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-medium">{s.name}</span>
                      <span className="text-muted-foreground">{priceLabel(s, size.id)}</span>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Se cotiza según el estado del vehículo: no se reserva online.
                    </p>
                    {link && (
                      <Button asChild variant="outline" className="mt-2 w-full">
                        <a href={link} target="_blank" rel="noopener noreferrer">
                          <MessageCircle className="mr-2 h-4 w-4" aria-hidden="true" />
                          Consultar por WhatsApp
                        </a>
                      </Button>
                    )}
                  </li>
                );
              }
              const checked = serviceIds.includes(s.id);
              return (
                <li key={s.id}>
                  <label className="flex min-h-touch cursor-pointer items-center gap-3 rounded-md border p-3 text-sm">
                    <Checkbox checked={checked} onCheckedChange={() => toggleService(s.id)} />
                    <span className="flex-1">
                      <span className="block font-medium">{s.name}</span>
                      <span className="block text-xs text-muted-foreground">
                        {formatDuration(price.duration_min)}
                        {s.category_name ? ` · ${s.category_name}` : ""}
                      </span>
                    </span>
                    <span>{priceLabel(s, size.id)}</span>
                  </label>
                </li>
              );
            })}
          </ul>
          {chosen.length > 0 && (
            <p className="text-sm">
              Total {formatARS(totals.price)} · {formatDuration(totals.duration)}
            </p>
          )}
          <Button
            type="button"
            className="w-full"
            disabled={chosen.length === 0}
            onClick={() => go("day")}
          >
            Elegir día
          </Button>
        </section>
      )}

      {step === "day" && (
        <section aria-labelledby="paso-dia" className="space-y-3">
          <h2 id="paso-dia" className="text-base font-semibold">
            ¿Qué día?
          </h2>
          <div className="grid grid-cols-3 gap-2">
            {days.map((d) => (
              <Choice
                key={d}
                selected={d === day}
                onClick={() => {
                  setDay(d);
                  setStartAt(null);
                  go("time");
                }}
              >
                <span className="w-full text-center">{dayLabel(d)}</span>
              </Choice>
            ))}
          </div>
        </section>
      )}

      {step === "time" && day && (
        <section aria-labelledby="paso-horario" className="space-y-3">
          <h2 id="paso-horario" className="text-base font-semibold">
            Horarios del {dayLabel(day)}
          </h2>
          {availability.isPending && (
            <div role="status" aria-label="Buscando horarios" className="flex justify-center py-6">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-muted border-t-primary" />
            </div>
          )}
          {availability.isError && (
            <p role="alert" className="text-sm text-destructive">
              {turneroErrorMessage(availability.error)}
            </p>
          )}
          {availability.data && availability.data.slots.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No quedan horarios libres ese día. Probá con otro.
            </p>
          )}
          {availability.data && availability.data.slots.length > 0 && (
            <div className="grid grid-cols-3 gap-2">
              {availability.data.slots.map((slot) => (
                <Choice
                  key={slot.start_at}
                  selected={slot.start_at === startAt}
                  onClick={() => {
                    setStartAt(slot.start_at);
                    go("details");
                  }}
                >
                  <span className="w-full text-center">{localTime(slot.start_at, tz)}</span>
                </Choice>
              ))}
            </div>
          )}
          <p className="text-xs text-muted-foreground">Horarios del negocio ({tz}).</p>
        </section>
      )}

      {step === "details" && startAt && size && (
        <section aria-labelledby="paso-datos" className="space-y-3">
          <h2 id="paso-datos" className="text-base font-semibold">
            Tus datos
          </h2>
          <p className="text-sm text-muted-foreground">
            {size.label} · {chosen.map((s) => s.name).join(" + ")} · {dayLabel(day!)}{" "}
            {localTime(startAt, tz)} · {formatARS(totals.price)}
          </p>
          <BookingDetailsForm
            policy={business.cancellation_policy}
            submitLabel="Reservar"
            initialValues={details}
            onValuesChange={setDetails}
            onSubmit={submit}
          />
        </section>
      )}
    </div>
  );
}
