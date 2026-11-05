"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Form } from "@/components/ui/form";
import { DialogFormFooter, TextField } from "@/features/configuracion/fields";
import type { CatalogFormProps } from "@/features/configuracion/CatalogTab";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import type { VehicleSize } from "@/types/api";
import { vehicleSizeSchema, type VehicleSizeInput } from "@/validation/catalog";

/** Alta y edición de un tamaño de vehículo. El código identifica al tamaño y no se edita. */
export function VehicleSizeForm({
  item,
  onSubmit,
  onCancel,
}: CatalogFormProps<VehicleSize, VehicleSizeInput>) {
  const form = useForm<VehicleSizeInput>({
    resolver: zodResolver(vehicleSizeSchema),
    defaultValues: {
      code: item?.code ?? "",
      label: item?.label ?? "",
      sort_order: String(item?.sort_order ?? 0),
    },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);
  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <TextField
          control={form.control}
          name="code"
          label="Código"
          hint="Mayúsculas, números o guion bajo. No se puede cambiar después."
          disabled={item !== undefined}
        />
        <TextField control={form.control} name="label" label="Nombre" placeholder="Auto" />
        <TextField
          control={form.control}
          name="sort_order"
          label="Orden en las listas"
          inputMode="numeric"
          hint="Los más chicos van primero."
        />
        <DialogFormFooter
          formError={formError}
          submitting={form.formState.isSubmitting}
          submitLabel={item ? "Guardar cambios" : "Crear tamaño"}
          onCancel={onCancel}
        />
      </form>
    </Form>
  );
}
