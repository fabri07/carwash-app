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
import { Switch } from "@/components/ui/switch";
import { FormActions } from "@/features/configuracion/FormActions";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import type { SettingsPatch, TenantSettings } from "@/types/api";
import { depositSchema, type DepositInput } from "@/validation/configuracion";

/** Seña sugerida al activarla por primera vez (D4-11). */
export const SUGGESTED_DEPOSIT_BPS = 2000;

interface DepositFormProps {
  settings?: Pick<TenantSettings, "deposit_enabled" | "deposit_bps">;
  /** Recibe solo `deposit_enabled` y `deposit_bps`, listos para `PATCH /settings`. */
  onSubmit: (payload: SettingsPatch) => Promise<void>;
  onCancel?: () => void;
  submitLabel?: string;
}

/** % entero → puntos básicos (20 % → 2000). */
export function toDepositPatch(v: DepositInput): SettingsPatch {
  return { deposit_enabled: v.deposit_enabled, deposit_bps: Number(v.deposit_percent) * 100 };
}

/**
 * Seña global (D4-4): un interruptor para todos los servicios y un único %.
 * El % se conserva con la seña apagada y se puede editar igual: así, al
 * volver a activarla, queda el que el dueño había elegido.
 */
export function DepositForm({
  settings,
  onSubmit,
  onCancel,
  submitLabel = "Guardar",
}: DepositFormProps) {
  const form = useForm<DepositInput>({
    resolver: zodResolver(depositSchema),
    defaultValues: {
      deposit_enabled: settings?.deposit_enabled ?? false,
      deposit_percent: String(Math.round((settings?.deposit_bps ?? SUGGESTED_DEPOSIT_BPS) / 100)),
    },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, (v) => onSubmit(toDepositPatch(v)));

  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <FormField
          control={form.control}
          name="deposit_enabled"
          render={({ field }) => (
            <FormItem className="flex min-h-touch items-center justify-between gap-4 space-y-0">
              <div className="space-y-1">
                <FormLabel>Pedir seña al reservar</FormLabel>
                <FormDescription>
                  Con la seña activa, el auto se recibe igual aunque no se haya pagado: lo pendiente
                  queda en el saldo.
                </FormDescription>
              </div>
              <FormControl>
                <Switch checked={field.value} onCheckedChange={field.onChange} />
              </FormControl>
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="deposit_percent"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Porcentaje de la seña</FormLabel>
              <div className="flex items-center gap-2">
                <FormControl>
                  <Input inputMode="numeric" autoComplete="off" className="w-28" {...field} />
                </FormControl>
                <span aria-hidden="true" className="text-muted-foreground">
                  %
                </span>
              </div>
              <FormDescription>
                Del precio de cada servicio. Se guarda aunque la seña esté apagada.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
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
