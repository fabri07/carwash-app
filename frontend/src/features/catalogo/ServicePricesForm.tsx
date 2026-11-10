"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Form } from "@/components/ui/form";
import { PriceFields } from "@/features/catalogo/PriceFields";
import { DialogFormFooter, TextField } from "@/features/configuracion/fields";
import { bpsToPercent } from "@/lib/percent";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import type { Service, VehicleSize } from "@/types/api";
import { servicePricesSchema, type ServicePricesInput } from "@/validation/services";

interface ServicePricesFormProps {
  service: Service;
  sizes: VehicleSize[];
  onSubmit: (values: ServicePricesInput) => Promise<void>;
  onCancel: () => void;
}

/**
 * Precios, modalidad y seña propia de un servicio (`PRECIOS_EDITAR`). Siempre se mandan los de todos los tamaños
 * vivos: los que faltan (un tamaño creado después) arrancan vacíos y hay que completarlos.
 */
export function ServicePricesForm({ service, sizes, onSubmit, onCancel }: ServicePricesFormProps) {
  const form = useForm<ServicePricesInput>({
    resolver: zodResolver(servicePricesSchema),
    defaultValues: {
      pricing_mode: service.pricing_mode,
      deposit_percent:
        service.deposit_bps == null
          ? ""
          : String(bpsToPercent(service.deposit_bps)).replace(".", ","),
      prices: sizes.map((s) => {
        const current = service.prices.find((p) => p.vehicle_size_id === s.id);
        return {
          vehicle_size_id: s.id,
          price_cents: current?.price_cents,
          duration_min: current ? String(current.duration_min) : "",
        };
      }),
    },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);
  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <TextField
          control={form.control}
          name="deposit_percent"
          label="Seña propia (%) (opcional)"
          inputMode="decimal"
          placeholder="Usa la general"
          hint="Vacío: usa el % de seña del negocio. Solo se cobra si la seña está activa en Configuración."
        />
        <PriceFields sizes={sizes} />
        <DialogFormFooter
          formError={formError}
          submitting={form.formState.isSubmitting}
          submitLabel="Guardar precios"
          onCancel={onCancel}
        />
      </form>
    </Form>
  );
}
