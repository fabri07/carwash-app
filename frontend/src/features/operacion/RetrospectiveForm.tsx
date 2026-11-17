"use client";

import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Button } from "@/components/ui/button";
import { Form } from "@/components/ui/form";
import { MoneyField, SelectField, TextField } from "@/features/configuracion/fields";
import { zonedToUtcIso } from "@/features/turnos/time";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import { todayInZone } from "@/lib/period";
import { settle, useCargaData } from "./carga";
import { PersonFields, ResourceSelect, ToggleField, VehicleFields, priceHint } from "./fields";
import { defaultPaymentMethod, useBusinessTimeZone } from "./queries";
import { orNull, retrospectiveSchema, type RetrospectiveInput } from "./schemas";
import { BIG_ACTION } from "./touch";
import type { RetrospectiveBody } from "./types";
import { KINDS, useOperacionSubmit } from "./useOperacionSubmit";

/**
 * Carga retrospectiva (`POST /jobs/retrospective`, D6-3): un trabajo ya hecho, con cliente (puede
 * ser "sin nombre"), vehículo, servicio, inicio, fin y cobro (cobrado con medio y monto, o
 * pendiente). Cae en la agenda de SU día. Ni el inicio ni el fin pueden ser a futuro: lo rechaza el
 * servidor con un 422 que se muestra sin perder lo cargado.
 */
export function RetrospectiveForm() {
  const router = useRouter();
  const { run } = useOperacionSubmit();
  const timeZone = useBusinessTimeZone();
  const data = useCargaData();
  const form = useForm<RetrospectiveInput>({
    resolver: zodResolver(retrospectiveSchema),
    defaultValues: {
      customer_name: "",
      customer_phone: "",
      plate: "",
      vehicle_size_id: "",
      service_ids: [],
      resource_id: "",
      date: todayInZone(new Date(), timeZone),
      start_time: "",
      end_time: "",
      paid: true,
      payment_method_id: "",
      amount_cents: null,
    },
  });
  const { onSubmit, formError } = useFormSubmit(form, async (v) => {
    const body: RetrospectiveBody = {
      customer_name: orNull(v.customer_name),
      customer_phone: orNull(v.customer_phone),
      plate: orNull(v.plate),
      vehicle_size_id: v.vehicle_size_id,
      service_ids: v.service_ids,
      resource_id: v.resource_id || null,
      started_at: zonedToUtcIso(v.date, v.start_time, timeZone),
      finished_at: zonedToUtcIso(v.date, v.end_time, timeZone),
      channel: "CARGA_MANUAL",
      payment: v.paid
        ? {
            payment_method_id: v.payment_method_id,
            ...(v.amount_cents ? { amount_cents: v.amount_cents } : {}),
          }
        : null,
    };
    settle(await run(KINDS.retrospective, { body }), "retrospective", "Trabajo cargado");
    router.push(`/agenda?date=${v.date}`);
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
  const paid = form.watch("paid");
  const hint = priceHint(data.services, form.watch("service_ids"), form.watch("vehicle_size_id"));

  return (
    <Form {...form}>
      <form onSubmit={onSubmit} noValidate className="space-y-5">
        <PersonFields control={form.control} />
        <VehicleFields control={form.control} sizes={data.sizes} services={data.services} />
        {hint && (
          <p className="text-sm text-muted-foreground">
            {hint}. El precio sale del catálogo de hoy, no del de aquel día.
          </p>
        )}
        <ResourceSelect control={form.control} resources={data.resources} />
        <div className="grid gap-3 sm:grid-cols-3">
          <TextField control={form.control} name="date" label="Día" type="date" />
          <TextField control={form.control} name="start_time" label="Inicio" type="time" />
          <TextField control={form.control} name="end_time" label="Fin" type="time" />
        </div>
        <ToggleField
          control={form.control}
          name="paid"
          onLabel="Cobrado (tocá para dejarlo pendiente)"
          offLabel="Cobro pendiente (tocá para cargar el cobro)"
        />
        {paid && (
          <div className="grid gap-3 sm:grid-cols-2">
            <SelectField
              control={form.control}
              name="payment_method_id"
              label="Medio de pago"
              options={[
                { value: "", label: "Elegí un medio" },
                ...incomes.map((m) => ({ value: m.id, label: m.label })),
              ]}
            />
            <MoneyField
              control={form.control}
              name="amount_cents"
              label="Importe cobrado"
              hint="Vacío: el total del trabajo."
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
            // Con cobro, el medio de pago por defecto ya viene elegido (no se pide un toque más).
            if (form.getValues("paid") && !form.getValues("payment_method_id")) {
              form.setValue("payment_method_id", defaultPaymentMethod(data.methods)?.id ?? "");
            }
          }}
        >
          Cargar trabajo
        </Button>
      </form>
    </Form>
  );
}
