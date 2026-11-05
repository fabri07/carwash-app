"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Button } from "@/components/ui/button";
import { Form } from "@/components/ui/form";
import { SwitchField, TextField } from "@/features/configuracion/fields";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import { bpsToPercent } from "@/lib/percent";
import type { BusinessSettings } from "@/types/api";
import { depositSchema, type DepositInput } from "@/validation/settings";

interface DepositFormProps {
  settings: BusinessSettings;
  onSubmit: (values: DepositInput) => Promise<void>;
}

/**
 * Seña global y reglas de las reservas (D4-4). El porcentaje de seña cambia solo las
 * reservas futuras: cada turno guarda el que regía cuando se hizo.
 */
export function DepositForm({ settings, onSubmit }: DepositFormProps) {
  const form = useForm<DepositInput>({
    resolver: zodResolver(depositSchema),
    defaultValues: {
      deposit_enabled: settings.deposit_enabled,
      deposit_percent: String(bpsToPercent(settings.deposit_bps)).replace(".", ","),
      hold_min: String(settings.hold_min),
      tolerance_min: String(settings.tolerance_min),
      late_cancel_threshold_min: String(settings.late_cancel_threshold_min),
      min_advance_min: String(settings.min_advance_min),
      quote_validity_hours: String(settings.quote_validity_hours),
    },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);
  const depositOn = form.watch("deposit_enabled");
  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="max-w-2xl space-y-4">
        <SwitchField
          control={form.control}
          name="deposit_enabled"
          label="Pedir seña al reservar"
          hint="Si está apagada, los turnos se confirman sin pago previo."
        />
        <TextField
          control={form.control}
          name="deposit_percent"
          label="Porcentaje de seña (%)"
          inputMode="decimal"
          disabled={!depositOn}
          hint="Sobre el precio de cada servicio. Cambiarlo no afecta los turnos ya reservados."
        />
        <TextField
          control={form.control}
          name="hold_min"
          label="Minutos que se guarda el turno esperando la seña"
          inputMode="numeric"
        />
        <TextField
          control={form.control}
          name="min_advance_min"
          label="Anticipación mínima para reservar (minutos)"
          inputMode="numeric"
          hint="0 permite reservar para ahora mismo."
        />
        <TextField
          control={form.control}
          name="tolerance_min"
          label="Tolerancia de llegada (minutos)"
          inputMode="numeric"
          hint="Pasado ese tiempo sin que llegue el cliente, se puede cancelar por demora."
        />
        <TextField
          control={form.control}
          name="late_cancel_threshold_min"
          label="Cancelación tardía: minutos antes del turno"
          inputMode="numeric"
          hint="Cancelar con menos anticipación que esto se considera tardío."
        />
        <TextField
          control={form.control}
          name="quote_validity_hours"
          label="Vigencia de una cotización (horas)"
          inputMode="numeric"
        />
        {formError && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {formError}
          </p>
        )}
        <Button type="submit" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? "Guardando…" : "Guardar cambios"}
        </Button>
      </form>
    </Form>
  );
}
