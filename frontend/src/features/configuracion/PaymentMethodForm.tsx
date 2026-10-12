"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Checkbox } from "@/components/ui/checkbox";
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
import type { PaymentMethod, PaymentMethodPayload } from "@/types/api";
import {
  formatDecimal,
  parseDecimal,
  paymentMethodSchema,
  type PaymentMethodInput,
} from "@/validation/configuracion";

interface PaymentMethodFormProps {
  /** Sin medio: alta. Con medio: edición. */
  paymentMethod?: PaymentMethod;
  onSubmit: (payload: PaymentMethodPayload) => Promise<void>;
  onCancel?: () => void;
  submitLabel?: string;
}

/** % con hasta 2 decimales → puntos básicos (3,5 % → 350). */
export function toPaymentMethodPayload(v: PaymentMethodInput): PaymentMethodPayload {
  return {
    code: v.code,
    label: v.label,
    commission_bps: Math.round(parseDecimal(v.commission_percent) * 100),
    // Vacío es "sin cuenta": en una edición, `null` la borra.
    settlement_account: v.settlement_account === "" ? null : v.settlement_account,
    for_income: v.for_income,
    for_expense: v.for_expense,
  };
}

/** Medio de pago (efectivo, transferencia, tarjeta), con su comisión y adónde va la plata. */
export function PaymentMethodForm({
  paymentMethod,
  onSubmit,
  onCancel,
  submitLabel,
}: PaymentMethodFormProps) {
  const form = useForm<PaymentMethodInput>({
    resolver: zodResolver(paymentMethodSchema),
    defaultValues: {
      code: paymentMethod?.code ?? "",
      label: paymentMethod?.label ?? "",
      commission_percent: formatDecimal((paymentMethod?.commission_bps ?? 0) / 100),
      settlement_account: paymentMethod?.settlement_account ?? "",
      for_income: paymentMethod?.for_income ?? true,
      for_expense: paymentMethod?.for_expense ?? false,
    },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, (v) =>
    onSubmit(toPaymentMethodPayload(v)),
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
                <Input autoComplete="off" placeholder="Tarjeta de crédito" {...field} />
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
                  placeholder="CREDITO"
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
          name="commission_percent"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Comisión</FormLabel>
              <div className="flex items-center gap-2">
                <FormControl>
                  <Input inputMode="decimal" autoComplete="off" className="w-28" {...field} />
                </FormControl>
                <span aria-hidden="true" className="text-muted-foreground">
                  %
                </span>
              </div>
              <FormDescription>
                Lo que te descuenta el medio (posnet, billetera). 0 si no cobra.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="settlement_account"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Cuenta de destino (opcional)</FormLabel>
              <FormControl>
                <Input autoComplete="off" placeholder="Caja, Banco, Mercado Pago" {...field} />
              </FormControl>
              <FormDescription>Adónde va la plata que entra por este medio.</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <fieldset className="space-y-1 rounded-lg border p-3">
          <legend className="px-1 text-sm font-semibold">Para qué se usa</legend>
          <FormField
            control={form.control}
            name="for_income"
            render={({ field }) => (
              <label className="flex min-h-touch items-center gap-3 text-sm">
                <Checkbox
                  checked={field.value}
                  onCheckedChange={(checked) => field.onChange(checked === true)}
                />
                Se usa para cobrar
              </label>
            )}
          />
          <FormField
            control={form.control}
            name="for_expense"
            render={({ field }) => (
              <label className="flex min-h-touch items-center gap-3 text-sm">
                <Checkbox
                  checked={field.value}
                  onCheckedChange={(checked) => field.onChange(checked === true)}
                />
                Se usa para pagar gastos
              </label>
            )}
          />
        </fieldset>
        <FormActions
          formError={formError}
          isSubmitting={form.formState.isSubmitting}
          submitLabel={submitLabel ?? (paymentMethod ? "Guardar cambios" : "Crear medio de pago")}
          onCancel={onCancel}
        />
      </form>
    </Form>
  );
}
