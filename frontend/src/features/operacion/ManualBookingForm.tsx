"use client";

import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Button } from "@/components/ui/button";
import { Form } from "@/components/ui/form";
import { SelectField, TextField } from "@/features/configuracion/fields";
import { zonedToUtcIso } from "@/features/turnos/time";
import { useCan } from "@/hooks/usePermission";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import { todayInZone } from "@/lib/period";
import { settle, useCargaData } from "./carga";
import { PersonFields, ResourceSelect, ToggleField, VehicleFields, priceHint } from "./fields";
import { defaultPaymentMethod, useBusinessTimeZone } from "./queries";
import { manualBookingSchema, moneyCents, orNull, type ManualBookingInput } from "./schemas";
import { BIG_ACTION } from "./touch";
import type { ManualBookingBody } from "./types";
import { KINDS, useOperacionSubmit } from "./useOperacionSubmit";

/**
 * Turno "solo a mano" (`POST /bookings/manual`): lo que no pasó por el turnero. Puede llevar la
 * seña ya cobrada. El choque de horario lo decide el servidor (409 `SLOT_TAKEN`) y se muestra sin
 * perder lo cargado.
 */
export function ManualBookingForm() {
  const router = useRouter();
  const { run } = useOperacionSubmit();
  const timeZone = useBusinessTimeZone();
  const data = useCargaData();
  // La seña cobrada pide COBROS_REGISTRAR: sin él no se ofrece (el servidor respondería 403).
  const canCharge = useCan()("COBROS_REGISTRAR");
  const form = useForm<ManualBookingInput>({
    resolver: zodResolver(manualBookingSchema),
    defaultValues: {
      customer_name: "",
      customer_phone: "",
      plate: "",
      vehicle_size_id: "",
      service_ids: [],
      resource_id: "",
      date: todayInZone(new Date(), timeZone),
      time: "",
      deposit_enabled: false,
      deposit_method_id: "",
      deposit_text: "",
    },
  });
  const { onSubmit, formError } = useFormSubmit(form, async (v) => {
    const body: ManualBookingBody = {
      customer_name: orNull(v.customer_name),
      customer_phone: orNull(v.customer_phone),
      plate: orNull(v.plate),
      vehicle_size_id: v.vehicle_size_id,
      service_ids: v.service_ids,
      resource_id: v.resource_id,
      start_at: zonedToUtcIso(v.date, v.time, timeZone),
      channel: "CARGA_MANUAL",
      deposit:
        canCharge && v.deposit_enabled
          ? {
              payment_method_id: v.deposit_method_id,
              ...(moneyCents(v.deposit_text) ? { amount_cents: moneyCents(v.deposit_text) } : {}),
            }
          : null,
    };
    settle(
      await run(KINDS.manualBooking, { body }, `turno-a-mano:${crypto.randomUUID()}`),
      "manualBooking",
      "Turno cargado",
    );
    router.push("/agenda");
  });

  if (data.loading) return <p className="text-sm text-muted-foreground">Cargando…</p>;
  if (data.error) {
    return (
      <p role="alert" className="text-sm text-destructive">
        No se pudo cargar el catálogo. Revisá la conexión y recargá.
      </p>
    );
  }
  const incomes = data.methods.filter((m) => m.for_income);
  const withDeposit = form.watch("deposit_enabled");
  const hint = priceHint(data.services, form.watch("service_ids"), form.watch("vehicle_size_id"));

  return (
    <Form {...form}>
      <form onSubmit={onSubmit} noValidate className="space-y-5">
        <PersonFields control={form.control} />
        <VehicleFields control={form.control} sizes={data.sizes} services={data.services} />
        {hint && <p className="text-sm text-muted-foreground">{hint}</p>}
        <ResourceSelect control={form.control} resources={data.resources} required />
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField control={form.control} name="date" label="Día" type="date" />
          <TextField control={form.control} name="time" label="Hora de inicio" type="time" />
        </div>
        {canCharge && (
          <ToggleField
            control={form.control}
            name="deposit_enabled"
            onLabel="Con seña ya cobrada (tocá para quitar)"
            offLabel="Sin seña (tocá para cargar una)"
          />
        )}
        {canCharge && withDeposit && (
          <div className="grid gap-3 sm:grid-cols-2">
            <SelectField
              control={form.control}
              name="deposit_method_id"
              label="Medio de pago de la seña"
              options={[
                { value: "", label: "Elegí un medio" },
                ...incomes.map((m) => ({ value: m.id, label: m.label })),
              ]}
            />
            <TextField
              control={form.control}
              name="deposit_text"
              inputMode="decimal"
              label="Importe de la seña"
              hint="Vacío: la seña que pide el negocio."
            />
          </div>
        )}
        {formError && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {formError}
          </p>
        )}
        <Button
          type="submit"
          className={BIG_ACTION}
          disabled={form.formState.isSubmitting}
          onClick={() => {
            // Atajo: con seña, el medio de pago por defecto ya viene elegido.
            if (form.getValues("deposit_enabled") && !form.getValues("deposit_method_id")) {
              form.setValue("deposit_method_id", defaultPaymentMethod(data.methods)?.id ?? "");
            }
          }}
        >
          Cargar turno
        </Button>
      </form>
    </Form>
  );
}
