"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { NativeSelect } from "@/components/ui/native-select";
import { DialogFormFooter, TextField } from "@/features/configuracion/fields";
import type { CatalogFormProps } from "@/features/configuracion/CatalogTab";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import { hhmm } from "@/lib/time";
import type { BusinessHours } from "@/types/api";
import { businessHoursSchema, type BusinessHoursInput } from "@/validation/catalog";

/** ISO: 1 = lunes … 7 = domingo (como el backend). */
export const WEEKDAYS = [
  "Lunes",
  "Martes",
  "Miércoles",
  "Jueves",
  "Viernes",
  "Sábado",
  "Domingo",
] as const;

/** Alta y edición de una franja de atención. Un día puede tener varias, sin pisarse. */
export function BusinessHoursForm({
  item,
  onSubmit,
  onCancel,
}: CatalogFormProps<BusinessHours, BusinessHoursInput>) {
  const form = useForm<BusinessHoursInput>({
    resolver: zodResolver(businessHoursSchema),
    defaultValues: {
      weekday: String(item?.weekday ?? 1),
      opens_at: item ? hhmm(item.opens_at) : "09:00",
      closes_at: item ? hhmm(item.closes_at) : "18:00",
    },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);
  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <FormField
          control={form.control}
          name="weekday"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Día</FormLabel>
              <FormControl>
                <NativeSelect {...field}>
                  {WEEKDAYS.map((name, i) => (
                    <option key={name} value={String(i + 1)}>
                      {name}
                    </option>
                  ))}
                </NativeSelect>
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField control={form.control} name="opens_at" label="Abre" type="time" />
          <TextField control={form.control} name="closes_at" label="Cierra" type="time" />
        </div>
        <DialogFormFooter
          formError={formError}
          submitting={form.formState.isSubmitting}
          submitLabel={item ? "Guardar cambios" : "Crear franja"}
          onCancel={onCancel}
        />
      </form>
    </Form>
  );
}
