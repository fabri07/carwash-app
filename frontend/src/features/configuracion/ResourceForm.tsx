"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { FormActions } from "@/features/configuracion/FormActions";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import type { Resource, ResourcePayload } from "@/types/api";
import { resourceSchema, type ResourceInput } from "@/validation/configuracion";

interface ResourceFormProps {
  /** Sin puesto: alta. Con puesto: edición. */
  resource?: Resource;
  onSubmit: (payload: ResourcePayload) => Promise<void>;
  onCancel?: () => void;
  submitLabel?: string;
}

/** Puesto de trabajo (box, rampa, cabina): cada turno ocupa uno. */
export function ResourceForm({ resource, onSubmit, onCancel, submitLabel }: ResourceFormProps) {
  const form = useForm<ResourceInput>({
    resolver: zodResolver(resourceSchema),
    defaultValues: {
      name: resource?.name ?? "",
      sort_order: String(resource?.sort_order ?? 0),
    },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, (v) =>
    onSubmit({ name: v.name, sort_order: Number(v.sort_order) }),
  );

  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <FormField
          control={form.control}
          name="name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Nombre del puesto</FormLabel>
              <FormControl>
                <Input autoComplete="off" placeholder="Box 1" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="sort_order"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Orden en la lista</FormLabel>
              <FormControl>
                <Input inputMode="numeric" autoComplete="off" className="w-28" {...field} />
              </FormControl>
              <FormDescription>Los números más bajos aparecen primero.</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormActions
          formError={formError}
          isSubmitting={form.formState.isSubmitting}
          submitLabel={submitLabel ?? (resource ? "Guardar cambios" : "Crear puesto")}
          onCancel={onCancel}
        />
      </form>
    </Form>
  );
}
