"use client";

import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Button } from "@/components/ui/button";
import { Form } from "@/components/ui/form";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import { settle, useCargaData } from "./carga";
import { PersonFields, ResourceSelect, VehicleFields } from "./fields";
import { orNull, walkInSchema, type WalkInInput } from "./schemas";
import { BIG_ACTION } from "./touch";
import type { Job, ReceiveBody } from "./types";
import { KINDS, useOperacionSubmit } from "./useOperacionSubmit";

/**
 * Ingreso sin turno: cliente, vehículo y servicio, y LLEGÓ (un toque). Crea el job (`POST /jobs`
 * sin turno) y sigue en su pantalla operativa para iniciarlo.
 */
export function WalkInForm() {
  const router = useRouter();
  const { run } = useOperacionSubmit();
  const data = useCargaData();
  const form = useForm<WalkInInput>({
    resolver: zodResolver(walkInSchema),
    defaultValues: {
      customer_name: "",
      customer_phone: "",
      plate: "",
      vehicle_size_id: "",
      service_ids: [],
      resource_id: "",
    },
  });
  const { onSubmit, formError } = useFormSubmit(form, async (v) => {
    const body: Omit<ReceiveBody, "occurred_at"> = {
      customer_name: orNull(v.customer_name),
      customer_phone: orNull(v.customer_phone),
      plate: orNull(v.plate),
      vehicle_size_id: v.vehicle_size_id,
      service_ids: v.service_ids,
      resource_id: v.resource_id || null,
      channel: "CALLE",
    };
    const out = await run(KINDS.receive, { body }, `llego-sin-turno:${crypto.randomUUID()}`);
    settle(out, "receive", "Llegada registrada");
    const job = out.status === "ok" ? (out.data as Job | undefined) : undefined;
    router.push(job ? `/operacion/${job.id}` : "/agenda");
  });

  if (data.loading) return <p className="text-sm text-muted-foreground">Cargando…</p>;
  if (data.error) {
    return (
      <p role="alert" className="text-sm text-destructive">
        No se pudo cargar el catálogo. Revisá la conexión y recargá.
      </p>
    );
  }
  return (
    <Form {...form}>
      <form onSubmit={onSubmit} noValidate className="space-y-5">
        <PersonFields control={form.control} />
        <VehicleFields control={form.control} sizes={data.sizes} services={data.services} />
        <ResourceSelect control={form.control} resources={data.resources} />
        {formError && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {formError}
          </p>
        )}
        <Button type="submit" className={BIG_ACTION} disabled={form.formState.isSubmitting}>
          LLEGÓ
        </Button>
      </form>
    </Form>
  );
}
