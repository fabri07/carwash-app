"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Button } from "@/components/ui/button";
import { Form } from "@/components/ui/form";
import { SelectField, TextField } from "@/features/configuracion/fields";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import { cn } from "@/lib/utils";
import { settle } from "./carga";
import { Guard } from "./Guard";
import { inspectionSchema, orNull, type InspectionInput } from "./schemas";
import { tapTime } from "./time";
import { TOUCH_TARGET } from "./touch";
import type { DirtLevel, InspectionBody } from "./types";
import { KINDS, useOperacionSubmit } from "./useOperacionSubmit";

const DIRT: { value: DirtLevel | ""; label: string }[] = [
  { value: "", label: "Sin indicar" },
  { value: "NORMAL", label: "Normal" },
  { value: "INTENSA", label: "Intensa" },
  { value: "BARRO", label: "Barro" },
  { value: "ARENA", label: "Arena" },
  { value: "PELOS_DE_MASCOTA", label: "Pelos de mascota" },
  { value: "TRABAJO_ESPECIAL", label: "Trabajo especial" },
];

/**
 * Recepción completa (`POST /jobs/{id}/inspection`): suciedad, objetos de valor y daños previos. Es
 * secundaria y OPCIONAL (D-007): nada del camino normal (llegó, iniciar, finalizar, cobrar) pasa por
 * acá, y un trabajo se abre, cierra y cobra sin tocarla.
 */
export function InspectionPageClient({ id }: { id: string }) {
  return (
    <Guard permission="JOBS_OPERAR" title="la recepción">
      <InspectionForm id={id} />
    </Guard>
  );
}

function InspectionForm({ id }: { id: string }) {
  const router = useRouter();
  const { run } = useOperacionSubmit();
  const form = useForm<InspectionInput>({
    resolver: zodResolver(inspectionSchema),
    defaultValues: { dirt_level: "", valuables: "", pre_existing_damage: "" },
  });
  const { onSubmit, formError } = useFormSubmit(form, async (v) => {
    const body: InspectionBody = {
      occurred_at: tapTime(),
      dirt_level: (v.dirt_level || null) as DirtLevel | null,
      valuables: orNull(v.valuables),
      pre_existing_damage: orNull(v.pre_existing_damage),
    };
    settle(await run(KINDS.inspection, { jobId: id, body }), "inspection", "Recepción guardada");
    router.push(`/operacion/${id}`);
  });
  return (
    <section aria-labelledby="inspection-title" className="space-y-4">
      <Link
        href={`/operacion/${id}`}
        className={cn(TOUCH_TARGET, "inline-flex items-center text-sm underline")}
      >
        ← Volver al trabajo
      </Link>
      <h1 id="inspection-title" className="text-2xl font-semibold">
        Recepción completa
      </h1>
      <p className="text-sm text-muted-foreground">
        Opcional: el trabajo se puede iniciar, finalizar y cobrar sin completarla.
      </p>
      <Form {...form}>
        <form onSubmit={onSubmit} noValidate className="space-y-4">
          <SelectField control={form.control} name="dirt_level" label="Suciedad" options={DIRT} />
          <TextField control={form.control} name="valuables" label="Objetos de valor" multiline />
          <TextField
            control={form.control}
            name="pre_existing_damage"
            label="Daños previos"
            multiline
          />
          {formError && (
            <p role="alert" className="text-sm font-medium text-destructive">
              {formError}
            </p>
          )}
          <Button type="submit" className="w-full" disabled={form.formState.isSubmitting}>
            Guardar recepción
          </Button>
        </form>
      </Form>
    </section>
  );
}
