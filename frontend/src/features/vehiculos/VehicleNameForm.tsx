"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Form } from "@/components/ui/form";
import { DialogFormFooter, TextField } from "@/features/configuracion/fields";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import { colorSchema, makeSchema, type MakeInput } from "@/validation/vehicles";

interface VehicleNameFormProps {
  /** Marca o color: cambia el límite de largo y los textos. */
  kind: "make" | "color";
  item?: { name: string };
  onSubmit: (values: MakeInput) => Promise<void>;
  onCancel: () => void;
}

/** Alta y renombre de una marca o un color: solo el nombre. */
export function VehicleNameForm({ kind, item, onSubmit, onCancel }: VehicleNameFormProps) {
  const form = useForm<MakeInput>({
    resolver: zodResolver(kind === "make" ? makeSchema : colorSchema),
    defaultValues: { name: item?.name ?? "" },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);
  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <TextField
          control={form.control}
          name="name"
          label="Nombre"
          placeholder={kind === "make" ? "Volkswagen" : "Gris oscuro"}
          hint="Sin distinguir mayúsculas ni tildes: no puede repetirse."
        />
        <DialogFormFooter
          formError={formError}
          submitting={form.formState.isSubmitting}
          submitLabel={item ? "Guardar cambios" : kind === "make" ? "Crear marca" : "Crear color"}
          onCancel={onCancel}
        />
      </form>
    </Form>
  );
}
