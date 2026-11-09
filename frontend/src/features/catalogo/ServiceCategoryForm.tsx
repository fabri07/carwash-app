"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Form } from "@/components/ui/form";
import type { CatalogFormProps } from "@/features/configuracion/CatalogTab";
import { DialogFormFooter, TextField } from "@/features/configuracion/fields";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import type { ServiceCategory } from "@/types/api";
import { categorySchema, type CategoryInput } from "@/validation/services";

/** Alta y edición de una categoría de servicios (agrupa la lista: "Lavado", "Interior"…). */
export function ServiceCategoryForm({
  item,
  onSubmit,
  onCancel,
}: CatalogFormProps<ServiceCategory, CategoryInput>) {
  const form = useForm<CategoryInput>({
    resolver: zodResolver(categorySchema),
    defaultValues: { name: item?.name ?? "", sort_order: String(item?.sort_order ?? 0) },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);
  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <TextField control={form.control} name="name" label="Nombre" placeholder="Lavado" />
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
          submitLabel={item ? "Guardar cambios" : "Crear categoría"}
          onCancel={onCancel}
        />
      </form>
    </Form>
  );
}
