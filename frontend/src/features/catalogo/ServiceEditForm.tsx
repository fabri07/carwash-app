"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Form } from "@/components/ui/form";
import { categoryOptions } from "@/features/catalogo/format";
import { DialogFormFooter, SelectField, TextField } from "@/features/configuracion/fields";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import type { Service, ServiceCategory } from "@/types/api";
import { serviceEditSchema, type ServiceEditInput } from "@/validation/services";

interface ServiceEditFormProps {
  service: Service;
  categories: ServiceCategory[];
  onSubmit: (values: ServiceEditInput) => Promise<void>;
  onCancel: () => void;
}

/** Nombre, categoría y notas de un servicio (`CATALOGO_EDITAR`). Los precios van aparte (`PRECIOS_EDITAR`). */
export function ServiceEditForm({ service, categories, onSubmit, onCancel }: ServiceEditFormProps) {
  const form = useForm<ServiceEditInput>({
    resolver: zodResolver(serviceEditSchema),
    defaultValues: {
      name: service.name,
      category_id: service.category_id ?? "",
      notes: service.notes ?? "",
    },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);
  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <TextField control={form.control} name="name" label="Nombre" />
        <SelectField
          control={form.control}
          name="category_id"
          label="Categoría"
          options={categoryOptions(categories)}
        />
        <TextField control={form.control} name="notes" label="Notas (opcional)" multiline />
        <DialogFormFooter
          formError={formError}
          submitting={form.formState.isSubmitting}
          submitLabel="Guardar cambios"
          onCancel={onCancel}
        />
      </form>
    </Form>
  );
}
