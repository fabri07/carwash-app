"use client";

import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Form } from "@/components/ui/form";
import { SelectField } from "@/features/configuracion/fields";
import { useBodyTypeSizes, vehicleKeys } from "@/features/vehiculos/queries";
import { useAllVehicleSizes } from "@/features/configuracion/queries";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import { configError, FormSubmitError } from "@/lib/errors";
import { putBodyTypeSizes } from "@/services/vehicles.service";
import { toast } from "@/stores/toastStore";
import type { BodyTypeSize } from "@/types/api";
import { BODY_TYPES, bodyTypeSizesSchema, type BodyTypeSizesInput } from "@/validation/vehicles";

/**
 * Los valores del formulario. Un tamaño que ya no está entre los vivos (se anuló en Tamaños y el
 * mapa en caché todavía lo trae) se muestra como "Sin sugerencia": mandarlo daría 422.
 */
function toValues(rows: BodyTypeSize[] | undefined, live: Set<string>): BodyTypeSizesInput {
  const sizes: BodyTypeSizesInput["sizes"] = {};
  for (const b of BODY_TYPES) {
    const id = rows?.find((r) => r.body_type === b.value)?.vehicle_size_id ?? "";
    sizes[b.value] = live.has(id) ? id : "";
  }
  return { sizes };
}

/**
 * Carrocería → tamaño (FASE-4-CONTRATO A10): qué tamaño del negocio se sugiere al cargar un
 * vehículo de cada carrocería. Se guarda el mapa entero de una vez (`PUT /body-type-sizes`); "Sin
 * sugerencia" deja la carrocería sin tamaño.
 */
export function BodyTypeSizesForm() {
  const queryClient = useQueryClient();
  const current = useBodyTypeSizes();
  const sizesQuery = useAllVehicleSizes();
  const sizes = sizesQuery.data?.items ?? [];
  const form = useForm<BodyTypeSizesInput>({
    resolver: zodResolver(bodyTypeSizesSchema),
    defaultValues: toValues(undefined, new Set()),
  });
  const { reset } = form;
  const liveIds = sizes.map((s) => s.id).join("|");
  useEffect(() => {
    if (current.data) reset(toValues(current.data, new Set(liveIds.split("|"))));
  }, [current.data, liveIds, reset]);

  const { onSubmit, formError } = useFormSubmit(form, async (values) => {
    try {
      const saved = await putBodyTypeSizes({
        items: BODY_TYPES.map((b) => ({
          body_type: b.value,
          vehicle_size_id: values.sizes[b.value] || null,
        })),
      });
      queryClient.setQueryData(vehicleKeys.bodyTypeSizes, saved);
    } catch (e) {
      throw new FormSubmitError({ field: null, message: configError(e).message });
    }
    // Las sugerencias de cada modelo cambian: se releen.
    await queryClient.invalidateQueries({ queryKey: vehicleKeys.all });
    toast.success("Carrocerías guardadas.");
  });

  if (current.isLoading || sizesQuery.isLoading) {
    return <p className="text-sm text-muted-foreground">Cargando…</p>;
  }
  if (sizes.length === 0) {
    return (
      <p className="rounded-lg border p-4 text-sm text-muted-foreground">
        Todavía no hay tamaños. Cargalos en la pestaña Tamaños y después elegí cuál sugiere cada
        carrocería.
      </p>
    );
  }
  const options = [
    { value: "", label: "Sin sugerencia" },
    ...sizes.map((s) => ({ value: s.id, label: s.label })),
  ];
  return (
    <Form {...form}>
      <form onSubmit={onSubmit} noValidate className="space-y-4 rounded-xl border bg-card p-4">
        <div>
          <h2 className="text-lg font-semibold">Carrocería y tamaño</h2>
          <p className="text-sm text-muted-foreground">
            Al elegir el modelo de un vehículo se sugiere este tamaño; siempre se puede cambiar.
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          {BODY_TYPES.map((b) => (
            <SelectField
              key={b.value}
              control={form.control}
              name={`sizes.${b.value}`}
              label={b.label}
              hint={b.hint}
              options={options}
            />
          ))}
        </div>
        {formError && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {formError}
          </p>
        )}
        <div className="flex justify-end">
          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? "Guardando…" : "Guardar carrocerías"}
          </Button>
        </div>
      </form>
    </Form>
  );
}
