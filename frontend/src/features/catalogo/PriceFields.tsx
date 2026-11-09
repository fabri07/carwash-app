"use client";

import { useFormContext, useWatch, type FieldValues } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { MODE_OPTIONS } from "@/features/catalogo/format";
import { MoneyField, SelectField, TextField } from "@/features/configuracion/fields";
import { formatDuration } from "@/lib/time";
import type { VehicleSize } from "@/types/api";

interface PriceFieldsProps {
  /** Tamaños vivos del negocio, en orden: una fila de precio por cada uno (A8). */
  sizes: VehicleSize[];
  /** Prefijo del formulario donde viven `pricing_mode` y `prices` (`""` o `items.0`). */
  basePath?: string;
}

interface Row {
  price_cents?: number | null;
  duration_min?: string;
}

/**
 * Modalidad y precio + duración por tamaño de vehículo, dentro de un `<Form>`. Todos los tamaños son
 * obligatorios: el backend rechaza (422) un servicio sin precio para alguno.
 */
export function PriceFields({ sizes, basePath = "" }: PriceFieldsProps) {
  const form = useFormContext<FieldValues>();
  const path = (rest: string) => (basePath ? `${basePath}.${rest}` : rest);
  const rows = useWatch({ control: form.control, name: path("prices") }) as Row[] | undefined;

  /**
   * Completa con los valores del primer tamaño SOLO los campos vacíos de los demás: nunca pisa lo que
   * ya se cargó, y no copia lo que el primero tampoco tiene. Valida los campos tocados para limpiar
   * sus errores.
   */
  function fillEmptyFromFirst() {
    const first = form.getValues(path("prices.0")) as Row;
    sizes.forEach((_, i) => {
      if (i === 0) return;
      const row = form.getValues(path(`prices.${i}`)) as Row;
      if (first.price_cents != null && row.price_cents == null) {
        form.setValue(path(`prices.${i}.price_cents`), first.price_cents, {
          shouldDirty: true,
          shouldValidate: true,
        });
      }
      if (first.duration_min && !row.duration_min) {
        form.setValue(path(`prices.${i}.duration_min`), first.duration_min, {
          shouldDirty: true,
          shouldValidate: true,
        });
      }
    });
  }

  return (
    <div className="space-y-4">
      <SelectField
        control={form.control}
        name={path("pricing_mode")}
        label="Modalidad"
        options={MODE_OPTIONS}
        hint="«A cotizar»: el precio de cada tamaño es una referencia («desde $ X») y se acuerda caso por caso."
      />
      <fieldset className="space-y-3 rounded-lg border p-3">
        <legend className="px-1 text-sm font-medium">Precio y duración por tamaño</legend>
        {sizes.map((size, i) => {
          const minutes = Number(rows?.[i]?.duration_min);
          return (
            <div key={size.id} className="grid gap-3 sm:grid-cols-2">
              <MoneyField
                control={form.control}
                name={path(`prices.${i}.price_cents`)}
                label={`Precio (${size.label})`}
              />
              <TextField
                control={form.control}
                name={path(`prices.${i}.duration_min`)}
                label={`Duración en minutos (${size.label})`}
                inputMode="numeric"
                hint={
                  Number.isInteger(minutes) && minutes > 0 ? formatDuration(minutes) : undefined
                }
              />
            </div>
          );
        })}
        {sizes.length > 1 && (
          <Button type="button" variant="outline" size="sm" onClick={fillEmptyFromFirst}>
            Completar los vacíos con el primero
          </Button>
        )}
      </fieldset>
    </div>
  );
}
