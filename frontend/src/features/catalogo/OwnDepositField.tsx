"use client";

import type { Control, FieldValues, Path } from "react-hook-form";

import { TextField } from "@/features/configuracion/fields";

/**
 * Seña propia del servicio (D4-17), compartida por el alta y el formulario de precios. Vacía usa el %
 * general del negocio.
 */
export function OwnDepositField<T extends FieldValues & { deposit_percent: string }>({
  control,
}: {
  control: Control<T>;
}) {
  return (
    <TextField
      control={control}
      name={"deposit_percent" as Path<T>}
      label="Seña propia (%) (opcional)"
      inputMode="decimal"
      placeholder="Usa la general"
      hint="Vacío: usa el % de seña del negocio. Solo se cobra si la seña está activa en Configuración."
    />
  );
}
