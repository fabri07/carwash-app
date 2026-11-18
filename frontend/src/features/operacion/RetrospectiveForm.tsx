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
import {
  moneyCents,
  orNull,
  resolveEndDay,
  retrospectiveSchema,
  type RetrospectiveInput,
} from "./schemas";
import { BIG_ACTION } from "./touch";
import type { RetrospectiveBody } from "./types";
import { KINDS, useOperacionSubmit } from "./useOperacionSubmit";
import { useStableKey } from "./useStableKey";

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
  const dedupe = useStableKey("carga-retro");
  // Cargar el cobro pide COBROS_REGISTRAR: sin él la carga es "pendiente" y no se ofrece "Cobrado".
  const canCharge = useCan()("COBROS_REGISTRAR");
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
      end_date: "",
      paid: canCharge,
      payment_method_id: "",
      amount_text: "",
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
      finished_at: zonedToUtcIso(resolveEndDay(v), v.end_time, timeZone),
      channel: "CARGA_MANUAL",
      payment:
        canCharge && v.paid
          ? {
              payment_method_id: v.payment_method_id,
              ...(moneyCents(v.amount_text) ? { amount_cents: moneyCents(v.amount_text) } : {}),
            }
          : null,
    };
    const out = await run(KINDS.retrospective, { body }, dedupe.key);
    settle(out, "retrospective", "Trabajo cargado");
    if (out.status === "ok") dedupe.renew();
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
  const [date, start, end, endDate] = form.watch(["date", "start_time", "end_time", "end_date"]);
  const endDay = /^\d{4}-\d{2}-\d{2}$/.test(date)
    ? resolveEndDay({ date, start_time: start, end_time: end, end_date: endDate })
    : date;
  const nextDay =
    endDate === "" && endDay !== date && /^\d{2}:\d{2}$/.test(start) && /^\d{2}:\d{2}$/.test(end)
      ? `${endDay.slice(8, 10)}/${endDay.slice(5, 7)}`
      : null;
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
        <TextField
          control={form.control}
          name="end_date"
          label="Día de fin (solo si terminó otro día)"
          type="date"
          hint="Vacío: terminó el mismo día."
        />
        {nextDay && (
          <p role="status" className="rounded-md border border-sky-300 p-3 text-sm">
            El fin es menor al inicio: se carga como que terminó al día siguiente ({nextDay}). Si no
            es así, corregí las horas.
          </p>
        )}
        {canCharge ? (
          <ToggleField
            control={form.control}
            name="paid"
            onLabel="Cobrado (tocá para dejarlo pendiente)"
            offLabel="Cobro pendiente (tocá para cargar el cobro)"
          />
        ) : (
          <p className="text-sm text-muted-foreground">
            Cobro pendiente: no tenés permiso para registrar cobros.
          </p>
        )}
        {canCharge && paid && (
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
            <TextField
              control={form.control}
              name="amount_text"
              inputMode="decimal"
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
