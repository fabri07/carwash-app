"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Form } from "@/components/ui/form";
import { categoryOptions } from "@/features/catalogo/format";
import { PriceFields } from "@/features/catalogo/PriceFields";
import { DialogFormFooter, SelectField, TextField } from "@/features/configuracion/fields";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import type { ServiceCategory, VehicleSize } from "@/types/api";
import { serviceCreateSchema, type ServiceCreateInput } from "@/validation/services";

interface ServiceFormProps {
  sizes: VehicleSize[];
  categories: ServiceCategory[];
  onSubmit: (values: ServiceCreateInput) => Promise<void>;
  onCancel: () => void;
}

/**
 * Alta de un servicio con sus precios por tamaño (crear exige `CATALOGO_EDITAR` y
 * `PRECIOS_EDITAR`). Un servicio sin precio para algún tamaño no existe (A8).
 */
export function ServiceForm({ sizes, categories, onSubmit, onCancel }: ServiceFormProps) {
  const form = useForm<ServiceCreateInput>({
    resolver: zodResolver(serviceCreateSchema),
    defaultValues: {
      name: "",
      category_id: "",
      notes: "",
      pricing_mode: "PRECIO_FIJO",
      deposit_percent: "",
      prices: sizes.map((s) => ({
        vehicle_size_id: s.id,
        price_cents: undefined,
        duration_min: "",
      })),
    },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);
  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <TextField
          control={form.control}
          name="name"
          label="Nombre"
          placeholder="Lavado completo"
        />
        <SelectField
          control={form.control}
          name="category_id"
          label="Categoría"
          options={categoryOptions(categories)}
        />
        <TextField
          control={form.control}
          name="notes"
          label="Notas (opcional)"
          multiline
          hint="Qué incluye, aclaraciones para el equipo."
        />
        <TextField
          control={form.control}
          name="deposit_percent"
          label="Seña propia (%) (opcional)"
          inputMode="decimal"
          placeholder="Usa la general"
          hint="Vacío: usa el % de seña del negocio. Solo se cobra si la seña está activa en Configuración."
        />
        <PriceFields sizes={sizes} />
        <DialogFormFooter
          formError={formError}
          submitting={form.formState.isSubmitting}
          submitLabel="Crear servicio"
          onCancel={onCancel}
        />
      </form>
    </Form>
  );
}
