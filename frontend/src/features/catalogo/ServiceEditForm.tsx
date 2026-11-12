"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Form } from "@/components/ui/form";
import { categoryOptions } from "@/features/catalogo/format";
import { ScheduleRulesField } from "@/features/catalogo/ScheduleRulesField";
import {
  DialogFormFooter,
  SelectField,
  SwitchField,
  TextField,
} from "@/features/configuracion/fields";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import type { Service, ServiceCategory } from "@/types/api";
import { serviceEditSchema, toRuleRows, type ServiceEditInput } from "@/validation/services";

interface ServiceEditFormProps {
  service: Service;
  categories: ServiceCategory[];
  onSubmit: (values: ServiceEditInput) => Promise<void>;
  onCancel: () => void;
}

/**
 * Nombre, categoría, notas, si aparece en el turnero y sus reglas horarias (`CATALOGO_EDITAR`).
 * Los precios van aparte (`PRECIOS_EDITAR`).
 */
export function ServiceEditForm({ service, categories, onSubmit, onCancel }: ServiceEditFormProps) {
  const form = useForm<ServiceEditInput>({
    resolver: zodResolver(serviceEditSchema),
    defaultValues: {
      name: service.name,
      category_id: service.category_id ?? "",
      notes: service.notes ?? "",
      solo_a_mano: service.booking_mode === "SOLO_A_MANO",
      schedule_rules: toRuleRows(service.schedule_rules),
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
        <SwitchField
          control={form.control}
          name="solo_a_mano"
          label="Solo a mano (no aparece en el turnero)"
          hint="Lo agenda el negocio: los clientes no lo pueden reservar desde la web."
        />
        <ScheduleRulesField control={form.control} />
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
