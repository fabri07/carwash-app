"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Form } from "@/components/ui/form";
import { DialogFormFooter, SelectField, TextField } from "@/features/configuracion/fields";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import type { VehicleModel } from "@/types/api";
import { BODY_TYPES, modelSchema, type ModelInput } from "@/validation/vehicles";

interface VehicleModelFormProps {
  item?: VehicleModel;
  /** Nombre de la marca, para el texto de ayuda. */
  makeName: string;
  onSubmit: (values: ModelInput) => Promise<void>;
  onCancel: () => void;
}

/** Alta y edición de un modelo: nombre y carrocería (la que sugiere el tamaño). */
export function VehicleModelForm({ item, makeName, onSubmit, onCancel }: VehicleModelFormProps) {
  const form = useForm<ModelInput>({
    resolver: zodResolver(modelSchema),
    defaultValues: { name: item?.name ?? "", body_type: item?.body_type ?? "SEDAN" },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);
  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <TextField
          control={form.control}
          name="name"
          label="Nombre"
          placeholder="Hilux"
          hint={`Modelo de ${makeName}. No puede repetirse dentro de la marca.`}
        />
        <SelectField
          control={form.control}
          name="body_type"
          label="Carrocería"
          hint="Define el tamaño que se sugiere al cargar un vehículo de este modelo."
          options={BODY_TYPES.map((b) => ({ value: b.value, label: `${b.label} (${b.hint})` }))}
        />
        <DialogFormFooter
          formError={formError}
          submitting={form.formState.isSubmitting}
          submitLabel={item ? "Guardar cambios" : "Crear modelo"}
          onCancel={onCancel}
        />
      </form>
    </Form>
  );
}
