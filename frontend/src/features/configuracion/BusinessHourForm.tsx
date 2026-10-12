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
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { FormActions } from "@/features/configuracion/FormActions";
import { shortTime, WEEKDAYS } from "@/features/configuracion/labels";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import type { BusinessHour, BusinessHourPayload } from "@/types/api";
import { businessHourSchema, type BusinessHourInput } from "@/validation/configuracion";

interface BusinessHourFormProps {
  /** Sin franja: alta. Con franja: edición. */
  businessHour?: BusinessHour;
  onSubmit: (payload: BusinessHourPayload) => Promise<void>;
  onCancel?: () => void;
  submitLabel?: string;
}

/**
 * Franja de atención de un día. Un día puede tener varias (mañana y tarde);
 * un día sin franjas es un día cerrado.
 */
export function BusinessHourForm({
  businessHour,
  onSubmit,
  onCancel,
  submitLabel,
}: BusinessHourFormProps) {
  const form = useForm<BusinessHourInput>({
    resolver: zodResolver(businessHourSchema),
    defaultValues: {
      weekday: businessHour ? String(businessHour.weekday) : "",
      opens_at: businessHour ? shortTime(businessHour.opens_at) : "",
      closes_at: businessHour ? shortTime(businessHour.closes_at) : "",
    },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, (v) =>
    onSubmit({ weekday: Number(v.weekday), opens_at: v.opens_at, closes_at: v.closes_at }),
  );

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
                  <option value="" disabled>
                    Elegí un día
                  </option>
                  {WEEKDAYS.map((d) => (
                    <option key={d.value} value={String(d.value)}>
                      {d.label}
                    </option>
                  ))}
                </NativeSelect>
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <div className="grid grid-cols-2 gap-3">
          <FormField
            control={form.control}
            name="opens_at"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Abre</FormLabel>
                <FormControl>
                  <Input type="time" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="closes_at"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Cierra</FormLabel>
                <FormControl>
                  <Input type="time" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
        <FormActions
          formError={formError}
          isSubmitting={form.formState.isSubmitting}
          submitLabel={submitLabel ?? (businessHour ? "Guardar cambios" : "Agregar horario")}
          onCancel={onCancel}
        />
      </form>
    </Form>
  );
}
