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
import type { VehicleSize, VehicleSizePayload } from "@/types/api";
import { vehicleSizeSchema, type VehicleSizeInput } from "@/validation/configuracion";

interface VehicleSizeFormProps {
  /** Sin tamaño: alta. Con tamaño: edición. */
  vehicleSize?: VehicleSize;
  onSubmit: (payload: VehicleSizePayload) => Promise<void>;
  onCancel?: () => void;
  submitLabel?: string;
}

/** Tamaño de vehículo (chico, mediano, camioneta): cada servicio tiene un precio por tamaño. */
export function VehicleSizeForm({
  vehicleSize,
  onSubmit,
  onCancel,
  submitLabel,
}: VehicleSizeFormProps) {
  const form = useForm<VehicleSizeInput>({
    resolver: zodResolver(vehicleSizeSchema),
    defaultValues: {
      code: vehicleSize?.code ?? "",
      label: vehicleSize?.label ?? "",
      sort_order: String(vehicleSize?.sort_order ?? 0),
    },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, (v) =>
    onSubmit({ code: v.code, label: v.label, sort_order: Number(v.sort_order) }),
  );

  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <FormField
          control={form.control}
          name="label"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Nombre</FormLabel>
              <FormControl>
                <Input autoComplete="off" placeholder="Camioneta" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="code"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Código</FormLabel>
              <FormControl>
                <Input
                  autoComplete="off"
                  autoCapitalize="characters"
                  placeholder="CAMIONETA"
                  {...field}
                />
              </FormControl>
              <FormDescription>
                Corto y sin espacios. Se usa en reportes y planillas.
              </FormDescription>
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
          submitLabel={submitLabel ?? (vehicleSize ? "Guardar cambios" : "Crear tamaño")}
          onCancel={onCancel}
        />
      </form>
    </Form>
  );
}
