"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Form } from "@/components/ui/form";
import { DialogFormFooter, TextField } from "@/features/configuracion/fields";
import type { CatalogFormProps } from "@/features/configuracion/CatalogTab";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import type { Resource } from "@/types/api";
import { resourceSchema, type ResourceInput } from "@/validation/catalog";

/** Alta y edición de un puesto (lugar físico donde se atiende un vehículo). */
export function ResourceForm({
  item,
  onSubmit,
  onCancel,
}: CatalogFormProps<Resource, ResourceInput>) {
  const form = useForm<ResourceInput>({
    resolver: zodResolver(resourceSchema),
    defaultValues: { name: item?.name ?? "", sort_order: String(item?.sort_order ?? 0) },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);
  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <TextField control={form.control} name="name" label="Nombre" placeholder="Puesto 1" />
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
          submitLabel={item ? "Guardar cambios" : "Crear puesto"}
          onCancel={onCancel}
        />
      </form>
    </Form>
  );
}
