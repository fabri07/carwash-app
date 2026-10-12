"use client";

import type { ReactNode } from "react";
import { useForm, type Control } from "react-hook-form";
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
import type { SettingsPatch, TenantSettings } from "@/types/api";
import {
  bookingRulesSchema,
  formatDecimal,
  parseDecimal,
  type BookingRulesInput,
} from "@/validation/configuracion";

type RuleFields = Pick<
  TenantSettings,
  "tolerance_min" | "late_cancel_threshold_min" | "hold_min" | "min_advance_min"
>;

/** Lo que trae un negocio nuevo (defaults de `tenant_settings`, FASE-4-CONTRATO §3.2). */
const DEFAULTS: RuleFields = {
  tolerance_min: 20,
  late_cancel_threshold_min: 720,
  hold_min: 30,
  min_advance_min: 5,
};

interface BookingRulesFormProps {
  settings?: RuleFields;
  /** Recibe solo las cuatro reglas, en minutos, listas para `PATCH /settings`. */
  onSubmit: (payload: SettingsPatch) => Promise<void>;
  onCancel?: () => void;
  submitLabel?: string;
}

/** La cancelación tardía se muestra en horas y viaja en minutos. */
export function toBookingRulesPatch(v: BookingRulesInput): SettingsPatch {
  return {
    tolerance_min: Number(v.tolerance_min),
    late_cancel_threshold_min: Math.round(parseDecimal(v.late_cancel_hours) * 60),
    hold_min: Number(v.hold_min),
    min_advance_min: Number(v.min_advance_min),
  };
}

function RuleField({
  control,
  name,
  label,
  unit,
  description,
  decimal = false,
}: {
  control: Control<BookingRulesInput>;
  name: keyof BookingRulesInput;
  label: string;
  unit: string;
  description: ReactNode;
  decimal?: boolean;
}) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <FormLabel>{label}</FormLabel>
          <div className="flex items-center gap-2">
            <FormControl>
              <Input
                inputMode={decimal ? "decimal" : "numeric"}
                autoComplete="off"
                className="w-28"
                {...field}
              />
            </FormControl>
            <span aria-hidden="true" className="text-sm text-muted-foreground">
              {unit}
            </span>
          </div>
          <FormDescription>{description}</FormDescription>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

/**
 * Reglas de la agenda: tolerancia de llegada, cuándo una cancelación es
 * tardía, cuánto se guarda un turno esperando la seña y con cuánta
 * anticipación se puede reservar.
 */
export function BookingRulesForm({
  settings = DEFAULTS,
  onSubmit,
  onCancel,
  submitLabel = "Guardar",
}: BookingRulesFormProps) {
  const form = useForm<BookingRulesInput>({
    resolver: zodResolver(bookingRulesSchema),
    defaultValues: {
      tolerance_min: String(settings.tolerance_min),
      late_cancel_hours: formatDecimal(settings.late_cancel_threshold_min / 60),
      hold_min: String(settings.hold_min),
      min_advance_min: String(settings.min_advance_min),
    },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, (v) =>
    onSubmit(toBookingRulesPatch(v)),
  );

  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <RuleField
          control={form.control}
          name="tolerance_min"
          label="Tolerancia de llegada"
          unit="minutos"
          description="Cuánto se espera a un cliente que llega tarde antes de dar el turno por perdido."
        />
        <RuleField
          control={form.control}
          name="late_cancel_hours"
          label="Cancelación tardía"
          unit="horas antes"
          decimal
          description="Si el cliente cancela con menos anticipación que esto, la cancelación cuenta como tardía."
        />
        <RuleField
          control={form.control}
          name="hold_min"
          label="Tiempo para pagar la seña"
          unit="minutos"
          description="Cuánto se guarda el turno esperando la seña. Si no llega, el horario se libera."
        />
        <RuleField
          control={form.control}
          name="min_advance_min"
          label="Anticipación mínima para reservar"
          unit="minutos"
          description="Con 0, se puede reservar un turno que empieza ya."
        />
        <FormActions
          formError={formError}
          isSubmitting={form.formState.isSubmitting}
          submitLabel={submitLabel}
          onCancel={onCancel}
        />
      </form>
    </Form>
  );
}
