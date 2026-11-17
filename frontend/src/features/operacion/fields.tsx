"use client";

import type { Control, FieldPath, FieldValues } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { cn } from "@/lib/utils";
import { SelectField, TextField } from "@/features/configuracion/fields";
import { formatARS } from "@/lib/money";
import type { Service, VehicleSize } from "@/types/api";
import type { Resource } from "@/types/api";

/**
 * Campos compartidos por las tres cargas a mano. Todo control mide ≥ 44 px (`touch.ts`): los
 * interruptores y las listas de servicios son botones de dos estados, no casillas chicas.
 */
type C<T extends FieldValues> = { control: Control<T> };

/** Cliente ("sin nombre" si queda vacío), teléfono y patente: los tres opcionales. */
export function PersonFields<T extends FieldValues>({ control }: C<T>) {
  const n = (name: string) => name as FieldPath<T>;
  return (
    <div className="space-y-4">
      <TextField
        control={control}
        name={n("customer_name")}
        label="Cliente"
        placeholder="Sin nombre"
        hint="Opcional. Vacío queda como “Sin nombre”."
      />
      <TextField
        control={control}
        name={n("customer_phone")}
        label="Teléfono"
        inputMode="tel"
        placeholder="+54 9 351 000-0000"
        hint="Opcional. Con teléfono, el cliente se reconoce la próxima vez."
      />
      <TextField
        control={control}
        name={n("plate")}
        label="Patente"
        placeholder="AA000AA"
        hint="Opcional."
      />
    </div>
  );
}

/** Tamaño del vehículo y servicios (botones de dos estados). */
export function VehicleFields<T extends FieldValues>({
  control,
  sizes,
  services,
}: C<T> & { sizes: VehicleSize[]; services: Service[] }) {
  return (
    <div className="space-y-4">
      <SelectField
        control={control}
        name={"vehicle_size_id" as FieldPath<T>}
        label="Tamaño del vehículo"
        options={[
          { value: "", label: "Elegí un tamaño" },
          ...sizes.map((s) => ({ value: s.id, label: s.label })),
        ]}
      />
      <FormField
        control={control}
        name={"service_ids" as FieldPath<T>}
        render={({ field }) => {
          const selected = (field.value ?? []) as string[];
          return (
            <FormItem>
              <FormLabel>Servicios</FormLabel>
              <div className="flex flex-wrap gap-2" role="group" aria-label="Servicios">
                {services.length === 0 && (
                  <p className="text-sm text-muted-foreground">No hay servicios con precio fijo.</p>
                )}
                {services.map((s) => {
                  const on = selected.includes(s.id);
                  return (
                    <Button
                      key={s.id}
                      type="button"
                      variant={on ? "default" : "outline"}
                      aria-pressed={on}
                      onClick={() =>
                        field.onChange(
                          on ? selected.filter((x) => x !== s.id) : [...selected, s.id],
                        )
                      }
                    >
                      {s.name}
                    </Button>
                  );
                })}
              </div>
              <FormMessage />
            </FormItem>
          );
        }}
      />
    </div>
  );
}

export function ResourceSelect<T extends FieldValues>({
  control,
  resources,
  required,
}: C<T> & { resources: Resource[]; required?: boolean }) {
  return (
    <SelectField
      control={control}
      name={"resource_id" as FieldPath<T>}
      label={required ? "Puesto" : "Puesto (opcional)"}
      options={[
        { value: "", label: required ? "Elegí un puesto" : "Sin puesto" },
        ...resources.map((r) => ({ value: r.id, label: r.name })),
      ]}
    />
  );
}

/** Interruptor de dos estados con su texto (cobrado / con seña): un botón de 44 px, no un `Switch`. */
export function ToggleField<T extends FieldValues>({
  control,
  name,
  onLabel,
  offLabel,
}: C<T> & { name: FieldPath<T>; onLabel: string; offLabel: string }) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <Button
            type="button"
            variant={field.value ? "default" : "outline"}
            aria-pressed={Boolean(field.value)}
            className={cn("w-full")}
            onClick={() => field.onChange(!field.value)}
          >
            {field.value ? onLabel : offLabel}
          </Button>
        </FormItem>
      )}
    />
  );
}

/** Total de lo elegido con el precio del catálogo de hoy (aviso para quien carga). */
export function priceHint(services: Service[], selected: string[], sizeId: string): string | null {
  if (!sizeId || selected.length === 0) return null;
  let total = 0;
  for (const s of services.filter((x) => selected.includes(x.id))) {
    total += s.prices.find((p) => p.vehicle_size_id === sizeId)?.price_cents ?? 0;
  }
  return total > 0 ? `Total con los precios de hoy: ${formatARS(total)}` : null;
}
