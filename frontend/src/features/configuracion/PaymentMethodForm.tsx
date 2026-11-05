"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Form } from "@/components/ui/form";
import { DialogFormFooter, SwitchField, TextField } from "@/features/configuracion/fields";
import type { CatalogFormProps } from "@/features/configuracion/CatalogTab";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import { bpsToPercent } from "@/lib/percent";
import type { PaymentMethod } from "@/types/api";
import { paymentMethodSchema, type PaymentMethodInput } from "@/validation/catalog";

/** Alta y edición de un medio de pago. El código identifica al medio y no se edita. */
export function PaymentMethodForm({
  item,
  onSubmit,
  onCancel,
}: CatalogFormProps<PaymentMethod, PaymentMethodInput>) {
  const form = useForm<PaymentMethodInput>({
    resolver: zodResolver(paymentMethodSchema),
    defaultValues: {
      code: item?.code ?? "",
      label: item?.label ?? "",
      commission_percent: String(item ? bpsToPercent(item.commission_bps) : 0).replace(".", ","),
      settlement_account: item?.settlement_account ?? "",
      for_income: item?.for_income ?? true,
      for_expense: item?.for_expense ?? false,
    },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);
  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <TextField
          control={form.control}
          name="code"
          label="Código"
          hint="Mayúsculas, números o guion bajo. No se puede cambiar después."
          disabled={item !== undefined}
        />
        <TextField control={form.control} name="label" label="Nombre" placeholder="Mercado Pago" />
        <TextField
          control={form.control}
          name="commission_percent"
          label="Comisión (%)"
          inputMode="decimal"
          hint="Lo que se queda el medio de pago de cada cobro. 0 si no cobra comisión."
        />
        <TextField
          control={form.control}
          name="settlement_account"
          label="Cuenta de acreditación"
          hint="Opcional. Dónde llega la plata de este medio."
        />
        <SwitchField control={form.control} name="for_income" label="Sirve para cobrar" />
        <SwitchField control={form.control} name="for_expense" label="Sirve para pagar gastos" />
        <DialogFormFooter
          formError={formError}
          submitting={form.formState.isSubmitting}
          submitLabel={item ? "Guardar cambios" : "Crear medio de pago"}
          onCancel={onCancel}
        />
      </form>
    </Form>
  );
}
